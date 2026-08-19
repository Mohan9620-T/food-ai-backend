from sqlalchemy.orm import Session
from datetime import timedelta

from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate
from app.services.email_service import EmailService
from app.utils.security import (
    REMEMBERED_ACCESS_TOKEN_EXPIRE_DAYS,
    verify_password,
    create_access_token,
)


class UserService:

    def __init__(self, email_service: EmailService | None = None):
        self.repository = UserRepository()
        self.email_service = email_service or EmailService()

    def create_user(self, db: Session, user: UserCreate):

        existing_user = self.repository.get_user_by_email(
            db,
            user.email
        )

        if existing_user:
            raise Exception("Email already exists")

        created_user = self.repository.create_user(
            db=db,
            fullname=user.fullname,
            email=user.email,
            password=user.password
        )

        email_sent = self.email_service.send_new_account_credentials(
            recipient=user.email,
            fullname=user.fullname,
            password=user.password,
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
    ):

        user = self.repository.get_user_by_email(db, email)

        if not user or not verify_password(password, user.password):
            return None

        expires_delta = (
            timedelta(days=REMEMBERED_ACCESS_TOKEN_EXPIRE_DAYS)
            if remember_me else None
        )
        return create_access_token(
            data={
                "sub": str(user.id),
                "email": user.email,
                "fullname": user.fullname,
            },
            expires_delta=expires_delta,
        )
