from datetime import datetime
from pydantic import BaseModel


class ChatMessageOut(BaseModel):
    id: int
    sender: str
    content: str
    created_at: datetime

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