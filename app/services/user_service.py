from typing import cast

from sqlalchemy.orm import Session

from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate
from app.services.email_service import EmailService
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
    verify_password,
)


class EmailAlreadyExistsError(ValueError):
    pass


class UserService:
    def __init__(self, email_service: EmailService | None = None):
        self.repository = UserRepository()
        self.refresh_tokens = RefreshTokenRepository()
        self.email_service = email_service or EmailService()

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
        email_sent = self.email_service.send_new_account_welcome(
            recipient=user.email,
            fullname=user.fullname,
        )
        return {
            "id": created_user.id,
            "fullname": created_user.fullname,
            "email": created_user.email,
            "email_sent": email_sent,
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
        return self._issue_tokens(db, user, remember_me)

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
            }
        )
