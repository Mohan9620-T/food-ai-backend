"""Workspace membership is separate from chat history and stored document bytes."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.database.database import Base


def now():
    return datetime.now(timezone.utc)


class Workspace(Base):
    __tablename__ = "workspaces"
    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(120), nullable=False)
    description = Column(String(500), nullable=False, default="")
    is_default = Column(Boolean, nullable=False, default=False)
    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=now)


class WorkspaceDocument(Base):
    __tablename__ = "workspace_documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "document_id"),
        UniqueConstraint("workspace_id", "checksum"),
    )
    id = Column(Integer, primary_key=True)
    workspace_id = Column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id = Column(
        Integer, ForeignKey("chat_document_attachments.id", ondelete="CASCADE"), nullable=False
    )
    checksum = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=now)


class WorkspaceSession(Base):
    __tablename__ = "workspace_sessions"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id = Column(
        Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    retrieval_mode = Column(String(10), nullable=False, default="all")


class WorkspaceSessionDocument(Base):
    __tablename__ = "workspace_session_documents"
    __table_args__ = (UniqueConstraint("workspace_session_id", "document_id"),)
    id = Column(Integer, primary_key=True)
    workspace_session_id = Column(
        Integer, ForeignKey("workspace_sessions.id", ondelete="CASCADE"), nullable=False
    )
    document_id = Column(
        Integer, ForeignKey("chat_document_attachments.id", ondelete="CASCADE"), nullable=False
    )


class WorkspaceBookmark(Base):
    __tablename__ = "workspace_bookmarks"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_type = Column(String(20), nullable=False)
    target_id = Column(Integer, nullable=False)
    note = Column(String(500), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=now)


class WorkspaceTimeline(Base):
    __tablename__ = "workspace_timeline"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type = Column(String(40), nullable=False)
    title = Column(String(300), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)


class WorkspaceSummary(Base):
    __tablename__ = "workspace_summaries"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=now)
