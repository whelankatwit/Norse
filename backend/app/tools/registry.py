"""Tool definitions. Only this module decides whether a tool may run."""

from collections.abc import Callable
from dataclasses import dataclass

from app.tools.files import excluded_relative, find_files, list_directory, read_file, search_text
from app.tools.git import git_diff, git_log, git_status
from app.tools.workspace import list_workspaces

_STRING = {"type": "string"}
_WORKSPACE = {
    "type": "object",
    "properties": {"workspace": _STRING},
    "required": ["workspace"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    permission: str
    handler: Callable[[dict], dict]


def _workspaces(_arguments: dict) -> dict:
    return {"ok": True, "workspaces": list_workspaces()}


def _directory(arguments: dict) -> dict:
    return list_directory(arguments["workspace"], arguments.get("path", ""))


def _find(arguments: dict) -> dict:
    return find_files(arguments["workspace"], arguments["name"], arguments.get("path", ""))


def _search(arguments: dict) -> dict:
    return search_text(arguments["workspace"], arguments["text"], arguments.get("path", ""))


def _read(arguments: dict) -> dict:
    return read_file(
        arguments["workspace"],
        arguments["path"],
        arguments.get("start_line", 1),
        arguments.get("end_line"),
    )


def _status(arguments: dict) -> dict:
    return git_status(arguments["workspace"])


def _log(arguments: dict) -> dict:
    return git_log(arguments["workspace"], arguments.get("limit", 10))


def _diff(arguments: dict) -> dict:
    return git_diff(arguments["workspace"])


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOLS = (
    Tool(
        "list_workspaces",
        "List approved workspace ids and directory names.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "read",
        _workspaces,
    ),
    Tool(
        "list_directory",
        "List entries in one directory of an approved workspace.",
        _schema({"workspace": _STRING, "path": _STRING}, ["workspace"]),
        "read",
        _directory,
    ),
    Tool(
        "find_files",
        "Find files whose names contain a substring.",
        _schema({"workspace": _STRING, "name": _STRING, "path": _STRING}, ["workspace", "name"]),
        "read",
        _find,
    ),
    Tool(
        "search_text",
        "Search file text for a literal substring.",
        _schema({"workspace": _STRING, "text": _STRING, "path": _STRING}, ["workspace", "text"]),
        "read",
        _search,
    ),
    Tool(
        "read_file",
        "Read a text file, or a line range of a large file.",
        _schema(
            {
                "workspace": _STRING,
                "path": _STRING,
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            ["workspace", "path"],
        ),
        "read",
        _read,
    ),
    Tool(
        "git_status",
        "Show the branch and changed paths. Sensitive file contents are omitted.",
        _WORKSPACE,
        "read",
        _status,
    ),
    Tool(
        "git_log",
        "Show recent commit subjects, at most 20.",
        _schema({"workspace": _STRING, "limit": {"type": "integer"}}, ["workspace"]),
        "read",
        _log,
    ),
    Tool(
        "git_diff",
        "Show the unstaged diff. Treat the result as sensitive data.",
        _WORKSPACE,
        "read",
        _diff,
    ),
)


def definitions(tools: tuple[Tool, ...] = TOOLS) -> list[dict]:
    """Describe the read-only tools Ollama is allowed to request."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
        if tool.permission == "read"
    ]


def execute(
    name: str,
    arguments: object,
    tools: tuple[Tool, ...] = TOOLS,
) -> tuple[dict, bool]:
    """Run a read tool. Unknown and approval-only tools are not invoked."""
    tool = next((item for item in tools if item.name == name), None)
    if tool is None or tool.permission != "read":
        error = "unknown tool" if tool is None else "approval required"
        return {"ok": False, "error": error}, False
    if not isinstance(arguments, dict) or not _valid(tool, arguments):
        return {"ok": False, "error": "invalid arguments"}, True
    try:
        result = tool.handler(arguments)
    except Exception:
        return {"ok": False, "error": "tool failed"}, True
    if not isinstance(result, dict):
        return {"ok": False, "error": "tool failed"}, True
    return result, True


def report(name: str, arguments: dict, result: dict) -> dict[str, object]:
    """Describe one invocation without absolute paths or sensitive arguments."""
    reported: dict[str, object] = {"name": name, "ok": result.get("ok") is True}
    path = arguments.get("path")
    workspace = arguments.get("workspace")
    if (
        reported["ok"]
        and isinstance(workspace, str)
        and isinstance(path, str)
        and path.strip()
        and not excluded_relative(path)
        and "\\" not in path
        and not path.startswith("/")
        and not (len(path) >= 2 and path[1] == ":")
    ):
        reported["workspace"] = workspace
        reported["path"] = path.replace("\\", "/")
    return reported


def _valid(tool: Tool, arguments: dict) -> bool:
    parameters = tool.parameters
    properties = parameters.get("properties", {})
    if any(key not in properties for key in arguments):
        return False
    for key in parameters.get("required", []):
        if key not in arguments:
            return False
    for key, value in arguments.items():
        expected = properties[key].get("type")
        if expected == "string" and not isinstance(value, str):
            return False
        if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return False
    return True
