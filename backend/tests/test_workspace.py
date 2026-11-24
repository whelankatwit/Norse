"""Path checks for approved workspaces."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tools.workspace import (
    WorkspaceConfigError,
    WorkspacePathError,
    list_workspaces,
    load_workspaces,
    resolve_inside,
)
from app.tools.workspace import _is_inside


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.outside = self.base / "outside"
        self.root.mkdir()
        self.outside.mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "app.txt").write_text("hello", encoding="utf-8")
        (self.outside / "secret.txt").write_text("nope", encoding="utf-8")
        self.junctions: list[Path] = []
        self.raw = str(self.root)

    def tearDown(self) -> None:
        for link in self.junctions:
            if os.path.lexists(link):
                os.rmdir(link)
        self.tmp.cleanup()

    def test_empty_configuration_approves_nothing(self) -> None:
        self.assertEqual(load_workspaces(""), [])

    def test_list_returns_id_and_name_only(self) -> None:
        with patch("app.tools.workspace.WORKSPACES", self.raw):
            self.assertEqual(list_workspaces(), [{"id": "repo", "name": "repo"}])

    def test_relative_configuration_is_rejected(self) -> None:
        with self.assertRaises(WorkspaceConfigError):
            load_workspaces("relative\\repo")

    def test_missing_directory_is_rejected(self) -> None:
        with self.assertRaises(WorkspaceConfigError):
            load_workspaces(str(self.base / "missing"))

    def test_duplicate_names_get_distinct_ids(self) -> None:
        other = self.base / "nested" / "repo"
        other.mkdir(parents=True)
        loaded = load_workspaces(f"{self.root}{os.pathsep}{other}")
        self.assertEqual([item.id for item in loaded], ["repo", "repo-2"])
        self.assertEqual(loaded[0].name, "repo")
        self.assertEqual(loaded[1].name, "repo")

    def test_relative_file_resolves_inside_the_workspace(self) -> None:
        resolved = resolve_inside("repo", "src/app.txt", self.raw)
        self.assertEqual(resolved, (self.root / "src" / "app.txt").resolve())
        self.assertEqual(resolve_inside("repo", "", self.raw), self.root.resolve())

    def test_parent_and_absolute_paths_are_rejected(self) -> None:
        with self.assertRaises(WorkspacePathError):
            resolve_inside("repo", "..\\outside\\secret.txt", self.raw)
        with self.assertRaises(WorkspacePathError):
            resolve_inside("repo", str(self.outside / "secret.txt"), self.raw)
        with self.assertRaises(WorkspacePathError):
            resolve_inside("repo", "src\\app.txt:stream", self.raw)

    def test_unknown_workspace_is_rejected(self) -> None:
        with self.assertRaises(WorkspacePathError):
            resolve_inside("other", "src/app.txt", self.raw)

    def test_sibling_prefix_is_outside(self) -> None:
        sibling = self.base / "repo-secret"
        sibling.mkdir()
        self.assertFalse(_is_inside(self.root, sibling / "secret.txt"))

    def test_junction_inside_the_workspace_can_be_read(self) -> None:
        real = self.root / "real"
        real.mkdir()
        (real / "note.txt").write_text("ok", encoding="utf-8")
        link = self.root / "alias"
        self._junction(link, real)
        resolved = resolve_inside("repo", "alias/note.txt", self.raw)
        self.assertEqual(resolved, (real / "note.txt").resolve())

    def test_junction_outside_the_workspace_is_rejected(self) -> None:
        link = self.root / "escape"
        self._junction(link, self.outside)
        with self.assertRaises(WorkspacePathError):
            resolve_inside("repo", "escape/secret.txt", self.raw)
        self.assertTrue((self.outside / "secret.txt").is_file())

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
