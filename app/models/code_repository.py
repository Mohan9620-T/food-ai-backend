"""Owner-scoped code snapshots, independent of document retrieval."""

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, UniqueConstraint

from app.database.database import Base


class CodeRepository(Base):
    __tablename__ = "code_repositories"
    __table_args__ = (UniqueConstraint("user_id", "path"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name = Column(String(100), nullable=False)
    path = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False, default="connected")
    indexed_at = Column(String(40), nullable=True)
    snapshot = Column(JSON, nullable=False, default=dict)
