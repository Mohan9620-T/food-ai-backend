from sqlalchemy.orm import Session

from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate
from app.utils.security import verify_password, create_access_token


class UserService:

    def __init__(self):
        self.repository = UserRepository()

    def create_user(self, db: Session, user: UserCreate):

        existing_user = self.repository.get_user_by_email(
            db,
            user.email
        )

        if existing_user:
            raise Exception("Email already exists")

        return self.repository.create_user(
            db=db,
            fullname=user.fullname,
            email=user.email,
            password=user.password
        )

    def authenticate_user(self, db: Session, email: str, password: str):

        user = self.repository.get_user_by_email(db, email)

        if not user or not verify_password(password, user.password):
            return None

        return create_access_token(data={"sub": str(user.id), "email": user.email})