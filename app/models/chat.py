import base64
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text
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

    messages = relationship(
        "ChatMessageRecord",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessageRecord.created_at",
    )


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    sender = Column(String(10), nullable=False)  # "user" or "bot"
    content = Column(Text, nullable=False)
    image_data = Column(LargeBinary, nullable=True)
    image_content_type = Column(String(50), nullable=True)
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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    message = relationship("ChatMessageRecord", back_populates="document_attachment")
