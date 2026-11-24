"""Native tool calls stay inside the temporary model conversation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agent.loop import run_tools
from app.api.chat import ChatRequest, chat_message
from app.models.conversations import MessageExchange
from app.tools.registry import Tool, execute, report


class LoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_tool_call_is_replayed_and_not_saved_as_text(self) -> None:
        script = _Script(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "list_workspaces", "arguments": {}}}
                    ],
                },
                {"role": "assistant", "content": "No workspaces are approved."},
            ]
        )

        reply, uses = await run_tools(
            [{"role": "user", "content": "What can you see?"}],
            complete_model=script,
        )

        self.assertEqual(reply, "No workspaces are approved.")
        self.assertEqual(uses, [{"name": "list_workspaces", "ok": True}])
        replay = script.seen[1]["messages"]
        self.assertIn("tool_calls", replay[-2])
        self.assertEqual(replay[-1]["role"], "tool")
        self.assertEqual(replay[-1]["tool_name"], "list_workspaces")
        self.assertNotIn("tool", {message["role"] for message in script.seen[0]["messages"]})

    async def test_json_in_content_is_not_a_tool_call(self) -> None:
        script = _Script(
            [
                {
                    "role": "assistant",
                    "content": '{"name": "list_workspaces", "arguments": {}}',
                }
            ]
        )
        called = []

        reply, uses = await run_tools(
            [{"role": "user", "content": "Look around."}],
            complete_model=script,
            execute_tool=lambda name, arguments: called.append(name) or ({"ok": True}, True),
        )

        self.assertEqual(called, [])
        self.assertEqual(uses, [])
        self.assertTrue(reply.startswith("{"))

    async def test_call_limit_ends_without_tools(self) -> None:
        async def complete(messages, tools):
            complete.seen.append(tools)
            if tools is None:
                return {"role": "assistant", "content": "Stopped."}
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "list_workspaces", "arguments": {}}}
                ],
            }

        complete.seen = []
        reply, uses = await run_tools(
            [{"role": "user", "content": "Keep looking."}],
            complete_model=complete,
            execute_tool=lambda name, arguments: ({"ok": True}, True),
        )
        self.assertEqual(reply, "Stopped.")
        self.assertEqual(len(uses), 4)
        self.assertIsNone(complete.seen[-1])

    async def test_eight_calls_stop_before_another_tool_round(self) -> None:
        calls = [
            {"function": {"name": "list_workspaces", "arguments": {}}}
            for _ in range(9)
        ]

        async def complete(messages, tools):
            complete.seen.append({"messages": list(messages), "tools": tools})
            if tools is None:
                return {"role": "assistant", "content": "Done."}
            return {"role": "assistant", "content": "", "tool_calls": calls}

        complete.seen = []
        reply, uses = await run_tools(
            [{"role": "user", "content": "Look at everything."}],
            complete_model=complete,
            execute_tool=lambda name, arguments: ({"ok": True}, True),
        )
        self.assertEqual(reply, "Done.")
        self.assertEqual(len(uses), 8)
        self.assertIsNone(complete.seen[-1]["tools"])
        replayed = complete.seen[1]["messages"]
        self.assertEqual(len(replayed[1]["tool_calls"]), 8)
        self.assertEqual(sum(message["role"] == "tool" for message in replayed), 8)

    async def test_post_chat_does_not_offer_tools(self) -> None:
        with patch("app.api.chat.chat", new_callable=AsyncMock) as chat:
            chat.return_value = "hello"
            response = await chat_message(ChatRequest(message="hello"))
        chat.assert_awaited_once_with([{"role": "user", "content": "hello"}])
        self.assertEqual(response.message, "hello")

    def test_unknown_and_approval_tools_are_not_invoked(self) -> None:
        ran = []
        approve = Tool(
            "write_file",
            "Write a file.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            "approve",
            lambda arguments: ran.append(arguments) or {"ok": True},
        )
        unknown, unknown_invoked = execute("missing", {})
        refused, refused_invoked = execute("write_file", {}, tools=(approve,))
        self.assertEqual(unknown["error"], "unknown tool")
        self.assertFalse(unknown_invoked)
        self.assertEqual(refused["error"], "approval required")
        self.assertFalse(refused_invoked)
        self.assertEqual(ran, [])
        invalid, invalid_invoked = execute(
            "read_file",
            {"workspace": "repo", "path": "src/app.txt", "raw": "C:/secret"},
        )
        self.assertTrue(invalid_invoked)
        self.assertEqual(invalid["error"], "invalid arguments")
        boolean_limit, limit_invoked = execute(
            "git_log",
            {"workspace": "repo", "limit": True},
        )
        self.assertTrue(limit_invoked)
        self.assertEqual(boolean_limit["error"], "invalid arguments")

    def test_successful_relative_path_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "app.txt").write_text("hello\n", encoding="utf-8")
            with patch("app.tools.workspace.WORKSPACES", str(root)):
                result, invoked = execute(
                    "read_file",
                    {"workspace": root.name, "path": "src/app.txt"},
                )
        self.assertTrue(invoked)
        self.assertTrue(result["ok"])
        reported = report(
            "read_file",
            {"workspace": root.name, "path": "src/app.txt"},
            result,
        )
        self.assertEqual(
            reported,
            {
                "name": "read_file",
                "ok": True,
                "workspace": root.name,
                "path": "src/app.txt",
            },
        )

    def test_failed_sensitive_path_is_reported_without_the_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            with patch("app.tools.workspace.WORKSPACES", str(root)):
                result, invoked = execute(
                    "read_file",
                    {"workspace": root.name, "path": ".env"},
                )
        self.assertTrue(invoked)
        self.assertEqual(result["ok"], False)
        reported = report(
            "read_file",
            {"workspace": root.name, "path": ".env"},
            result,
        )
        self.assertEqual(reported, {"name": "read_file", "ok": False})
        self.assertNotIn("secret", str(reported))


class _Script:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages
        self.seen: list[dict] = []

    async def __call__(self, messages, tools):
        self.seen.append({"messages": list(messages), "tools": tools})
        return self.messages.pop(0)


class ResponseShapeTests(unittest.TestCase):
    def test_exchange_includes_tool_uses(self) -> None:
        self.assertIn("tool_uses", MessageExchange.model_fields)


if __name__ == "__main__":
    unittest.main()
