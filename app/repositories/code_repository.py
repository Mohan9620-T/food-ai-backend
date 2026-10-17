from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.code_repository import CodeRepository


class CodeRepositoryStore:
    def list(self, db: Session, user_id: int):
        return db.query(CodeRepository).filter_by(user_id=user_id).order_by(CodeRepository.id).all()

    def owned(self, db: Session, user_id: int, repo_id: int):
        row = db.query(CodeRepository).filter_by(id=repo_id, user_id=user_id).first()
        if row is None:
            raise HTTPException(404, "Repository not found")
        return row

    def create(self, db: Session, user_id: int, name: str, path: str):
        if db.query(CodeRepository).filter_by(user_id=user_id, path=path).first():
            raise HTTPException(409, "Repository already connected")
        if db.query(CodeRepository).filter_by(user_id=user_id).count() >= 20:
            raise HTTPException(409, "Repository limit reached")
        row = CodeRepository(user_id=user_id, name=name, path=path, snapshot={})
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
