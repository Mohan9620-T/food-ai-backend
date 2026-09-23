from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database.database import Base


class AccountPasswordLink(Base):
    __tablename__ = "account_password_links"

    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
