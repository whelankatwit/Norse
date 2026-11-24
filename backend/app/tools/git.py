"""Read-only Git inspection.

Pattern-based redaction cannot find every secret. Callers must treat the
returned text as untrusted, sensitive data.
"""

import os
import re
import subprocess
import threading
from pathlib import Path

from app.tools.files import excluded_relative
from app.tools.workspace import WorkspaceError, resolve_inside

MAX_GIT_BYTES = 8000
GIT_TIMEOUT_SECONDS = 5.0
MAX_LOG_COUNT = 20

_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b(\s*[:=]\s*)\S+"
)
_TOKEN = re.compile(
    r"\b(?:ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]+)\b"
)
_DROPPED_ENV = {
    "GIT_ASKPASS",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_EXTERNAL_DIFF",
    "GIT_WORK_TREE",
    "SSH_ASKPASS",
}


def git_status(
    workspace_id: str,
    raw: str | None = None,
    timeout_seconds: float = GIT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Return the branch and changed paths, without sensitive file contents."""
    prepared = _prepare(workspace_id, raw, timeout_seconds)
    if isinstance(prepared, dict):
        return prepared
    result = _git(
        prepared,
        ["status", "--porcelain=v1", "-b", "--ignore-submodules=all"],
        timeout_seconds,
    )
    if not result["ok"]:
        return result
    output, omitted = _filter_status(str(result["output"]))
    return _finish(output, bool(result["truncated"]), omitted)


def git_log(
    workspace_id: str,
    limit: int = 10,
    raw: str | None = None,
    timeout_seconds: float = GIT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Return recent commit subjects, capped at 20."""
    prepared = _prepare(workspace_id, raw, timeout_seconds)
    if isinstance(prepared, dict):
        return prepared
    count = min(max(limit, 1), MAX_LOG_COUNT)
    result = _git(
        prepared,
        ["log", f"-n{count}", "--format=%h %s"],
        timeout_seconds,
    )
    if not result["ok"]:
        return result
    output = "\n".join(_redact_line(line) for line in str(result["output"]).splitlines())
    return _finish(output, bool(result["truncated"]), False)


def git_diff(
    workspace_id: str,
    raw: str | None = None,
    timeout_seconds: float = GIT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Return the unstaged diff, with sensitive paths removed and lines redacted."""
    prepared = _prepare(workspace_id, raw, timeout_seconds)
    if isinstance(prepared, dict):
        return prepared
    result = _git(
        prepared,
        ["diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules"],
        timeout_seconds,
    )
    if not result["ok"]:
        return result
    output, omitted = _filter_diff(str(result["output"]))
    return _finish(output, bool(result["truncated"]), omitted)


def _prepare(workspace_id: str, raw: str | None, timeout_seconds: float) -> Path | dict[str, object]:
    try:
        root = resolve_inside(workspace_id, "", raw)
    except WorkspaceError as exc:
        return {"ok": False, "error": str(exc)}
    probed = _git(root, ["rev-parse", "--show-toplevel"], timeout_seconds)
    if not probed["ok"]:
        if probed["error"] == "git is not available":
            return probed
        return {"ok": False, "error": "workspace is not a git repository"}
    toplevel = Path(str(probed["output"]).strip())
    if not _same_path(root, toplevel):
        return {"ok": False, "error": "git repository root is outside the workspace"}
    return root


def _git(root: Path, args: list[str], timeout_seconds: float) -> dict[str, object]:
    command = [
        "git",
        "--no-pager",
        "-C",
        str(root),
        "-c",
        "core.fsmonitor=false",
        *args,
    ]
    return _run(command, timeout_seconds, MAX_GIT_BYTES)


def _run(command: list[str], timeout_seconds: float, limit: int) -> dict[str, object]:
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            shell=False,
            env=_git_env(),
        )
    except FileNotFoundError:
        return {"ok": False, "error": "git is not available"}
    captured = {"out": b"", "err": b""}

    def read_stream(stream, key: str) -> None:
        try:
            captured[key] = stream.read(limit + 1)
        except OSError:
            captured[key] = b""

    assert process.stdout is not None
    assert process.stderr is not None
    threads = [
        threading.Thread(target=read_stream, args=(process.stdout, "out"), daemon=True),
        threading.Thread(target=read_stream, args=(process.stderr, "err"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout_seconds)
    data = captured["out"]
    try:
        if len(data) > limit:
            process.kill()
            process.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=2)
            return {
                "ok": True,
                "output": data[:limit].decode("utf-8", errors="replace"),
                "truncated": True,
            }
        if any(thread.is_alive() for thread in threads):
            process.kill()
            for thread in threads:
                thread.join(timeout=2)
            process.wait(timeout=2)
            return {"ok": False, "error": "git timed out"}
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
            return {"ok": False, "error": "git timed out"}
        text = data.decode("utf-8", errors="replace")
        if process.returncode != 0:
            return {"ok": False, "error": "git failed"}
        return {"ok": True, "output": text, "truncated": False}
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def _git_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in _DROPPED_ENV and not key.upper().startswith("GIT_CONFIG_KEY_")
        and not key.upper().startswith("GIT_CONFIG_VALUE_")
    }
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _filter_status(text: str) -> tuple[str, bool]:
    kept: list[str] = []
    omitted = False
    for line in text.splitlines():
        if line.startswith("##"):
            kept.append(line)
            continue
        if any(excluded_relative(path) for path in _status_paths(line)):
            omitted = True
            continue
        kept.append(_redact_line(line))
    if omitted:
        kept.append("Sensitive file content was omitted.")
    return "\n".join(kept), omitted


def _filter_diff(text: str) -> tuple[str, bool]:
    kept: list[str] = []
    omitted = False
    skipping = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            path = _diff_path(line)
            skipping = path is not None and excluded_relative(path)
            if skipping:
                omitted = True
                continue
        if skipping:
            continue
        kept.append(_redact_line(line))
    if omitted:
        kept.append("Sensitive file content was omitted.")
    return "\n".join(kept), omitted


def _status_paths(line: str) -> list[str]:
    body = line[3:] if len(line) > 3 else line
    if " -> " in body:
        old, new = body.split(" -> ", 1)
        return [old.strip().strip('"'), new.strip().strip('"')]
    return [body.strip().strip('"')]


def _diff_path(header: str) -> str | None:
    marker = " b/"
    index = header.rfind(marker)
    if index == -1:
        return None
    return header[index + len(marker) :].strip().strip('"')


def _redact_line(line: str) -> str:
    if "PRIVATE KEY" in line or "-----BEGIN " in line:
        return "[redacted]"
    line = _ASSIGNMENT.sub(r"\1\2[redacted]", line)
    return _TOKEN.sub("[redacted]", line)


def _finish(output: str, truncated: bool, omitted: bool) -> dict[str, object]:
    return {
        "ok": True,
        "output": output,
        "truncated": truncated,
        "sensitive_omitted": omitted,
    }


def _same_path(left: Path, right: Path) -> bool:
    try:
        left_real = os.path.normcase(os.path.normpath(str(left.resolve())))
        right_real = os.path.normcase(os.path.normpath(str(right.resolve())))
    except OSError:
        return False
    return left_real == right_real
