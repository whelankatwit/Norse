"""Native tool calls stay inside the temporary model conversation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agent.loop import run_tools
from app.api.chat import ChatRequest, chat_message
from app.models.conversations import MessageExchange
from app.tools.registry import Tool, execute, report, result_text


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

        turn = await run_tools(
            [{"role": "user", "content": "What can you see?"}],
            complete_model=script,
        )

        self.assertEqual(turn.reply, "No workspaces are approved.")
        self.assertEqual(turn.tool_uses, [{"name": "list_workspaces", "ok": True}])
        self.assertEqual(turn.tool_requests, [])
        self.assertNotIn("No workspaces are approved.", turn.tool_results[0]["output"])
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

        turn = await run_tools(
            [{"role": "user", "content": "Look around."}],
            complete_model=script,
            execute_tool=lambda name, arguments: called.append(name) or ({"ok": True}, True),
        )

        self.assertEqual(called, [])
        self.assertEqual(turn.tool_uses, [])
        self.assertEqual(turn.tool_results, [])
        self.assertTrue(turn.reply.startswith("{"))

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
        turn = await run_tools(
            [{"role": "user", "content": "Keep looking."}],
            complete_model=complete,
            execute_tool=lambda name, arguments: ({"ok": True}, True),
        )
        self.assertEqual(turn.reply, "Stopped.")
        self.assertEqual(len(turn.tool_uses), 4)
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
        turn = await run_tools(
            [{"role": "user", "content": "Look at everything."}],
            complete_model=complete,
            execute_tool=lambda name, arguments: ({"ok": True}, True),
        )
        self.assertEqual(turn.reply, "Done.")
        self.assertEqual(len(turn.tool_uses), 8)
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


    async def test_explicit_git_log_without_a_call_discards_invented_history(self) -> None:
        script = _Script(
            [
                {
                    "role": "assistant",
                    "content": "feat: Add new feature to Norse\nfix: Resolve issue in backend",
                }
            ]
        )
        turn = await run_tools(
            [
                {
                    "role": "user",
                    "content": "Use git_log on workspace ID Norse. Show the five most recent commits.",
                }
            ],
            complete_model=script,
            execute_tool=lambda name, arguments: (_ for _ in ()).throw(AssertionError(name)),
        )
        self.assertEqual(turn.reply, "I couldn't run git_log, so I don't have its result.")
        self.assertEqual(turn.tool_uses, [])
        self.assertEqual(turn.tool_results, [])
        self.assertEqual(turn.tool_requests, [{"name": "git_log", "status": "not_invoked"}])
        self.assertNotIn("feat:", turn.reply)

    async def test_explicit_list_workspaces_without_a_call_is_discarded(self) -> None:
        script = _Script([{"role": "assistant", "content": "The workspace is Norse."}])
        turn = await run_tools(
            [{"role": "user", "content": "Call list_workspaces."}],
            complete_model=script,
        )
        self.assertEqual(turn.reply, "I couldn't run list_workspaces, so I don't have its result.")
        self.assertEqual(turn.tool_requests, [{"name": "list_workspaces", "status": "not_invoked"}])
        self.assertEqual(turn.tool_results, [])

    async def test_successful_native_call_keeps_handler_output(self) -> None:
        script = _Script(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "git_log", "arguments": {"workspace": "Norse", "limit": 5}}}
                    ],
                },
                {"role": "assistant", "content": "feat: Add new feature to Norse"},
            ]
        )

        def execute_tool(name, arguments):
            self.assertEqual(name, "git_log")
            return {"ok": True, "output": "ab82957 Begin to implement tools"}, True

        turn = await run_tools(
            [{"role": "user", "content": "Use git_log on workspace Norse."}],
            complete_model=script,
            execute_tool=execute_tool,
        )
        self.assertEqual(turn.reply, "feat: Add new feature to Norse")
        self.assertEqual(
            turn.tool_results,
            [{"name": "git_log", "output": "ab82957 Begin to implement tools"}],
        )
        self.assertEqual(turn.tool_requests, [{"name": "git_log", "status": "succeeded"}])
        self.assertNotIn("feat:", turn.tool_results[0]["output"])

    async def test_failed_native_call_returns_the_handler_error(self) -> None:
        script = _Script(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "git_log", "arguments": {"workspace": "Norse"}}}
                    ],
                },
                {"role": "assistant", "content": "feat: Add new feature to Norse"},
            ]
        )
        turn = await run_tools(
            [{"role": "user", "content": "Run git_log for Norse."}],
            complete_model=script,
            execute_tool=lambda name, arguments: (
                {"ok": False, "error": "workspace is not approved"},
                True,
            ),
        )
        self.assertEqual(turn.reply, "workspace is not approved")
        self.assertEqual(turn.tool_results, [])
        self.assertEqual(turn.tool_uses, [{"name": "git_log", "ok": False}])
        self.assertEqual(turn.tool_requests, [{"name": "git_log", "status": "failed"}])
        self.assertNotIn("feat:", turn.reply)

    async def test_partial_success_keeps_real_results_and_drops_commentary(self) -> None:
        script = _Script(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "git_log", "arguments": {"workspace": "Norse"}}}
                    ],
                },
                {"role": "assistant", "content": "The status is clean and the log starts with feat: Add."},
            ]
        )

        def execute_tool(name, arguments):
            if name == "git_log":
                return {"ok": True, "output": "ab82957 Begin to implement tools"}, True
            return {"ok": False, "error": "workspace is not approved"}, True

        turn = await run_tools(
            [{"role": "user", "content": "Use git_log and run git_status."}],
            complete_model=script,
            execute_tool=execute_tool,
        )
        self.assertEqual(
            turn.reply,
            "I couldn't complete every requested operation.\ngit_status was not invoked.",
        )
        self.assertEqual(
            turn.tool_results,
            [{"name": "git_log", "output": "ab82957 Begin to implement tools"}],
        )
        self.assertEqual(
            turn.tool_requests,
            [
                {"name": "git_log", "status": "succeeded"},
                {"name": "git_status", "status": "not_invoked"},
            ],
        )
        self.assertNotIn("feat:", turn.reply)

    async def test_informational_question_does_not_require_a_tool(self) -> None:
        script = _Script([{"role": "assistant", "content": "git_log shows recent commit subjects."}])
        turn = await run_tools(
            [{"role": "user", "content": "What does git_log do?"}],
            complete_model=script,
            execute_tool=lambda name, arguments: (_ for _ in ()).throw(AssertionError(name)),
        )
        self.assertEqual(turn.reply, "git_log shows recent commit subjects.")
        self.assertEqual(turn.tool_requests, [])
        self.assertEqual(turn.tool_results, [])

    async def test_negated_instruction_does_not_require_a_tool(self) -> None:
        script = _Script([{"role": "assistant", "content": "I will not run it."}])
        turn = await run_tools(
            [{"role": "user", "content": "Don't use git_log."}],
            complete_model=script,
        )
        self.assertEqual(turn.reply, "I will not run it.")
        self.assertEqual(turn.tool_requests, [])

    def test_result_text_copies_handler_output(self) -> None:
        text = result_text(
            {
                "ok": True,
                "output": "ab82957 Begin to implement tools\n<script>alert(1)</script>",
                "truncated": True,
                "sensitive_omitted": True,
            }
        )
        self.assertIn("ab82957 Begin to implement tools", text)
        self.assertIn("<script>alert(1)</script>", text)
        self.assertIn("Output was truncated.", text)
        self.assertIn("Sensitive file content was omitted.", text)
        self.assertEqual(result_text({"ok": False, "error": "workspace is not approved", "output": "secret"}), "")


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
        self.assertIn("tool_results", MessageExchange.model_fields)
        self.assertIn("tool_requests", MessageExchange.model_fields)


if __name__ == "__main__":
    unittest.main()
