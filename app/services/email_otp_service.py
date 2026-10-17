import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.email_otp import PURPOSE_PASSWORD_RESET, PURPOSE_SIGNUP_VERIFICATION
from app.models.user import User
from app.repositories.email_otp_repository import EmailOtpRepository
from app.services.email_service import EmailService
from app.utils.security import hash_refresh_token

logger = logging.getLogger(__name__)

__all__ = [
    "PURPOSE_PASSWORD_RESET",
    "PURPOSE_SIGNUP_VERIFICATION",
    "EmailOtpService",
    "OtpInvalidError",
    "OtpLockedError",
    "OtpDeliveryUnavailableError",
]


class OtpInvalidError(ValueError):
    """Wrong code, no active OTP, or an expired one - always the same generic message."""


class OtpLockedError(ValueError):
    """max_attempts reached for the active OTP; the caller must request a new one."""


class OtpDeliveryUnavailableError(ValueError):
    """The sender is unavailable for every account, independent of registration status."""


class EmailOtpService:
    def __init__(self, email_service: EmailService | None = None):
        self.repository = EmailOtpRepository()
        self.email_service = email_service or EmailService()

    def ensure_configured(self) -> None:
        errors = self.email_service.configuration_errors()
        if errors:
            logger.warning(
                "otp.delivery_unavailable",
                extra={
                    "email_provider": self.email_service.provider,
                    "configuration_errors": errors,
                },
            )
            raise OtpDeliveryUnavailableError(
                "Verification email is unavailable right now. Please try again later or contact support."
            )

    def generate_and_send(self, db: Session, user: User, purpose: str) -> bool:
        user_id = int(user.id)
        recipient, fullname = str(user.email), str(user.fullname)
        code = f"{secrets.randbelow(1_000_000):06d}"
        try:
            self.repository.lock_user(db, user_id)
            # Publish the replacement only if the provider accepted it. A failed
            # resend must not destroy the previous usable code or retain an unsent one.
            self.repository.invalidate_active(db, user_id, purpose, commit=False)
            self.repository.create(
                db,
                user_id=user_id,
                purpose=purpose,
                otp_hash=hash_refresh_token(code),
                expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
                max_attempts=settings.OTP_MAX_ATTEMPTS,
                commit=False,
            )
            sent = self.email_service.send_otp_email(
                recipient=recipient, fullname=fullname, otp=code, purpose=purpose
            )
            if sent:
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            raise
        logger.info(
            "otp.email_accepted" if sent else "otp.delivery_failed",
            extra={"user_id": user_id, "purpose": purpose, "email_sent": sent},
        )
        return sent

    def verify(self, db: Session, user: User, code: str, purpose: str) -> None:
        """Raise OtpInvalidError or OtpLockedError on failure; consume the OTP on success."""
        self.repository.lock_user(db, int(user.id))
        otp = self.repository.get_active(db, int(user.id), purpose)
        if otp is None:
            logger.info("otp.verify_failed", extra={"user_id": user.id, "reason": "no_active_otp"})
            raise OtpInvalidError("Invalid or expired code.")
        if int(otp.attempts) >= int(otp.max_attempts):
            logger.info("otp.verify_failed", extra={"user_id": user.id, "reason": "locked"})
            raise OtpLockedError("Too many incorrect attempts. Request a new code.")
        if not secrets.compare_digest(str(otp.otp_hash), hash_refresh_token(code)):
            self.repository.increment_attempts(db, otp)
            logger.info("otp.verify_failed", extra={"user_id": user.id, "reason": "mismatch"})
            raise OtpInvalidError("Invalid or expired code.")
        self.repository.consume(db, otp)
        logger.info("otp.verified", extra={"user_id": user.id, "purpose": purpose})
