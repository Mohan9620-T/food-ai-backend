from sqlalchemy.orm import Session

from app.models.chat import ChatSession, ChatMessageRecord


class ChatRepository:

    def create_session(self, db: Session, user_id: int, title: str = "New chat") -> ChatSession:
        session = ChatSession(user_id=user_id, title=title)
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    def get_sessions_for_user(self, db: Session, user_id: int) -> list[ChatSession]:
        return (
            db.query(ChatSession)
            .filter(ChatSession.user_id == user_id)
            .order_by(ChatSession.updated_at.desc())
            .all()
        )

    def get_session(self, db: Session, session_id: int, user_id: int) -> ChatSession | None:
        return (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .first()
        )

    def add_message(self, db: Session, session_id: int, sender: str, content: str) -> ChatMessageRecord:
        message = ChatMessageRecord(session_id=session_id, sender=sender, content=content)
        db.add(message)
        db.commit()
        db.refresh(message)

        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session:
            db.add(session)
            db.commit()

        return message

    def rename_session(self, db: Session, session_id: int, user_id: int, title: str) -> ChatSession | None:
        session = self.get_session(db, session_id, user_id)
        if not session:
            return None
        session.title = title
        db.commit()
        db.refresh(session)
        return session

    def delete_session(self, db: Session, session_id: int, user_id: int) -> bool:
        session = self.get_session(db, session_id, user_id)
        if not session:
            return False
        db.delete(session)
        db.commit()
        return True