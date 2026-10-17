from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String

from app.database.database import Base

PURPOSE_SIGNUP_VERIFICATION = "signup_verification"
PURPOSE_PASSWORD_RESET = "password_reset"


class EmailOtp(Base):
    """A one-time 6-digit code emailed for signup verification or password reset.

    Only the hash is stored (never the plaintext code); see hash_refresh_token,
    the same digest already used for refresh tokens.
    """

    __tablename__ = "email_otps"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose = Column(String(30), nullable=False)
    otp_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (Index("ix_email_otps_user_purpose", "user_id", "purpose"),)
