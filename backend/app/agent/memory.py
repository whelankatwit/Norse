"""Select durable facts from a user message and prepare them for the model."""

import json
import re
from dataclasses import dataclass

from app.agent.ollama import OllamaError, OllamaFailure, chat

MAX_FACTS = 5
MAX_FACT_LENGTH = 500
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "do",
    "does",
    "how",
    "is",
    "it",
    "me",
    "my",
    "or",
    "that",
    "the",
    "this",
    "user",
    "what",
    "when",
    "where",
    "who",
    "why",
    "you",
    "your",
}
NAME_STATEMENT = re.compile(r"\bmy name is\s+([A-Za-z][A-Za-z'-]*)", re.IGNORECASE)

EXTRACTION_INSTRUCTION = """Extract durable facts about the user from the user message below.
Return a JSON object and no other text. Each item in add is one sentence string.
Use this shape. Do not copy the angle-bracket placeholders into the result:
{"add": ["<one sentence the user just stated>"], "conflicts": [{"memory_id": "<id from existing memories>", "stated": "<the new claim the user just made>"}]}
When nothing qualifies, return {"add": [], "conflicts": []}.
A question does not state a fact. Do not save placeholders.

Save a fact only when the user explicitly states it about themselves, their preferences, their projects, or an ongoing circumstance.
Write a stated name only as "The user's name is <name>."
Do not save generic descriptions such as "is a person".
Do not infer facts from questions. A question produces no facts.
Do not save temporary details, one-off requests, or ordinary conversation.
The assistant's replies are not included. Do not invent facts from assumptions.
When nothing is worth retaining, return empty lists.

Compare the user message with the existing memories.
Put a genuinely new fact in add.
If the user states something that conflicts with an existing memory, do not add or replace it. Put the new claim in conflicts. "stated" is that new claim, not the text already stored.
Skip facts that repeat an existing memory, including obvious wording differences that state the same fact.
Order add from the most durable and useful fact to the least.
Prefer identity, lasting preferences, projects, and ongoing circumstances over minor details.
Return at most 5 facts in add. When more than 5 qualify, keep the 5 most durable and useful.
"""

RANK_INSTRUCTION = """Choose which of these facts to keep.
The user explicitly stated each one.
Return a JSON object with this shape and no other text:
{"keep": []}

Keep at most 5 facts.
Order keep from the most durable and useful fact to the least.
Prefer identity, lasting preferences, projects, and ongoing circumstances over minor or temporary details.
Copy the fact text exactly from the list. Do not rewrite facts or add new ones.
"""


class ExtractionError(Exception):
    """The extraction response could not be stored."""


@dataclass(frozen=True)
class ExtractionResult:
    add: list[str]
    conflicts: list[dict[str, str]]


def normalize_fact(content: str) -> str:
    return " ".join(content.split()).casefold()


def memory_prompt(memories: list[dict[str, str | None]]) -> str | None:
    """Build the temporary system message that carries saved facts."""
    if not memories:
        return None
    lines = "\n".join(f"- {memory['content']}" for memory in memories)
    return (
        "Durable facts about the user. Use a fact when it applies to the request.\n"
        f"{lines}"
    )


def _load_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ExtractionError("extraction was not JSON")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ExtractionError("extraction was not JSON") from exc
    if not isinstance(value, dict):
        raise ExtractionError("extraction was not a JSON object")
    return value


def _fact_text(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("content", "fact", "stated"):
            text = value.get(key)
            if isinstance(text, str):
                value = text
                break
        else:
            return None
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > MAX_FACT_LENGTH:
        return None
    return cleaned


def _content_words(text: str) -> set[str]:
    words: set[str] = set()
    for raw in text.casefold().replace("'", " ").split():
        word = "".join(character for character in raw if character.isalnum())
        if len(word) >= 3 and word not in STOPWORDS:
            words.add(word)
    return words


def _grounded(fact: str, user_message: str) -> bool:
    if not (_content_words(fact) & _content_words(user_message)):
        return False
    stated_name = re.search(r"name is\s+([A-Za-z][A-Za-z'-]*)", fact, re.IGNORECASE)
    if stated_name and stated_name.group(1).casefold() not in user_message.casefold():
        return False
    return True


def _canonical_fact(fact: str, user_message: str) -> str:
    match = NAME_STATEMENT.search(user_message)
    if match and match.group(1).casefold() in fact.casefold():
        return f"The user's name is {match.group(1)}."
    return fact


def parse_extraction(
    raw: str,
    memories: list[dict[str, str | None]],
    user_message: str,
) -> tuple[list[str], list[dict[str, str]]]:
    """Validate an extraction payload. Invalid items are dropped."""
    payload = _load_json_object(raw)
    added = payload.get("add")
    conflicts = payload.get("conflicts")
    if not isinstance(added, list) or not isinstance(conflicts, list):
        raise ExtractionError("extraction was missing add or conflicts")

    existing_by_id = {str(memory["id"]): str(memory["content"]) for memory in memories}
    parsed_conflicts: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    swapped_ids: list[str] = []
    for item in conflicts:
        if not isinstance(item, dict):
            continue
        memory_id = item.get("memory_id")
        if not isinstance(memory_id, str) or memory_id not in existing_by_id:
            continue
        if memory_id in seen_ids or memory_id in swapped_ids:
            continue
        stated = _fact_text(item.get("stated"))
        if stated is None or not _grounded(stated, user_message):
            continue
        stated = _canonical_fact(stated, user_message)
        existing = existing_by_id[memory_id]
        if normalize_fact(stated) == normalize_fact(existing):
            swapped_ids.append(memory_id)
            continue
        seen_ids.add(memory_id)
        parsed_conflicts.append(
            {
                "memory_id": memory_id,
                "existing": existing,
                "stated": stated,
            }
        )

    conflicted = {normalize_fact(conflict["stated"]) for conflict in parsed_conflicts}
    seen = {normalize_fact(content) for content in existing_by_id.values()}
    seen.update(conflicted)
    facts: list[str] = []
    for item in added:
        cleaned = _fact_text(item)
        if cleaned is None or not _grounded(cleaned, user_message):
            continue
        cleaned = _canonical_fact(cleaned, user_message)
        key = normalize_fact(cleaned)
        if key in seen:
            continue
        seen.add(key)
        facts.append(cleaned)

    if swapped_ids and facts:
        if len(swapped_ids) == 1:
            memory_id = swapped_ids[0]
            existing = existing_by_id[memory_id]
            for fact in facts:
                parsed_conflicts.append(
                    {
                        "memory_id": memory_id,
                        "existing": existing,
                        "stated": fact,
                    }
                )
        facts = []

    stated_name = NAME_STATEMENT.search(user_message)
    if stated_name:
        name = stated_name.group(1)
        stated = f"The user's name is {name}."
        replaced_name = False
        for memory in memories:
            existing_name = re.search(
                r"name is\s+([A-Za-z][A-Za-z'-]*)",
                str(memory["content"]),
                re.IGNORECASE,
            )
            if existing_name is None or existing_name.group(1).casefold() == name.casefold():
                continue
            replaced_name = True
            memory_id = str(memory["id"])
            if any(conflict["memory_id"] == memory_id for conflict in parsed_conflicts):
                continue
            parsed_conflicts.append(
                {
                    "memory_id": memory_id,
                    "existing": str(memory["content"]),
                    "stated": stated,
                }
            )
        if replaced_name:
            facts = [fact for fact in facts if name.casefold() not in fact.casefold()]
    return facts, parsed_conflicts


def select_ranked_facts(candidates: list[str], keep: list[object]) -> list[str]:
    """Keep up to five candidates in the ranker's order."""
    available = {normalize_fact(fact): fact for fact in candidates}
    chosen: list[str] = []
    for item in keep:
        cleaned = _fact_text(item)
        if cleaned is None:
            continue
        fact = available.get(normalize_fact(cleaned))
        if fact is None or fact in chosen:
            continue
        chosen.append(fact)
        if len(chosen) == MAX_FACTS:
            break
    return chosen


async def _prioritize(facts: list[str]) -> list[str]:
    if len(facts) <= MAX_FACTS:
        return facts
    raw = await chat(
        [
            {
                "role": "user",
                "content": RANK_INSTRUCTION + "\nFacts:\n" + json.dumps(facts),
            }
        ],
        json_format=True,
        stage="memory_extraction",
    )
    payload = _load_json_object(raw)
    keep = payload.get("keep")
    if not isinstance(keep, list):
        raise ExtractionError("ranking was missing keep")
    chosen = select_ranked_facts(facts, keep)
    if not chosen:
        raise ExtractionError("ranking did not choose any facts")
    return chosen


async def extract_memories(
    user_message: str,
    memories: list[dict[str, str | None]],
) -> ExtractionResult:
    """Read one user message and return validated facts and conflicts."""
    existing = json.dumps(
        [{"id": memory["id"], "content": memory["content"]} for memory in memories]
    )
    raw = await chat(
        [
            {
                "role": "user",
                "content": (
                    f"{EXTRACTION_INSTRUCTION}\n"
                    f"Existing memories:\n{existing}\n\n"
                    f"User message:\n{user_message}"
                ),
            }
        ],
        json_format=True,
        stage="memory_extraction",
    )
    facts, conflicts = parse_extraction(raw, memories, user_message)
    try:
        facts = await _prioritize(facts)
    except (OllamaError, ExtractionError) as exc:
        if isinstance(exc, ExtractionError):
            OllamaFailure(
                "extraction_failed",
                stage="memory_extraction",
                error_type=type(exc).__name__,
            )
        facts = []
    return ExtractionResult(add=facts, conflicts=conflicts)
