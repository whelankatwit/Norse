"""Conversation routes."""

import sqlite3

from fastapi import APIRouter, HTTPException

from app.agent.loop import TOOL_GUIDANCE, run_tools
from app.agent.memory import ExtractionError, extract_memories, memory_prompt
from app.agent.ollama import (
    OllamaError,
    OllamaModelNotInstalledError,
    OllamaUnavailableError,
)
from app.database.sqlite import (
    ConversationNotFoundError,
    add_memories,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_memories,
    list_messages,
    save_exchange,
)
from app.models.conversations import (
    ConversationCreate,
    ConversationDetail,
    ConversationSummary,
    MessageCreate,
    MessageExchange,
    MessageRecord,
    ToolRequest,
    ToolResult,
    ToolUse,
)
from app.models.memories import MemoryConflict, MemoryRecord as StoredMemory

router = APIRouter()


def _require_conversation(conversation_id: str) -> dict[str, str]:
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.post("/conversations", response_model=ConversationSummary)
def create_conversation_route(body: ConversationCreate | None = None) -> ConversationSummary:
    title = body.title if body is not None else None
    return ConversationSummary.model_validate(create_conversation(title))


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations_route() -> list[ConversationSummary]:
    return [ConversationSummary.model_validate(row) for row in list_conversations()]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation_route(conversation_id: str) -> ConversationDetail:
    conversation = _require_conversation(conversation_id)
    messages = [MessageRecord.model_validate(row) for row in list_messages(conversation_id)]
    return ConversationDetail(**conversation, messages=messages)


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation_route(conversation_id: str) -> None:
    if not delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageExchange,
)
async def create_message_route(
    conversation_id: str,
    body: MessageCreate,
) -> MessageExchange:
    _require_conversation(conversation_id)
    memories = list_memories()
    history = [
        {"role": message["role"], "content": message["content"]}
        for message in list_messages(conversation_id)
    ]
    history.append({"role": "user", "content": body.message})
    prompt = memory_prompt(memories)
    guidance = "\n\n".join(part for part in (TOOL_GUIDANCE, prompt) if part)
    model_messages = [{"role": "system", "content": guidance}, *history]

    try:
        turn = await run_tools(model_messages)
    except OllamaUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OllamaModelNotInstalledError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        user_message, assistant_message = save_exchange(
            conversation_id,
            body.message,
            turn.reply,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc

    memories_saved: list[StoredMemory] = []
    memory_conflicts: list[MemoryConflict] = []
    try:
        extraction = await extract_memories(body.message, memories)
        memories_saved = [
            StoredMemory.model_validate(row)
            for row in add_memories(extraction.add, conversation_id)
        ]
        memory_conflicts = [
            MemoryConflict.model_validate(conflict) for conflict in extraction.conflicts
        ]
    except (OllamaError, ExtractionError, sqlite3.Error):
        memories_saved = []
        memory_conflicts = []

    return MessageExchange(
        conversation_id=conversation_id,
        user_message=MessageRecord.model_validate(user_message),
        assistant_message=MessageRecord.model_validate(assistant_message),
        memories_saved=memories_saved,
        memory_conflicts=memory_conflicts,
        tool_uses=[ToolUse.model_validate(use) for use in turn.tool_uses],
        tool_results=[ToolResult.model_validate(result) for result in turn.tool_results],
        tool_requests=[ToolRequest.model_validate(request) for request in turn.tool_requests],
    )
