from typing import Literal

from pydantic import BaseModel, Field


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatHistoryMessage] = Field(default_factory=list)
    reference_history: list[ChatHistoryMessage] = Field(default_factory=list)
    # Internal optional image payload for future multimodal transports. The existing
    # JSON /chat endpoint remains text-only; uploaded files use /chat/vision.
    image_data: bytes | None = Field(default=None, exclude=True, repr=False)


class ChatResponse(BaseModel):
    response: str
    session_id: int
