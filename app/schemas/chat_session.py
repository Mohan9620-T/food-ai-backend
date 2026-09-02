from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChatMessageOut(BaseModel):
    id: int
    sender: str
    content: str
    created_at: datetime
    image_url: str | None = None

    class Config:
        from_attributes = True


class ChatSessionOut(BaseModel):
    id: int
    title: str
    updated_at: datetime

    class Config:
        from_attributes = True


class ChatSessionDetailOut(ChatSessionOut):
    messages: list[ChatMessageOut] = []


class ChatSessionCreate(BaseModel):
    title: str = "New chat"


class ChatSessionRename(BaseModel):
    title: str


class ChatMessageImport(BaseModel):
    sender: Literal["user", "bot"]
    content: str


class ChatSessionImport(BaseModel):
    messages: list[ChatMessageImport] = Field(default_factory=list)
