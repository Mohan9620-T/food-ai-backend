from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.chat import ChatDocumentAutomationState, ChatMessageSourceOut


class ChatDocumentAttachmentOut(BaseModel):
    id: int
    filename: str
    content_type: str
    file_size: int
    processing_status: str = "ready"
    processing_stage: str | None = None
    processing_error: str | None = None
    kind: Literal["uploaded", "generated"]
    provenance: Literal["uploaded_source", "general_knowledge"] | None = None
    source_document_ids: list[int] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ChatMessageOut(BaseModel):
    citations: list[dict] | None = None
    file_clarification: dict | None = None
    id: int
    sender: str
    content: str
    created_at: datetime
    image_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    document_attachment: ChatDocumentAttachmentOut | None = None
    attachments: list[ChatDocumentAttachmentOut] = Field(
        default_factory=list, validation_alias="history_attachments"
    )
    automation: ChatDocumentAutomationState | None = None
    sources: list[ChatMessageSourceOut] = Field(default_factory=list)
    source_images: list[str] = Field(default_factory=list)
    search_query: str | None = None

    class Config:
        from_attributes = True


class ChatSessionOut(BaseModel):
    id: int
    title: str
    updated_at: datetime
    is_pinned: bool = False
    pin_order: int | None = None

    class Config:
        from_attributes = True


class ChatSessionDetailOut(ChatSessionOut):
    messages: list[ChatMessageOut] = Field(default_factory=list, validation_alias="public_messages")


class ChatSessionsPageOut(BaseModel):
    conversations: list[ChatSessionOut]
    total: int
    limit: int
    offset: int


class ChatSessionPinRequest(BaseModel):
    pinned: bool


class ChatSessionCreate(BaseModel):
    title: str = "New chat"


class ChatSessionRename(BaseModel):
    title: str


class ChatMessageImport(BaseModel):
    sender: Literal["user", "bot"]
    content: str


class ChatSessionImport(BaseModel):
    messages: list[ChatMessageImport] = Field(default_factory=list)
