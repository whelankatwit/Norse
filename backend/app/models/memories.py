"""Request and response schemas for long-term memories."""

from pydantic import BaseModel, Field, field_validator


def _clean_content(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("content must not be blank")
    return cleaned


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=500)

    @field_validator("content")
    @classmethod
    def content_is_not_blank(cls, value: str) -> str:
        return _clean_content(value)


class MemoryUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=500)

    @field_validator("content")
    @classmethod
    def content_is_not_blank(cls, value: str) -> str:
        return _clean_content(value)


class MemoryRecord(BaseModel):
    id: str
    content: str
    source_conversation_id: str | None = None
    created_at: str
    updated_at: str


class MemoryConflict(BaseModel):
    memory_id: str
    existing: str
    stated: str
