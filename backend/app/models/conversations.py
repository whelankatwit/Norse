"""Request and response schemas for conversations."""

from pydantic import BaseModel, Field, field_validator


class ConversationCreate(BaseModel):
    title: str | None = None

    @field_validator("title")
    @classmethod
    def blank_title_is_missing(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class MessageRecord(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class ConversationDetail(ConversationSummary):
    messages: list[MessageRecord]


class MessageCreate(BaseModel):
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def message_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class MessageExchange(BaseModel):
    conversation_id: str
    user_message: MessageRecord
    assistant_message: MessageRecord
