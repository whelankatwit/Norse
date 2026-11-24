"""Read-only file tool limits and exclusions."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.tools.files import (
    MAX_DIRECTORY_ENTRIES,
    MAX_FILE_BYTES,
    MAX_LINE_CHARS,
    MAX_READ_LINES,
    find_files,
    list_directory,
    read_file,
    search_text,
)


class FileToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.outside = self.base / "outside"
        self.root.mkdir()
        self.outside.mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "app.txt").write_text("hello\n", encoding="utf-8")
        (self.root / ".env").write_text("hello secret\n", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "app.txt").write_text("hello hidden\n", encoding="utf-8")
        (self.root / "src" / "data.bin").write_bytes(b"hello\x00world")
        (self.outside / "secret.txt").write_text("nope\n", encoding="utf-8")
        self.junctions: list[Path] = []
        self.raw = str(self.root)

    def tearDown(self) -> None:
        for link in self.junctions:
            if os.path.lexists(link):
                os.rmdir(link)
        self.tmp.cleanup()

    def test_listing_hides_sensitive_and_skipped_names(self) -> None:
        listed = list_directory("repo", raw=self.raw)
        names = [entry["name"] for entry in listed["entries"]]
        self.assertEqual(listed["ok"], True)
        self.assertIn("src", names)
        self.assertNotIn(".env", names)
        self.assertNotIn("node_modules", names)
        self.assertNotIn(str(self.root), str(listed))

    def test_listing_stops_at_the_entry_limit(self) -> None:
        folder = self.root / "many"
        folder.mkdir()
        for number in range(MAX_DIRECTORY_ENTRIES + 1):
            (folder / f"f{number}.txt").write_text("x", encoding="utf-8")
        listed = list_directory("repo", "many", self.raw)
        self.assertEqual(len(listed["entries"]), MAX_DIRECTORY_ENTRIES)
        self.assertEqual(listed["truncated"], True)

    def test_find_and_search_skip_sensitive_binary_and_outside_targets(self) -> None:
        link = self.root / "escape"
        self._junction(link, self.outside)
        found = find_files("repo", "app", raw=self.raw)
        self.assertEqual(found["matches"], ["src/app.txt"])
        secret = find_files("repo", "secret", raw=self.raw)
        self.assertEqual(secret["matches"], [])
        matches = search_text("repo", "hello", raw=self.raw)
        self.assertEqual(matches["matches"], ["src/app.txt:1: hello"])
        self.assertEqual(search_text("repo", "nope", raw=self.raw)["matches"], [])
        self.assertEqual(search_text("repo", "secret", raw=self.raw)["matches"], [])

    def test_read_range_and_limits(self) -> None:
        lines = "".join(f"line {number}\n" for number in range(1, MAX_READ_LINES + 20))
        (self.root / "src" / "long.txt").write_text(lines, encoding="utf-8")
        (self.root / "src" / "wide.txt").write_text("w" * (MAX_LINE_CHARS + 50), encoding="utf-8")
        (self.root / "src" / "big.txt").write_bytes(b"a" * (MAX_FILE_BYTES + 1))
        read = read_file("repo", "src/app.txt", raw=self.raw)
        self.assertEqual(read["content"], "hello\n")
        limited = read_file("repo", "src/long.txt", raw=self.raw)
        self.assertEqual(limited["content"].count("\n"), MAX_READ_LINES)
        self.assertEqual(limited["truncated"], True)
        wide = read_file("repo", "src/wide.txt", raw=self.raw)
        self.assertEqual(len(wide["content"].strip("\n")), MAX_LINE_CHARS)
        huge = read_file("repo", "src/big.txt", raw=self.raw)
        self.assertEqual(huge["ok"], False)
        ranged = read_file("repo", "src/long.txt", start_line=3, end_line=4, raw=self.raw)
        self.assertEqual(ranged["content"], "line 3\nline 4\n")

    def test_sensitive_and_parent_paths_are_refused(self) -> None:
        self.assertEqual(read_file("repo", ".env", raw=self.raw)["ok"], False)
        self.assertEqual(list_directory("repo", "node_modules", raw=self.raw)["ok"], False)
        self.assertEqual(read_file("repo", "..\\outside\\secret.txt", raw=self.raw)["ok"], False)

    def test_walk_stops_when_the_time_limit_is_already_spent(self) -> None:
        found = find_files("repo", "app", raw=self.raw, time_limit_seconds=0)
        self.assertEqual(found["matches"], [])
        self.assertEqual(found["truncated"], True)

    def _junction(self, link: Path, target: Path) -> None:
        self.junctions.append(link)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise AssertionError(message or "mklink failed")


if __name__ == "__main__":
    unittest.main()
