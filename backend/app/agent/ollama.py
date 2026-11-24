"""HTTP client for the local Ollama API."""

import logging

import httpx
from fastapi import HTTPException

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 120.0

_LOGGER = logging.getLogger("uvicorn.error")
_FAILURE_STATUS = {
    "ollama_timeout": 503,
    "ollama_unavailable": 503,
    "ollama_model_not_found": 503,
}
_MESSAGES = {
    "empty_reply": "Ollama returned an empty reply.",
    "invalid_ollama_response": "Ollama returned a response Norse could not use.",
    "ollama_http_error": "Ollama returned an error.",
    "ollama_timeout": "Ollama did not respond in time.",
    "ollama_unavailable": "Ollama is not reachable.",
    "ollama_model_not_found": "The configured Ollama model is not installed.",
    "extraction_failed": "Memory extraction failed.",
}


class OllamaError(Exception):
    """Ollama returned a response Norse could not use."""


class OllamaFailure(OllamaError):
    """A classified Ollama failure. The log line is written when this is created."""

    def __init__(
        self,
        code: str,
        *,
        stage: str,
        status_code: int | None = None,
        content_empty: bool | None = None,
        tool_calls: str = "",
        response_model: str = "",
        error_type: str = "",
    ) -> None:
        self.code = code
        self.stage = stage
        self.status_code = status_code
        self.content_empty = content_empty
        self.tool_calls = tool_calls
        self.response_model = response_model
        self.error_type = error_type
        super().__init__(_MESSAGES[code])
        log_ollama_failure(self)


class OllamaUnavailableError(OllamaFailure):
    """Ollama could not be reached."""


class OllamaModelNotInstalledError(OllamaFailure):
    """The configured model is not installed in Ollama."""


def log_ollama_failure(failure: OllamaFailure) -> None:
    """Write the safe diagnostic fields. Uvicorn prints this message as-is."""
    content_empty = "" if failure.content_empty is None else str(failure.content_empty).lower()
    upstream = "" if failure.status_code is None else str(failure.status_code)
    _LOGGER.error(
        "ollama failure code=%s stage=%s model=%s response_model=%s "
        "upstream_status=%s content_empty=%s tool_calls=%s error_type=%s",
        failure.code,
        failure.stage,
        _token(OLLAMA_MODEL),
        _token(failure.response_model),
        upstream,
        content_empty,
        failure.tool_calls,
        failure.error_type,
    )


def failure_response(exc: OllamaFailure) -> HTTPException:
    """Return the HTTP error for a classified Ollama failure."""
    return HTTPException(
        status_code=_FAILURE_STATUS.get(exc.code, 502),
        detail={"code": exc.code, "message": str(exc)},
    )


async def chat(
    messages: list[dict[str, str]],
    *,
    json_format: bool = False,
    stage: str = "chat",
) -> str:
    """Send conversation messages to Ollama and return the assistant text.

    json_format constrains the reply to JSON. The normal chat call leaves it off
    and does not offer tools.
    """
    message = await complete(messages, json_format=json_format, stage=stage)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OllamaFailure(
            "empty_reply",
            stage=stage,
            content_empty=True,
            tool_calls=tool_calls_kind(message),
        )
    return content


async def complete(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    json_format: bool = False,
    stage: str = "tool_round",
) -> dict:
    """Return Ollama's message, including tool_calls when the model requests tools."""
    if not messages:
        raise OllamaFailure("invalid_ollama_response", stage=stage, error_type="no_messages")

    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    payload: dict[str, object] = {
        "model": OLLAMA_MODEL,
        "messages": [_outgoing(message) for message in messages],
        "stream": False,
    }
    if tools is not None:
        payload["tools"] = tools
    if json_format:
        payload["format"] = "json"
    timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        raise OllamaFailure("ollama_timeout", stage=stage, error_type=type(exc).__name__) from exc
    except httpx.ConnectError as exc:
        raise OllamaFailure(
            "ollama_unavailable",
            stage=stage,
            error_type=type(exc).__name__,
        ) from exc

    if response.status_code == 404:
        raise OllamaFailure(
            "ollama_model_not_found",
            stage=stage,
            status_code=404,
            error_type="HTTPStatusError",
        )

    try:
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        raise OllamaFailure(
            "ollama_http_error",
            stage=stage,
            status_code=response.status_code,
            error_type=type(exc).__name__,
        ) from exc
    except (ValueError, TypeError) as exc:
        raise OllamaFailure(
            "invalid_ollama_response",
            stage=stage,
            status_code=response.status_code,
            error_type=type(exc).__name__,
        ) from exc

    if not isinstance(body, dict) or not isinstance(body.get("message"), dict):
        raise OllamaFailure(
            "invalid_ollama_response",
            stage=stage,
            status_code=response.status_code,
            response_model=_response_model(body),
            error_type="message_shape",
        )
    message = body["message"]
    content = message.get("content")
    calls = message.get("tool_calls")
    if (not isinstance(content, str) or not content.strip()) and not (isinstance(calls, list) and calls):
        raise OllamaFailure(
            "empty_reply",
            stage=stage,
            status_code=response.status_code,
            content_empty=True,
            tool_calls=tool_calls_kind(message),
            response_model=_response_model(body),
        )
    return message


def tool_calls_kind(message: object) -> str:
    """Describe the tool_calls field without reading its contents."""
    if not isinstance(message, dict) or message.get("tool_calls") is None:
        return "missing"
    if isinstance(message.get("tool_calls"), list):
        return "list"
    return "other"


def _outgoing(message: dict) -> dict:
    payload: dict[str, object] = {
        "role": message["role"],
        "content": message.get("content") or "",
    }
    if message.get("tool_calls"):
        payload["tool_calls"] = message["tool_calls"]
    if message.get("tool_name"):
        payload["tool_name"] = message["tool_name"]
    return payload


def _response_model(body: object) -> str:
    if not isinstance(body, dict):
        return ""
    return _token(body.get("model"))


def _token(value: object) -> str:
    """Keep a short single-line token. Drop anything that could rewrite a log line."""
    if not isinstance(value, str) or not value or len(value) > 80:
        return ""
    if any(_unsafe_log_character(character) for character in value):
        return ""
    return value


def _unsafe_log_character(character: str) -> bool:
    code = ord(character)
    return character.isspace() or code < 32 or code == 127 or 128 <= code <= 159
