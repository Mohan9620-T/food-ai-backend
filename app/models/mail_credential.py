from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text

from app.database.database import Base


class MailCredential(Base):
    """Rotated provider refresh tokens, encrypted before they reach the database."""

    __tablename__ = "mail_credentials"

    configuration_id = Column(String(64), primary_key=True)
    encrypted_token = Column(Text, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
