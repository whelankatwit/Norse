"""Approved workspace roots and path checks.

Git commands are not run here. When they are added, every Git invocation must
pass ``-c core.fsmonitor=false`` so a repository's local config cannot start a
filesystem monitor. The discovered Git root must also be the approved workspace
root; a repository found in a parent directory is rejected. Git output stays
untrusted after pattern-based credential filtering, which cannot see every secret.
"""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.config import WORKSPACES

_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class WorkspaceError(Exception):
    """A workspace could not be used."""


class WorkspaceConfigError(WorkspaceError):
    """NORSE_WORKSPACES contains a path Norse cannot approve."""


class WorkspacePathError(WorkspaceError):
    """The requested path is not inside the approved workspace."""


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    root: Path


def list_workspaces() -> list[dict[str, str]]:
    """Return the approved workspace ids and directory names."""
    return [{"id": workspace.id, "name": workspace.name} for workspace in load_workspaces()]


def load_workspaces(raw: str | None = None) -> list[Workspace]:
    """Parse an explicit list, or NORSE_WORKSPACES when raw is omitted."""
    source = WORKSPACES if raw is None else raw
    workspaces: list[Workspace] = []
    used_ids: set[str] = set()
    for piece in source.split(os.pathsep):
        text = piece.strip().strip('"')
        if not text:
            continue
        root = _approved_root(text)
        base = root.name or root.anchor.rstrip("\\/")
        workspace_id = base
        suffix = 2
        while workspace_id.casefold() in used_ids:
            workspace_id = f"{base}-{suffix}"
            suffix += 1
        used_ids.add(workspace_id.casefold())
        workspaces.append(Workspace(id=workspace_id, name=base, root=root))
    return workspaces


def resolve_inside(workspace_id: str, relative: str = "", raw: str | None = None) -> Path:
    """Resolve a relative path and require the real target to stay inside the workspace."""
    workspace = _workspace(workspace_id, raw)
    parts = _relative_parts(relative)
    current = workspace.root
    for index, part in enumerate(parts):
        current = current / part
        if _is_reparse(current):
            target = _reparse_target(current)
            if not _is_inside(workspace.root, target):
                raise WorkspacePathError("path is outside the workspace")
            current = target
            continue
        if current.exists():
            resolved = current.resolve()
            if not _is_inside(workspace.root, resolved):
                raise WorkspacePathError("path is outside the workspace")
            current = resolved
            continue
        for extra in parts[index + 1 :]:
            current = current / extra
        if not _is_inside(workspace.root, current):
            raise WorkspacePathError("path is outside the workspace")
        return current
    if not _is_inside(workspace.root, current):
        raise WorkspacePathError("path is outside the workspace")
    return current.resolve() if current.exists() else current


def _workspace(workspace_id: str, raw: str | None = None) -> Workspace:
    for workspace in load_workspaces(raw):
        if workspace.id == workspace_id:
            return workspace
    raise WorkspacePathError("workspace is not approved")


def _approved_root(text: str) -> Path:
    path = Path(text)
    if not path.is_absolute():
        raise WorkspaceConfigError(f"{text} is not an absolute path")
    try:
        root = path.resolve()
    except OSError as exc:
        raise WorkspaceConfigError(f"{text} could not be resolved") from exc
    if not root.is_dir():
        raise WorkspaceConfigError(f"{text} is not a directory")
    return root


def _relative_parts(relative: str) -> tuple[str, ...]:
    if "\x00" in relative:
        raise WorkspacePathError("path is outside the workspace")
    stripped = relative.strip()
    if stripped in ("", "."):
        return ()
    path = Path(stripped)
    if path.is_absolute() or stripped.startswith(("/", "\\")):
        raise WorkspacePathError("path is outside the workspace")
    if os.name == "nt" and len(stripped) >= 2 and stripped[1] == ":":
        raise WorkspacePathError("path is outside the workspace")
    parts: list[str] = []
    for part in path.parts:
        if part in (".",):
            continue
        if part == ".." or ":" in part:
            raise WorkspacePathError("path is outside the workspace")
        parts.append(part)
    return tuple(parts)


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_junction():
            return True
        info = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_ATTRIBUTE)


def _reparse_target(path: Path) -> Path:
    try:
        raw = os.readlink(path)
    except OSError as exc:
        raise WorkspacePathError("reparse point target could not be determined") from exc
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    target = Path(raw)
    if not target.is_absolute():
        target = path.parent / target
    try:
        return target.resolve()
    except OSError as exc:
        raise WorkspacePathError("reparse point target could not be determined") from exc


def reparse_confined(root: Path, path: Path) -> bool:
    """False when a reparse point leaves the workspace or its target cannot be read."""
    if not _is_reparse(path):
        return True
    try:
        target = _reparse_target(path)
    except WorkspacePathError:
        return False
    return _is_inside(root, target)


def _is_inside(root: Path, candidate: Path) -> bool:
    root_real = os.path.normcase(os.path.normpath(str(root.resolve())))
    try:
        candidate_real = os.path.normcase(os.path.normpath(str(candidate.resolve())))
    except OSError:
        return False
    return candidate_real == root_real or candidate_real.startswith(root_real + os.sep)
