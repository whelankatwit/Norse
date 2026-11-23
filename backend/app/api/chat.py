"""Chat route."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.agent.ollama import (
    OllamaError,
    OllamaModelNotInstalledError,
    OllamaUnavailableError,
    chat,
)

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
    except OllamaUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OllamaModelNotInstalledError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatResponse(message=reply)
