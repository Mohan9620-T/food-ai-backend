import base64
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    event,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Session, relationship

from app.database.database import Base
from app.storage import read_blob, store_blob


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_user_pin_order", "user_id", "is_pinned", "pin_order"),
    )

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
    is_pinned = Column(Boolean, nullable=False, default=False)
    pin_order = Column(Integer, nullable=True)

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
    _image_data = Column("image_data", LargeBinary, nullable=True)
    storage_key = Column(String(512), nullable=True)
    citations = Column(JSON, nullable=True)
    file_clarification = Column(JSON, nullable=True)
    image_content_type = Column(String(50), nullable=True)
    is_internal = Column(Boolean, nullable=False, default=False)
    automation = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    session = relationship("ChatSession", back_populates="messages")
    additional_images = relationship(
        "ChatImageAttachment", cascade="all, delete-orphan", order_by="ChatImageAttachment.id"
    )
    document_attachment = relationship(
        "ChatDocumentAttachment",
        back_populates="message",
        uselist=False,
        cascade="all, delete-orphan",
    )
    sources = relationship(
        "ChatMessageSource", cascade="all, delete-orphan", order_by="ChatMessageSource.position"
    )

    @property
    def source_images(self) -> list[str]:
        return [source.image_url for source in self.sources if source.image_url][:2]

    @property
    def search_query(self) -> str | None:
        return self.sources[0].query if self.sources else None

    @property
    def image_url(self) -> str | None:
        if not self.image_data or not self.image_content_type:
            return None
        encoded = base64.b64encode(self.image_data).decode("ascii")
        return f"data:{self.image_content_type};base64,{encoded}"

    @property
    def image_urls(self) -> list[str]:
        first = self.image_url
        return ([first] if first else []) + [image.image_url for image in self.additional_images]

    @hybrid_property
    def image_data(self):
        return read_blob(cast(str | None, self.storage_key), cast(bytes | None, self._image_data))

    @image_data.inplace.setter
    def _set_image_data(self, value):
        self._image_data = value
        setattr(self, "storage_key", None)

    @image_data.inplace.expression
    def _image_data_expression(cls):
        return cls._image_data


class ChatImageAttachment(Base):
    """Additional images on one user turn; the first retains its legacy storage."""

    __tablename__ = "chat_image_attachments"

    id = Column(Integer, primary_key=True)
    message_id = Column(
        Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    _image_data = Column("image_data", LargeBinary, nullable=True)
    storage_key = Column(String(512), nullable=True)
    content_type = Column(String(50), nullable=False)

    @property
    def image_url(self) -> str:
        encoded = base64.b64encode(self.image_data).decode("ascii")
        return f"data:{self.content_type};base64,{encoded}"

    @hybrid_property
    def image_data(self):
        return read_blob(cast(str | None, self.storage_key), cast(bytes | None, self._image_data))

    @image_data.inplace.setter
    def _set_image_data(self, value):
        self._image_data = value
        setattr(self, "storage_key", None)

    @image_data.inplace.expression
    def _image_data_expression(cls):
        return cls._image_data


class ChatMessageSource(Base):
    """One structured web-search source backing a bot answer."""

    __tablename__ = "chat_message_sources"

    id = Column(Integer, primary_key=True)
    message_id = Column(
        Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position = Column(Integer, nullable=False)
    title = Column(String(512), nullable=False)
    url = Column(String(2048), nullable=False)
    domain = Column(String(255), nullable=False)
    snippet = Column(Text, nullable=True)
    image_url = Column(String(2048), nullable=True)
    query = Column(String(255), nullable=True)


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
    _file_data = Column("file_data", LargeBinary, nullable=True)
    storage_key = Column(String(512), nullable=True)
    processing_status = Column(String(20), nullable=False, default="ready", server_default="ready")
    processing_stage = Column(String(50), nullable=True)
    processing_error = Column(Text, nullable=True)
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

    @hybrid_property
    def file_data(self):
        return read_blob(cast(str | None, self.storage_key), cast(bytes | None, self._file_data))

    @file_data.inplace.setter
    def _set_file_data(self, value):
        self._file_data = value
        setattr(self, "storage_key", None)

    @file_data.inplace.expression
    def _file_data_expression(cls):
        return cls._file_data


@event.listens_for(Session, "before_flush")
def _externalize_blobs(session, flush_context, instances):
    for record in list(session.new) + list(session.dirty):
        if isinstance(record, ChatDocumentAttachment):
            field, kind = "_file_data", record.content_type
        elif isinstance(record, (ChatMessageRecord, ChatImageAttachment)):
            field = "_image_data"
            kind = (
                record.image_content_type
                if isinstance(record, ChatMessageRecord)
                else record.content_type
            )
        else:
            continue
        data = getattr(record, field)
        if data and not record.storage_key:
            key = store_blob(data, str(kind or "application/octet-stream"))
            if key:
                record.storage_key = key
                setattr(record, field, None)
