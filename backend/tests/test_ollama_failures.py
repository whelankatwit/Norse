"""Ollama failures are classified and logged without user content."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.agent.loop import ToolTurn
from app.agent.memory import ExtractionError
from app.agent.ollama import OllamaFailure, complete
from app.api.conversations import create_message_route
from app.database.sqlite import create_conversation, init_db, list_messages
from app.models.conversations import MessageCreate

SENTINEL = "user-secret-7f3a9c2e"


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.request = httpx.Request("POST", "http://localhost:11434/api/chat")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "upstream",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _client(result: object):
    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def post(self, url, json):
            if isinstance(result, Exception):
                raise result
            return result

    return Client


class CompleteFailureTests(unittest.IsolatedAsyncioTestCase):
    async def _fail(self, result: object) -> tuple[OllamaFailure, str]:
        with (
            patch("app.agent.ollama.httpx.AsyncClient", _client(result)),
            self.assertLogs("uvicorn.error", level="ERROR") as logs,
        ):
            with self.assertRaises(OllamaFailure) as caught:
                await complete(
                    [{"role": "user", "content": SENTINEL}],
                    stage="tool_round",
                )
        return caught.exception, "\n".join(logs.output)

    async def test_empty_reply_is_classified_without_user_content(self) -> None:
        failure, log = await self._fail(
            _Response(200, {"model": "granite4:3b", "message": {"role": "assistant", "content": ""}, "echo": SENTINEL})
        )
        self.assertEqual(failure.code, "empty_reply")
        self.assertIn("code=empty_reply", log)
        self.assertIn("stage=tool_round", log)
        self.assertIn("response_model=granite4:3b", log)
        self.assertIn("content_empty=true", log)
        self.assertIn("tool_calls=missing", log)
        self.assertNotIn(SENTINEL, log)

    async def test_malformed_response_is_classified_without_user_content(self) -> None:
        failure, log = await self._fail(_Response(200, {"detail": SENTINEL}))
        self.assertEqual(failure.code, "invalid_ollama_response")
        self.assertIn("code=invalid_ollama_response", log)
        self.assertNotIn(SENTINEL, log)

    async def test_invalid_json_does_not_log_the_parser_message(self) -> None:
        failure, log = await self._fail(_Response(200, ValueError(SENTINEL)))
        self.assertEqual(failure.code, "invalid_ollama_response")
        self.assertIn("error_type=ValueError", log)
        self.assertNotIn(SENTINEL, log)

    async def test_upstream_http_error_logs_the_status_only(self) -> None:
        failure, log = await self._fail(_Response(500, {"error": SENTINEL}))
        self.assertEqual(failure.code, "ollama_http_error")
        self.assertIn("upstream_status=500", log)
        self.assertNotIn(SENTINEL, log)

    async def test_timeout_and_connection_failure(self) -> None:
        timeout, timeout_log = await self._fail(httpx.ReadTimeout(SENTINEL))
        unavailable, unavailable_log = await self._fail(httpx.ConnectError(SENTINEL))
        self.assertEqual(timeout.code, "ollama_timeout")
        self.assertEqual(unavailable.code, "ollama_unavailable")
        self.assertIn("code=ollama_timeout", timeout_log)
        self.assertIn("code=ollama_unavailable", unavailable_log)
        self.assertNotIn(SENTINEL, timeout_log)
        self.assertNotIn(SENTINEL, unavailable_log)

    async def test_missing_model_is_a_503_code(self) -> None:
        failure, log = await self._fail(_Response(404, {"error": SENTINEL}))
        self.assertEqual(failure.code, "ollama_model_not_found")
        self.assertIn("upstream_status=404", log)
        self.assertNotIn(SENTINEL, log)

    async def test_newline_in_response_model_stays_on_one_line(self) -> None:
        hostile = "granite4:3b\nollama failure code=owned"
        failure, log = await self._fail(
            _Response(200, {"model": hostile, "message": {"role": "assistant", "content": ""}})
        )
        self.assertEqual(failure.code, "empty_reply")
        self.assertEqual(log.splitlines(), [log])
        self.assertNotIn("code=owned", log)
        self.assertNotIn(hostile, log)
        self.assertIn("response_model= ", log)

    async def test_ansi_response_model_is_omitted(self) -> None:
        hostile = "granite\x1b[31mEVIL"
        failure, log = await self._fail(
            _Response(
                200,
                {"model": hostile, "message": {"role": "assistant", "content": ""}, "echo": SENTINEL},
            )
        )
        self.assertEqual(failure.code, "empty_reply")
        self.assertEqual(log.splitlines(), [log])
        self.assertNotIn("\x1b", log)
        self.assertNotIn("[31m", log)
        self.assertNotIn("EVIL", log)
        self.assertNotIn(SENTINEL, log)
        self.assertIn("response_model= ", log)


class RouteFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "norse.db"
        self.database = patch("app.database.sqlite.DATABASE_PATH", database)
        self.database.start()
        init_db()
        self.conversation_id = create_conversation("diag")["id"]

    def tearDown(self) -> None:
        self.database.stop()
        self.temporary.cleanup()

    async def test_empty_reply_saves_nothing(self) -> None:
        with (
            patch("app.agent.loop.complete", AsyncMock(return_value={"role": "assistant", "content": ""})),
            self.assertLogs("uvicorn.error", level="ERROR") as logs,
        ):
            with self.assertRaises(HTTPException) as caught:
                await create_message_route(
                self.conversation_id,
                MessageCreate(message=SENTINEL, idempotency_key="diag-empty"),
            )
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(caught.exception.detail["code"], "empty_reply")
        self.assertEqual(list_messages(self.conversation_id), [])
        self.assertNotIn(SENTINEL, "\n".join(logs.output))

    async def test_extraction_failure_keeps_the_saved_reply(self) -> None:
        with (
            patch("app.api.conversations.run_tools", AsyncMock(return_value=ToolTurn("The reply."))),
            patch(
                "app.api.conversations.extract_memories",
                AsyncMock(side_effect=ExtractionError(SENTINEL)),
            ),
            self.assertLogs("uvicorn.error", level="ERROR") as logs,
        ):
            exchange = await create_message_route(
                self.conversation_id,
                MessageCreate(message=SENTINEL, idempotency_key="diag-extract"),
            )
        self.assertEqual(exchange.assistant_message.content, "The reply.")
        self.assertEqual(len(list_messages(self.conversation_id)), 2)
        log = "\n".join(logs.output)
        self.assertIn("code=extraction_failed", log)
        self.assertIn("stage=memory_extraction", log)
        self.assertNotIn(SENTINEL, log)

    async def test_extraction_timeout_keeps_the_saved_reply(self) -> None:
        await self._extraction_transport_failure(httpx.ReadTimeout(SENTINEL), "ollama_timeout")

    async def test_extraction_connection_failure_keeps_the_saved_reply(self) -> None:
        await self._extraction_transport_failure(httpx.ConnectError(SENTINEL), "ollama_unavailable")

    async def _extraction_transport_failure(self, error: Exception, code: str) -> None:
        with (
            patch("app.api.conversations.run_tools", AsyncMock(return_value=ToolTurn("The reply."))),
            patch("app.agent.ollama.httpx.AsyncClient", _client(error)),
            self.assertLogs("uvicorn.error", level="ERROR") as logs,
        ):
            exchange = await create_message_route(
                self.conversation_id,
                MessageCreate(message=SENTINEL, idempotency_key="diag-extract"),
            )
        self.assertEqual(exchange.assistant_message.content, "The reply.")
        messages = list_messages(self.conversation_id)
        self.assertEqual(
            [(message["role"], message["content"]) for message in messages],
            [("user", SENTINEL), ("assistant", "The reply.")],
        )
        log = "\n".join(logs.output)
        self.assertIn(f"code={code}", log)
        self.assertIn("stage=memory_extraction", log)
        self.assertNotIn(SENTINEL, log)
        self.assertEqual(log.splitlines(), [log])


if __name__ == "__main__":
    unittest.main()
