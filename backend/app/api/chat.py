"""Chat route."""

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from app.agent.ollama import OllamaFailure, chat, failure_response

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def message_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class ChatResponse(BaseModel):
    message: str


@router.post("/chat", response_model=ChatResponse)
async def chat_message(body: ChatRequest) -> ChatResponse:
    try:
        reply = await chat([{"role": "user", "content": body.message}])
    except OllamaFailure as exc:
        raise failure_response(exc) from exc

    return ChatResponse(message=reply)
