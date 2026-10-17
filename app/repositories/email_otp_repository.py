from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.email_otp import EmailOtp
from app.models.user import User


class EmailOtpRepository:
    def lock_user(self, db: Session, user_id: int) -> None:
        # Serialize issuance/resend/verification for this account across workers.
        db.query(User.id).filter(User.id == user_id).with_for_update().one()

    def invalidate_active(
        self, db: Session, user_id: int, purpose: str, *, commit: bool = True
    ) -> None:
        """Consume any existing unconsumed OTP so only one stays active per purpose."""
        db.query(EmailOtp).filter(
            EmailOtp.user_id == user_id,
            EmailOtp.purpose == purpose,
            EmailOtp.consumed_at.is_(None),
        ).update({EmailOtp.consumed_at: datetime.now(timezone.utc)}, synchronize_session=False)
        if commit:
            db.commit()

    def create(
        self,
        db: Session,
        user_id: int,
        purpose: str,
        otp_hash: str,
        expires_at: datetime,
        max_attempts: int,
        *,
        commit: bool = True,
    ) -> EmailOtp:
        otp = EmailOtp(
            user_id=user_id,
            purpose=purpose,
            otp_hash=otp_hash,
            expires_at=expires_at,
            max_attempts=max_attempts,
        )
        db.add(otp)
        if commit:
            db.commit()
            db.refresh(otp)
        else:
            db.flush()
        return otp

    def get_active(self, db: Session, user_id: int, purpose: str) -> EmailOtp | None:
        now = datetime.now(timezone.utc)
        return (
            db.query(EmailOtp)
            .filter(
                EmailOtp.user_id == user_id,
                EmailOtp.purpose == purpose,
                EmailOtp.consumed_at.is_(None),
                EmailOtp.expires_at > now,
            )
            .order_by(EmailOtp.created_at.desc())
            .first()
        )

    def increment_attempts(self, db: Session, otp: EmailOtp) -> int:
        otp.attempts = otp.attempts + 1  # type: ignore[assignment]
        db.commit()
        db.refresh(otp)
        return int(otp.attempts)

    def consume(self, db: Session, otp: EmailOtp) -> None:
        otp.consumed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        db.commit()
