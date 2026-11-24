"""HTTP client for the local Ollama API."""

import httpx

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 120.0


class OllamaError(Exception):
    """Ollama returned a response Norse could not use."""


class OllamaUnavailableError(OllamaError):
    """Ollama could not be reached."""


class OllamaModelNotInstalledError(OllamaError):
    """The configured model is not installed in Ollama."""


async def chat(messages: list[dict[str, str]], *, json_format: bool = False) -> str:
    """Send conversation messages to Ollama and return the assistant text.

    json_format constrains the reply to JSON. The normal chat call leaves it off
    and does not offer tools.
    """
    message = await complete(messages, json_format=json_format)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OllamaError("Ollama returned an unexpected response")
    return content


async def complete(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    json_format: bool = False,
) -> dict:
    """Return Ollama's message, including tool_calls when the model requests tools."""
    if not messages:
        raise OllamaError("No messages to send")

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
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise OllamaUnavailableError(
            f"Ollama is not reachable at {OLLAMA_BASE_URL}"
        ) from exc

    if response.status_code == 404:
        raise OllamaModelNotInstalledError(
            f"Ollama model '{OLLAMA_MODEL}' is not installed"
        )

    try:
        response.raise_for_status()
        message = response.json()["message"]
    except (httpx.HTTPStatusError, ValueError, KeyError, TypeError) as exc:
        raise OllamaError("Ollama returned an unexpected response") from exc

    if not isinstance(message, dict):
        raise OllamaError("Ollama returned an unexpected response")
    return message


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
