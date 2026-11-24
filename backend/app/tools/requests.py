"""Detect an explicit request to run a registered tool.

A request is a registered tool name used as the object of ``use``, ``call``,
or ``run``. ``the`` may sit between the verb and the name. Further names
joined by ``and``, ``or``, ``then``, or a comma are part of the same request:

    use git_log
    call the list_workspaces
    run git_status and git_diff

These are not requests:

- text inside double quotes, single quotes, backticks, or fenced code blocks
- a verb governed by ``not``, ``never``, ``don't``, or ``do not``
- a verb immediately preceded by ``say``, ``says``, ``said``, ``mention``, or ``mentioned``
- a verb in a clause that asks ``how``, ``what``, ``why``, ``when``, ``where``, ``which``, or ``who``

A clause starts at the beginning of the text or after ``.``, ``!``, ``?``,
``;``, ``:``, a newline, or a comma. A word elsewhere in the message does not
cancel a request. Repeated names become one request, in first-seen order. A
positive occurrence keeps the tool when another sentence negates it.
"""

import re

from app.tools.registry import TOOLS

_VERBS = r"use|call|run"
_NEGATION = r"do\s+not|don['\u2019]?t|dont|never|not"
_INFO = re.compile(r"(?i)\b(?:how|what|why|when|where|which|who)\b")
_MENTION = {"say", "says", "said", "mention", "mentioned"}
_CLAUSE = ".!?;:\n,"


def explicit_requests(text: str, names: tuple[str, ...] | None = None) -> list[str]:
    """Return the registered tools this message explicitly asks to run."""
    catalog = tuple(tool.name for tool in TOOLS) if names is None else names
    if not text or not catalog:
        return []
    canonical = {name.casefold(): name for name in catalog}
    name_pattern = "|".join(re.escape(name) for name in sorted(catalog, key=len, reverse=True))
    verb_name = rf"(?i)\b(?:{_VERBS})\s+(?:the\s+)?(?P<name>{name_pattern})\b"
    positive = re.compile(verb_name)
    negated = re.compile(rf"(?i)\b(?:{_NEGATION})\s+(?:{_VERBS})\s+(?:the\s+)?(?P<name>{name_pattern})\b")
    chain = re.compile(rf"(?i)\s*(?:,|and|or|then)\s+(?:the\s+)?(?P<name>{name_pattern})\b")
    visible = _visible(text)
    spans: list[tuple[int, int]] = []
    for match in negated.finditer(visible):
        spans.append((match.start(), _chain_end(visible, match.end(), chain)))
    requested: list[str] = []
    for match in positive.finditer(visible):
        if _covered(spans, match.start()) or _mentioned(visible, match.start()) or _informational(visible, match.start()):
            continue
        for found in _names(match, visible, chain):
            tool_name = canonical.get(found.casefold())
            if tool_name and tool_name not in requested:
                requested.append(tool_name)
    return requested


def _names(match: re.Match[str], text: str, chain: re.Pattern[str]) -> list[str]:
    found = [match.group("name")]
    end = match.end()
    while True:
        extra = chain.match(text, end)
        if extra is None:
            return found
        found.append(extra.group("name"))
        end = extra.end()


def _chain_end(text: str, end: int, chain: re.Pattern[str]) -> int:
    while True:
        extra = chain.match(text, end)
        if extra is None:
            return end
        end = extra.end()


def _covered(spans: list[tuple[int, int]], start: int) -> bool:
    return any(span_start <= start < span_end for span_start, span_end in spans)


def _mentioned(text: str, verb_start: int) -> bool:
    words = text[:verb_start].split()
    return bool(words) and words[-1].casefold() in _MENTION


def _informational(text: str, verb_start: int) -> bool:
    start = 0
    for marker in _CLAUSE:
        position = text.rfind(marker, 0, verb_start)
        if position >= start:
            start = position + 1
    return _INFO.search(text[start:verb_start]) is not None


def _visible(text: str) -> str:
    """Blank quoted, backticked, and fenced regions so they cannot match."""
    chars = list(text)
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("```", index):
            end = text.find("```", index + 3)
            stop = length if end < 0 else end + 3
            _blank(chars, index, stop)
            index = stop
            continue
        quote = _quote_at(text, index)
        if quote:
            end = text.find(quote, index + len(quote))
            stop = length if end < 0 else end + len(quote)
            _blank(chars, index, stop)
            index = stop
            continue
        index += 1
    return "".join(chars)


def _quote_at(text: str, index: int) -> str:
    character = text[index]
    if character in {'"', "`"}:
        return character
    if character == "'" and (index == 0 or not text[index - 1].isalnum()):
        return character
    return ""


def _blank(chars: list[str], start: int, stop: int) -> None:
    for index in range(start, stop):
        chars[index] = " "
