from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.database.database import Base


class UserMemoryFact(Base):
    """A discrete fact the user explicitly stated in chat (allergy, dislike, goal,
    constraint, ...), distinct from the per-session rolling summary so it survives
    across sessions instead of aging out of the summarization window."""

    __tablename__ = "user_memory_facts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(30), nullable=False)
    fact_text = Column(String(300), nullable=False)
    source_session_id = Column(
        Integer, ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "category", "fact_text", name="uq_user_memory_fact"),
    )
