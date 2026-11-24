"""Submission keys claim one model run and keep a single saved exchange."""

import asyncio
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.agent.loop import ToolTurn
from app.agent.memory import ExtractionResult
from app.agent.ollama import OllamaFailure
from app.api.conversations import create_message_route
from app.database.sqlite import (
    claim_submission,
    connect,
    create_conversation,
    delete_conversation,
    fingerprint_message,
    init_db,
    list_messages,
)
from app.models.conversations import MessageCreate

SENTINEL = "user-secret-7f3a9c2e"


class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "norse.db"
        self.database = patch("app.database.sqlite.DATABASE_PATH", database)
        self.database.start()
        init_db()
        self.conversation_id = create_conversation("submission")["id"]

    def tearDown(self) -> None:
        self.database.stop()
        self.temporary.cleanup()

    @contextmanager
    def _quiet(self, tools: object | None = None):
        tool_target = tools if tools is not None else AsyncMock(return_value=ToolTurn("The reply."))
        with (
            patch("app.api.conversations.run_tools", tool_target) as tool_mock,
            patch(
                "app.api.conversations.extract_memories",
                AsyncMock(return_value=ExtractionResult(add=[], conflicts=[])),
            ) as extract_mock,
        ):
            yield tool_mock, extract_mock

    def _age(self, minutes: int) -> None:
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        with connect() as connection:
            connection.execute("UPDATE submission_keys SET updated_at = ?", (stamp,))

    def _row(self, conversation_id: str | None = None) -> dict[str, str | None]:
        with connect() as connection:
            row = connection.execute(
                """
                SELECT submission_key, fingerprint, status, owner, updated_at
                FROM submission_keys
                WHERE conversation_id = ?
                """,
                (conversation_id or self.conversation_id,),
            ).fetchone()
            return dict(row)

    def _submission_count(self) -> int:
        with connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM submission_keys").fetchone()[0]

    async def _send(
        self,
        message: str,
        key: str,
        *,
        recover: bool = False,
        conversation_id: str | None = None,
    ):
        return await create_message_route(
            conversation_id or self.conversation_id,
            MessageCreate(message=message, idempotency_key=key, recover=recover),
        )

    async def test_a_fresh_pending_request_does_not_start_another_model_call(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def tools(messages):
            started.set()
            await release.wait()
            return ToolTurn("The reply.")

        with self._quiet(tools) as (_tool_mock, _extract):
            first = asyncio.create_task(self._send("Hello", "same-key"))
            await asyncio.wait_for(started.wait(), timeout=5)
            with self.assertRaises(HTTPException) as caught:
                await self._send("Hello", "same-key")
            release.set()
            exchange = await first
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "request_in_progress")
        self.assertFalse(exchange.replayed)
        self.assertEqual(len(list_messages(self.conversation_id)), 2)

    async def test_a_completed_key_is_replayed_without_the_model(self) -> None:
        with self._quiet() as (tools, extract):
            first = await self._send(SENTINEL, "same-key")
            tools.reset_mock()
            extract.reset_mock()
            with self.assertNoLogs("uvicorn.error", level="ERROR"):
                second = await self._send(SENTINEL, "same-key")
                recovered = await self._send(SENTINEL, "same-key", recover=True)
        self.assertTrue(second.replayed)
        self.assertTrue(recovered.replayed)
        self.assertEqual(second.user_message.id, first.user_message.id)
        self.assertEqual(second.assistant_message.id, first.assistant_message.id)
        self.assertEqual(recovered.assistant_message.content, "The reply.")
        self.assertEqual(second.tool_uses, [])
        self.assertEqual(second.tool_results, [])
        self.assertEqual(second.memories_saved, [])
        self.assertEqual(len(list_messages(self.conversation_id)), 2)
        tools.assert_not_awaited()
        extract.assert_not_awaited()

    async def test_a_different_fingerprint_conflicts(self) -> None:
        with self._quiet() as (tools, _extract):
            await self._send("Hello", "same-key")
            with self.assertNoLogs("uvicorn.error", level="ERROR"):
                with self.assertRaises(HTTPException) as caught:
                    await self._send(SENTINEL, "same-key")
                with self.assertRaises(HTTPException) as recovered:
                    await self._send(SENTINEL, "same-key", recover=True)
        self.assertEqual(caught.exception.detail["code"], "idempotency_conflict")
        self.assertEqual(recovered.exception.detail["code"], "idempotency_conflict")
        self.assertEqual(len(list_messages(self.conversation_id)), 2)
        self.assertEqual(tools.await_count, 1)
        self.assertNotIn(SENTINEL, str(caught.exception.detail))

    async def test_reclaim_waits_until_fifteen_minutes(self) -> None:
        claim_submission(
            self.conversation_id,
            "same-key",
            fingerprint_message("Hello"),
            recover=False,
        )
        self._age(14)
        with self._quiet() as (tools, extract):
            with self.assertRaises(HTTPException) as caught:
                await self._send("Hello", "same-key")
            self._age(16)
            exchange = await self._send("Hello", "same-key")
        self.assertEqual(caught.exception.detail["code"], "request_in_progress")
        self.assertEqual(exchange.assistant_message.content, "The reply.")
        self.assertEqual(tools.await_count, 1)
        self.assertEqual(extract.await_count, 1)
        self.assertEqual(len(list_messages(self.conversation_id)), 2)

    async def test_a_stale_owner_cannot_save_or_extract(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = {"n": 0}

        async def tools(messages):
            calls["n"] += 1
            if calls["n"] == 1:
                started.set()
                await release.wait()
                return ToolTurn("old reply")
            return ToolTurn("new reply")

        with self._quiet(tools) as (_tools, extract), self.assertNoLogs("uvicorn.error", level="ERROR"):
            first = asyncio.create_task(self._send(SENTINEL, "same-key"))
            await asyncio.wait_for(started.wait(), timeout=5)
            self._age(16)
            second = await self._send(SENTINEL, "same-key")
            release.set()
            with self.assertRaises(HTTPException) as caught:
                await first
        self.assertEqual(caught.exception.detail["code"], "request_in_progress")
        self.assertEqual(second.assistant_message.content, "new reply")
        self.assertFalse(second.replayed)
        self.assertEqual(
            [message["content"] for message in list_messages(self.conversation_id)],
            [SENTINEL, "new reply"],
        )
        self.assertEqual(extract.await_count, 1)

    async def test_a_stale_owner_cannot_mark_the_new_request_failed(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        second_started = asyncio.Event()
        second_release = asyncio.Event()
        calls = {"n": 0}

        async def tools(messages):
            calls["n"] += 1
            if calls["n"] == 1:
                started.set()
                await release.wait()
                raise OllamaFailure("empty_reply", stage="tool_round")
            second_started.set()
            await second_release.wait()
            return ToolTurn("new reply")

        with self._quiet(tools) as (_tools, extract), self.assertLogs("uvicorn.error", level="ERROR") as logs:
            first = asyncio.create_task(self._send(SENTINEL, "same-key"))
            await asyncio.wait_for(started.wait(), timeout=5)
            self._age(16)
            second = asyncio.create_task(self._send(SENTINEL, "same-key"))
            await asyncio.wait_for(second_started.wait(), timeout=5)
            owner = self._row()["owner"]
            release.set()
            with self.assertRaises(HTTPException) as caught:
                await first
            row = self._row()
            self.assertEqual(list_messages(self.conversation_id), [])
            second_release.set()
            exchange = await second
        self.assertNotIn(SENTINEL, "\n".join(logs.output))
        self.assertEqual(caught.exception.detail["code"], "request_in_progress")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["owner"], owner)
        self.assertEqual(exchange.assistant_message.content, "new reply")
        self.assertEqual(len(list_messages(self.conversation_id)), 2)
        self.assertEqual(extract.await_count, 1)

    async def test_a_failed_key_can_be_retried_by_an_execution_request(self) -> None:
        outcomes = [
            OllamaFailure("empty_reply", stage="tool_round"),
            ToolTurn("The reply."),
        ]

        async def tools(messages):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with self._quiet(tools):
            with self.assertRaises(HTTPException) as failed:
                await self._send("Hello", "same-key")
            exchange = await self._send("Hello", "same-key")
        self.assertEqual(failed.exception.detail["code"], "empty_reply")
        self.assertEqual(exchange.assistant_message.content, "The reply.")
        self.assertFalse(exchange.replayed)
        self.assertEqual(len(list_messages(self.conversation_id)), 2)

    async def test_recovery_does_not_change_a_pending_submission(self) -> None:
        claim_submission(
            self.conversation_id,
            "same-key",
            fingerprint_message("Hello"),
            recover=False,
        )
        before = self._row()
        with self._quiet() as (tools, extract):
            with self.assertRaises(HTTPException) as pending:
                await self._send("Hello", "same-key", recover=True)
        after = self._row()
        self.assertEqual(pending.exception.detail["code"], "request_in_progress")
        self.assertEqual(after["owner"], before["owner"])
        self.assertEqual(after["updated_at"], before["updated_at"])
        self.assertEqual(after["status"], "pending")
        tools.assert_not_awaited()
        extract.assert_not_awaited()
        self.assertEqual(list_messages(self.conversation_id), [])

    async def test_recovery_reports_failure_without_running_the_model_again(self) -> None:
        calls = {"n": 0}

        async def tools(messages):
            calls["n"] += 1
            raise OllamaFailure("empty_reply", stage="tool_round")

        with self._quiet(tools) as (_tools, extract):
            with self.assertRaises(HTTPException) as failed_run:
                await self._send("Hello", "same-key")
            before = self._row()
            with self.assertRaises(HTTPException) as recovered:
                await self._send("Hello", "same-key", recover=True)
        after = self._row()
        self.assertEqual(failed_run.exception.detail["code"], "empty_reply")
        self.assertEqual(recovered.exception.detail["code"], "request_failed")
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["owner"], before["owner"])
        self.assertEqual(after["updated_at"], before["updated_at"])
        self.assertEqual(calls["n"], 1)
        extract.assert_not_awaited()
        self.assertEqual(list_messages(self.conversation_id), [])

    async def test_deleting_a_conversation_removes_its_submission(self) -> None:
        with self._quiet():
            await self._send("Hello", "same-key")
        self.assertEqual(self._submission_count(), 1)
        self.assertTrue(delete_conversation(self.conversation_id))
        self.assertEqual(self._submission_count(), 0)
        self.assertEqual(list_messages(self.conversation_id), [])

    async def test_the_same_key_in_another_conversation_is_independent(self) -> None:
        other = create_conversation("other")["id"]
        with self._quiet() as (tools, _extract):
            first = await self._send("Hello", "same-key")
            second = await self._send("Hello", "same-key", conversation_id=other)
        self.assertFalse(first.replayed)
        self.assertFalse(second.replayed)
        self.assertEqual(len(list_messages(self.conversation_id)), 2)
        self.assertEqual(len(list_messages(other)), 2)
        self.assertEqual(tools.await_count, 2)
        self.assertEqual(self._submission_count(), 2)

    def test_an_idempotency_key_cannot_contain_whitespace(self) -> None:
        with self.assertRaises(ValidationError):
            MessageCreate(message="Hello", idempotency_key="not a token")

if __name__ == "__main__":
    unittest.main()
