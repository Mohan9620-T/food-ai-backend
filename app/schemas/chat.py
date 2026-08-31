from typing import Literal

from pydantic import BaseModel, Field


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatHistoryMessage] = Field(default_factory=list)
    reference_history: list[ChatHistoryMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    response: str
    session_id: int
