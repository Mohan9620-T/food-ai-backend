import base64
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False, default="New chat")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    latest_document_id = Column(
        Integer,
        ForeignKey("chat_document_attachments.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        index=True,
    )
    # Rolling summary of turns that have fallen outside the visible history
    # window, plus the id of the last message folded into it (a watermark so
    # the same turns are never re-summarized). Structured facts extracted from
    # the conversation (topic, decisions, constraints, ...) live in
    # memory_facts as a single JSON blob rather than normalized columns.
    rolling_summary = Column(Text, nullable=True)
    summary_covers_through_message_id = Column(Integer, nullable=True)
    memory_facts = Column(JSON, nullable=True)

    messages = relationship(
        "ChatMessageRecord",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="(ChatMessageRecord.created_at, ChatMessageRecord.id)",
    )

    @property
    def public_messages(self):
        """Messages that are safe to expose in chat history."""
        visible = []
        pending_attachments: list[ChatDocumentAttachment] = []
        for message in self.messages:
            if message.sender == "user":
                pending_attachments = []
            attachment = message.document_attachment
            if attachment is not None and attachment.kind == "generated":
                pending_attachments.append(attachment)
            if not message.is_internal:
                message.history_attachments = list(pending_attachments)
                visible.append(message)
                pending_attachments = []
        return visible


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    sender = Column(String(10), nullable=False)  # "user" or "bot"
    content = Column(Text, nullable=False)
    image_data = Column(LargeBinary, nullable=True)
    image_content_type = Column(String(50), nullable=True)
    is_internal = Column(Boolean, nullable=False, default=False)
    automation = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    session = relationship("ChatSession", back_populates="messages")
    document_attachment = relationship(
        "ChatDocumentAttachment",
        back_populates="message",
        uselist=False,
        cascade="all, delete-orphan",
    )

    @property
    def image_url(self) -> str | None:
        if not self.image_data or not self.image_content_type:
            return None
        encoded = base64.b64encode(self.image_data).decode("ascii")
        return f"data:{self.image_content_type};base64,{encoded}"


class ChatDocumentAttachment(Base):
    __tablename__ = "chat_document_attachments"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(
        Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id = Column(
        Integer,
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    kind = Column(String(20), nullable=False, default="uploaded")
    file_data = Column(LargeBinary, nullable=False)
    raw_text = Column(Text, nullable=True)
    structured_summary = Column(Text, nullable=True)
    generation_metadata = Column(JSON, nullable=True)
    extracted_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    message = relationship("ChatMessageRecord", back_populates="document_attachment")

    @property
    def provenance(self) -> str | None:
        return cast(dict, self.generation_metadata or {}).get("provenance")

    @property
    def source_document_ids(self) -> list[int]:
        return cast(dict, self.generation_metadata or {}).get("source_document_ids", [])

    @property
    def assumptions(self) -> list[str]:
        return cast(dict, self.generation_metadata or {}).get("assumptions", [])
