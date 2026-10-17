from sqlalchemy.orm import Session

from app.models.user import User
from app.utils.security import hash_password


class UserRepository:
    def create_user(self, db: Session, fullname: str, email: str, password: str):
        user = User(fullname=fullname, email=email, password=hash_password(password))

        db.add(user)
        db.commit()
        db.refresh(user)

        from app.config import settings

        if settings.ENABLE_WORKSPACES:
            from app.repositories.workspace_repository import WorkspaceRepository

            WorkspaceRepository().default(db, int(user.id))

        return user

    def get_user_by_email(self, db: Session, email: str):
        return db.query(User).filter(User.email == email).first()

    def get_user_by_id(self, db: Session, user_id: int):
        return db.query(User).filter(User.id == user_id).first()
