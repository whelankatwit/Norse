"""Read-only file tools. Results stay inside an approved workspace."""

import os
import time
from pathlib import Path

from app.tools.workspace import (
    WorkspaceError,
    _is_inside,
    load_workspaces,
    reparse_confined,
    resolve_inside,
)

MAX_DIRECTORY_ENTRIES = 200
MAX_MATCHES = 50
MAX_DEPTH = 8
MAX_FILES_VISITED = 5000
MAX_LINE_CHARS = 500
MAX_OUTPUT_CHARS = 8000
MAX_READ_LINES = 200
MAX_FILE_BYTES = 1_000_000
TIME_LIMIT_SECONDS = 3.0

_SKIPPED_DIRECTORIES = {
    ".git",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
_SENSITIVE_NAMES = {
    ".env",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
_SENSITIVE_SUFFIXES = {".cer", ".crt", ".key", ".p12", ".pem", ".pfx"}


def list_directory(
    workspace_id: str,
    relative: str = "",
    raw: str | None = None,
    time_limit_seconds: float = TIME_LIMIT_SECONDS,
) -> dict[str, object]:
    """List one directory, omitting skipped and sensitive names."""
    try:
        root = _root(workspace_id, raw)
        directory = resolve_inside(workspace_id, relative, raw)
        if not directory.is_dir() or _blocked(directory):
            return _unavailable()
        deadline = time.monotonic() + time_limit_seconds
        entries: list[dict[str, str]] = []
        truncated = False
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return _unavailable()
        for child in children:
            if time.monotonic() > deadline:
                truncated = True
                break
            if not _visible(root, child):
                continue
            if len(entries) >= MAX_DIRECTORY_ENTRIES:
                truncated = True
                break
            kind = "directory" if child.is_dir() else "file"
            entries.append({"name": child.name, "kind": kind})
        return {
            "ok": True,
            "path": _display(root, directory),
            "entries": entries,
            "truncated": truncated,
        }
    except WorkspaceError as exc:
        return {"ok": False, "error": str(exc)}


def find_files(
    workspace_id: str,
    name: str,
    relative: str = "",
    raw: str | None = None,
    time_limit_seconds: float = TIME_LIMIT_SECONDS,
) -> dict[str, object]:
    """Find files whose names contain a substring."""
    needle = name.strip()
    if not needle:
        return {"ok": False, "error": "name is required"}
    try:
        root = _root(workspace_id, raw)
        start = resolve_inside(workspace_id, relative, raw)
        if not start.exists() or _blocked(start):
            return _unavailable()
        folded = needle.casefold()
        matches: list[str] = []
        truncated = False
        for path, stopped in _walk(root, start, time_limit_seconds):
            if stopped:
                truncated = True
                break
            if not path.is_file() or folded not in path.name.casefold():
                continue
            matches.append(_display(root, path))
            if len(matches) >= MAX_MATCHES:
                truncated = True
                break
        return {"ok": True, "matches": matches, "truncated": truncated}
    except WorkspaceError as exc:
        return {"ok": False, "error": str(exc)}


def search_text(
    workspace_id: str,
    text: str,
    relative: str = "",
    raw: str | None = None,
    time_limit_seconds: float = TIME_LIMIT_SECONDS,
) -> dict[str, object]:
    """Search file text for a literal substring."""
    query = text.strip()
    if not query:
        return {"ok": False, "error": "text is required"}
    try:
        root = _root(workspace_id, raw)
        start = resolve_inside(workspace_id, relative, raw)
        if not start.exists() or _blocked(start):
            return _unavailable()
        hits: list[str] = []
        size = 0
        truncated = False
        for path, stopped in _walk(root, start, time_limit_seconds):
            if stopped:
                truncated = True
                break
            if not path.is_file() or _is_binary(path) or _too_large(path):
                continue
            for hit in _search_file(root, path, query):
                if len(hits) >= MAX_MATCHES or size + len(hit) > MAX_OUTPUT_CHARS:
                    truncated = True
                    break
                hits.append(hit)
                size += len(hit)
            if truncated:
                break
        return {"ok": True, "matches": hits, "truncated": truncated}
    except WorkspaceError as exc:
        return {"ok": False, "error": str(exc)}


def read_file(
    workspace_id: str,
    relative: str,
    start_line: int = 1,
    end_line: int | None = None,
    raw: str | None = None,
) -> dict[str, object]:
    """Read a text file, at most 200 lines, with a required range for large files."""
    if start_line < 1 or (end_line is not None and end_line < start_line):
        return {"ok": False, "error": "line range is invalid"}
    try:
        root = _root(workspace_id, raw)
        path = resolve_inside(workspace_id, relative, raw)
        if not path.is_file() or _blocked(path):
            return _unavailable()
        if end_line is None and _too_large(path):
            return {"ok": False, "error": "file is too large; provide a line range"}
        if _is_binary(path):
            return {"ok": False, "error": "file is binary"}
        last = start_line + MAX_READ_LINES - 1
        if end_line is not None:
            last = min(end_line, last)
        lines: list[str] = []
        size = 0
        truncated = False
        with path.open(encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, start=1):
                if number < start_line:
                    continue
                if number > last:
                    truncated = True
                    break
                if len(line) > MAX_LINE_CHARS:
                    line = line[:MAX_LINE_CHARS]
                if size + len(line) > MAX_OUTPUT_CHARS:
                    truncated = True
                    break
                lines.append(line if line.endswith("\n") else f"{line}\n")
                size += len(lines[-1])
        return {
            "ok": True,
            "path": _display(root, path),
            "start_line": start_line,
            "content": "".join(lines),
            "truncated": truncated,
        }
    except WorkspaceError as exc:
        return {"ok": False, "error": str(exc)}


def _root(workspace_id: str, raw: str | None) -> Path:
    for workspace in load_workspaces(raw):
        if workspace.id == workspace_id:
            return workspace.root
    raise WorkspaceError("workspace is not approved")


def _walk(root: Path, start: Path, time_limit_seconds: float):
    deadline = time.monotonic() + time_limit_seconds
    seen: set[str] = set()
    stack = [(start, 0)]
    visited = 0
    while stack:
        if time.monotonic() >= deadline or visited > MAX_FILES_VISITED:
            yield start, True
            return
        current, depth = stack.pop()
        if current.is_file():
            visited += 1
            yield current, False
            continue
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            visited += 1
            if visited > MAX_FILES_VISITED or time.monotonic() >= deadline:
                yield child, True
                return
            if not _visible(root, child):
                continue
            identity = os.path.normcase(os.path.normpath(str(child.resolve())))
            if identity in seen:
                continue
            seen.add(identity)
            if child.is_dir():
                if depth + 1 <= MAX_DEPTH:
                    stack.append((child, depth + 1))
                continue
            if child.is_file():
                yield child, False


def _search_file(root: Path, path: Path, query: str):
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for number, line in enumerate(handle, start=1):
            if query not in line:
                continue
            shown = line.rstrip("\r\n")
            if len(shown) > MAX_LINE_CHARS:
                shown = shown[:MAX_LINE_CHARS]
            yield f"{_display(root, path)}:{number}: {shown}"


def _visible(root: Path, path: Path) -> bool:
    if _blocked(path) or not reparse_confined(root, path):
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return _is_inside(root, resolved)


def excluded_relative(relative: str) -> bool:
    """True when a repository-relative path is left out of tool output."""
    text = relative.strip().strip('"').replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    return any(_blocked(Path(part)) for part in Path(text).parts)


def _blocked(path: Path) -> bool:
    if path.name.casefold() in {name.casefold() for name in _SKIPPED_DIRECTORIES}:
        return True
    return _is_sensitive(path.name)


def _is_sensitive(name: str) -> bool:
    folded = name.casefold()
    if folded in {item.casefold() for item in _SENSITIVE_NAMES} or folded.startswith(".env."):
        return True
    return Path(folded).suffix in _SENSITIVE_SUFFIXES


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(8192)
    except OSError:
        return True


def _too_large(path: Path) -> bool:
    try:
        return path.stat().st_size > MAX_FILE_BYTES
    except OSError:
        return True


def _display(root: Path, path: Path) -> str:
    relative = os.path.relpath(path, root)
    return Path(relative).as_posix()


def _unavailable() -> dict[str, object]:
    return {"ok": False, "error": "path is not available"}
