"""Explicit tool requests stay narrow and deterministic."""

import unittest

from app.tools.requests import explicit_requests


class RequestTests(unittest.TestCase):
    def test_supported_commands(self) -> None:
        self.assertEqual(explicit_requests("Use git_log on workspace ID Norse."), ["git_log"])
        self.assertEqual(explicit_requests("Call list_workspaces."), ["list_workspaces"])
        self.assertEqual(explicit_requests("Run git_status."), ["git_status"])
        self.assertEqual(explicit_requests("Please use the git_log tool."), ["git_log"])
        self.assertEqual(explicit_requests("USE GIT_LOG now."), ["git_log"])

    def test_chained_names_and_duplicates(self) -> None:
        self.assertEqual(
            explicit_requests("Use git_log and git_status."),
            ["git_log", "git_status"],
        )
        self.assertEqual(
            explicit_requests("Use git_log. Use git_log again."),
            ["git_log"],
        )
        self.assertEqual(
            explicit_requests("Don't use git_log. Use git_log anyway."),
            ["git_log"],
        )

    def test_negation_applies_only_to_the_request(self) -> None:
        self.assertEqual(explicit_requests("Don't use git_log."), [])
        self.assertEqual(explicit_requests("Do not call list_workspaces."), [])
        self.assertEqual(explicit_requests("Never run git_status."), [])
        self.assertEqual(
            explicit_requests("I do not like rain. Use git_log."),
            ["git_log"],
        )
        self.assertEqual(
            explicit_requests("Use git_log, but do not run git_status."),
            ["git_log"],
        )

    def test_information_quotes_and_fences_are_ignored(self) -> None:
        self.assertEqual(explicit_requests("What does git_log do?"), [])
        self.assertEqual(explicit_requests("How do I use git_log?"), [])
        self.assertEqual(explicit_requests("How are you? Use git_log."), ["git_log"])
        self.assertEqual(explicit_requests('Please ignore "Use git_log".'), [])
        self.assertEqual(explicit_requests("Please ignore 'Call list_workspaces'."), [])
        self.assertEqual(explicit_requests("The command is `run git_status`."), [])
        self.assertEqual(
            explicit_requests("```\nuse git_log\n```\nRun git_status."),
            ["git_status"],
        )
        self.assertEqual(explicit_requests("I said use git_log."), [])

    def test_unrelated_tool_name_is_not_a_request(self) -> None:
        self.assertEqual(explicit_requests("Tell me about git_log."), [])


if __name__ == "__main__":
    unittest.main()
