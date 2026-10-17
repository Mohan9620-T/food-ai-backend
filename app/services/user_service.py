from typing import cast

from sqlalchemy.orm import Session

from app.config import settings
from app.models.email_otp import PURPOSE_PASSWORD_RESET, PURPOSE_SIGNUP_VERIFICATION
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate
from app.services.account_password_service import AccountPasswordService, public_app_url
from app.services.email_otp_service import EmailOtpService, OtpInvalidError
from app.services.email_service import EmailService
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

__all__ = ["EmailAlreadyExistsError", "EmailNotVerifiedError", "UserService"]


class EmailAlreadyExistsError(ValueError):
    pass


class EmailNotVerifiedError(ValueError):
    """Credentials are correct, but the account's first-login OTP is not done yet."""


class UserService:
    def __init__(self, email_service: EmailService | None = None):
        self.repository = UserRepository()
        self.refresh_tokens = RefreshTokenRepository()
        self.email_service = email_service or EmailService()
        self.account_passwords = AccountPasswordService(self.email_service)
        self.email_otps = EmailOtpService(self.email_service)

    def create_user(self, db: Session, user: UserCreate):
        existing_user = self.repository.get_user_by_email(db, user.email)
        if existing_user:
            raise EmailAlreadyExistsError("Email already exists")

        created_user = self.repository.create_user(
            db=db,
            fullname=user.fullname,
            email=user.email,
            password=user.password,
        )
        # The welcome-email set-password link is unrelated to first-login OTP
        # verification and must keep working exactly as it does today.
        password_link = (
            self.account_passwords.create_link(db, created_user.id)
            if settings.ENABLE_EMAIL_DELIVERY
            else None
        )
        base_url = public_app_url()
        email_sent = settings.ENABLE_EMAIL_DELIVERY and self.email_service.send_new_account_welcome(
            recipient=user.email,
            fullname=user.fullname,
            password_link=password_link,
            login_url=f"{base_url}/login" if base_url else None,
        )
        # Keep email verification state honest when local password-only login
        # is enabled. Do not generate or send a code in that mode.
        requires_otp = settings.email_otp_enabled()
        otp_email_sent = (
            self.email_otps.generate_and_send(db, created_user, PURPOSE_SIGNUP_VERIFICATION)
            if requires_otp
            else False
        )
        return {
            "id": created_user.id,
            "fullname": created_user.fullname,
            "email": created_user.email,
            "email_sent": email_sent,
            "otp_email_sent": otp_email_sent,
            "requires_otp": requires_otp,
        }

    def authenticate_user(
        self,
        db: Session,
        email: str,
        password: str,
        remember_me: bool = False,
    ) -> dict[str, str] | None:
        user = self.repository.get_user_by_email(db, email)
        if not user or not verify_password(password, user.password):
            return None
        if settings.email_otp_enabled() and not user.is_email_verified:
            raise EmailNotVerifiedError("Verify your email with the code we sent you.")
        return self._issue_tokens(db, user, remember_me)

    def verify_signup_otp(self, db: Session, email: str, code: str) -> dict[str, str] | None:
        user = self.repository.get_user_by_email(db, email)
        if user is None:
            # Same generic failure as a wrong code: do not reveal whether the
            # account exists through a different error for this case.
            raise OtpInvalidError("Invalid or expired code.")
        self.email_otps.verify(db, user, code, PURPOSE_SIGNUP_VERIFICATION)
        user.is_email_verified = True
        db.commit()
        db.refresh(user)
        return self._issue_tokens(db, user, remember_me=False)

    def resend_otp(self, db: Session, email: str, purpose: str) -> None:
        # Check global sender configuration before account lookup so this error
        # cannot reveal whether an address is registered.
        self.email_otps.ensure_configured()
        user = self.repository.get_user_by_email(db, email)
        if user is None:
            return
        if purpose == PURPOSE_SIGNUP_VERIFICATION and user.is_email_verified:
            return
        self.email_otps.generate_and_send(db, user, purpose)

    def forgot_password(self, db: Session, email: str) -> None:
        self.email_otps.ensure_configured()
        user = self.repository.get_user_by_email(db, email)
        if user is None:
            return
        self.email_otps.generate_and_send(db, user, PURPOSE_PASSWORD_RESET)

    def reset_password_with_otp(
        self, db: Session, email: str, code: str, new_password: str
    ) -> None:
        user = self.repository.get_user_by_email(db, email)
        if user is None:
            raise OtpInvalidError("Invalid or expired code.")
        self.email_otps.verify(db, user, code, PURPOSE_PASSWORD_RESET)
        user.password = hash_password(new_password)
        user.auth_version = int(user.auth_version) + 1
        db.commit()
        self.refresh_tokens.revoke_all_for_user(db, cast(int, user.id))
        self.email_service.send_text(
            recipient=str(user.email),
            subject="Your Food AI password was changed",
            body=(
                "Your Food AI password was changed using an emailed verification code.\n"
                "Your previous sessions have been signed out.\n"
                "If this was not you, contact your Food AI administrator.\n"
            ),
        )

    def refresh_access_token(self, db: Session, raw_refresh_token: str) -> dict[str, str] | None:
        stored_token = self.refresh_tokens.get_active(db, hash_refresh_token(raw_refresh_token))
        if not stored_token:
            return None
        user = self.repository.get_user_by_id(db, cast(int, stored_token.user_id))
        if not user:
            return None
        return {
            "access_token": self._create_user_access_token(user),
            "refresh_token": raw_refresh_token,
        }

    def revoke_refresh_token(self, db: Session, raw_refresh_token: str) -> bool:
        stored_token = self.refresh_tokens.get_by_hash(db, hash_refresh_token(raw_refresh_token))
        if not stored_token or stored_token.revoked_at is not None:
            return False
        self.refresh_tokens.revoke(db, stored_token)
        return True

    def _issue_tokens(self, db: Session, user, remember_me: bool) -> dict[str, str]:
        raw_refresh_token, expires_at = create_refresh_token(remember_me)
        self.refresh_tokens.create(
            db,
            user.id,
            hash_refresh_token(raw_refresh_token),
            expires_at,
        )
        return {
            "access_token": self._create_user_access_token(user),
            "refresh_token": raw_refresh_token,
        }

    @staticmethod
    def _create_user_access_token(user) -> str:
        return create_access_token(
            data={
                "sub": str(user.id),
                "email": user.email,
                "fullname": user.fullname,
                "auth_version": user.auth_version,
            }
        )
