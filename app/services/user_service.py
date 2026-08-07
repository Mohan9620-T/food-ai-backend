from sqlalchemy.orm import Session

from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate


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