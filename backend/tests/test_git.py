"""Read-only Git tools."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.tools.git import _run, git_diff, git_log, git_status


class GitToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.raw = str(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_plain_directory_is_not_a_repository(self) -> None:
        result = git_status("repo", raw=self.raw)
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "workspace is not a git repository")

    def test_parent_repository_is_rejected(self) -> None:
        parent = self.base / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        (parent / "outside.txt").write_text("parent-secret\n", encoding="utf-8")
        self._commit(parent, "outside.txt")
        result = git_status("child", raw=str(child))
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "git repository root is outside the workspace")
        self.assertNotIn("parent-secret", str(result))
        self.assertNotIn(str(parent), str(result))

    def test_status_diff_and_log_hide_sensitive_text(self) -> None:
        (self.root / "app.txt").write_text("one\n", encoding="utf-8")
        (self.root / "note.txt").write_text("password=hunter2\n", encoding="utf-8")
        self._commit(self.root, "app.txt", "note.txt")
        (self.root / "app.txt").write_text("two\n", encoding="utf-8")
        (self.root / "note.txt").write_text("password=hunter3\n", encoding="utf-8")
        (self.root / ".env").write_text("token=ghp_abcdefghijklmnopqrst\n", encoding="utf-8")
        status = git_status("repo", raw=self.raw)
        diff = git_diff("repo", raw=self.raw)
        logged = git_log("repo", raw=self.raw)
        self.assertEqual(status["ok"], True)
        self.assertIn("app.txt", str(status["output"]))
        self.assertNotIn(".env", str(status["output"]))
        self.assertNotIn("ghp_", str(status["output"]))
        self.assertNotIn("hunter3", str(diff["output"]))
        self.assertIn("[redacted]", str(diff["output"]))
        self.assertNotIn(".env", str(diff["output"]))
        self.assertIn("init", str(logged["output"]))

    def test_configured_filesystem_monitor_does_not_run(self) -> None:
        (self.root / "app.txt").write_text("one\n", encoding="utf-8")
        self._commit(self.root, "app.txt")
        marker = self.root / "fsmonitor-ran.txt"
        hook = self.root / "hook.cmd"
        hook.write_text(f'@echo off\r\necho ran>"{marker}"\r\n', encoding="utf-8")
        self._git(self.root, "config", "core.fsmonitor", str(hook))
        result = git_status("repo", raw=self.raw)
        self.assertEqual(result["ok"], True)
        self.assertFalse(marker.exists())

    def test_output_limit_and_timeout(self) -> None:
        limited = _run(
            [sys.executable, "-c", "print('x' * 20000)"],
            timeout_seconds=5,
            limit=100,
        )
        self.assertEqual(limited["truncated"], True)
        self.assertLessEqual(len(str(limited["output"])), 100)
        timed = _run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_seconds=0.2,
            limit=100,
        )
        self.assertEqual(timed, {"ok": False, "error": "git timed out"})

    def _commit(self, root: Path, *paths: str) -> None:
        self._git(root, "init")
        if paths:
            self._git(root, "add", *paths)
            self._git(root, "commit", "-m", "init")

    def _git(self, root: Path, *args: str) -> None:
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "Test"
        env["GIT_AUTHOR_EMAIL"] = "test@example.com"
        env["GIT_COMMITTER_NAME"] = "Test"
        env["GIT_COMMITTER_EMAIL"] = "test@example.com"
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
