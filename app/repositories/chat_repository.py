from sqlalchemy.orm import Session

from app.models.chat import ChatMessageRecord, ChatSession


class ChatRepository:
    def get_message_history(
        self,
        db: Session,
        session_id: int,
        limit: int = 12,
    ) -> list[ChatMessageRecord]:
        messages = (
            db.query(ChatMessageRecord)
            .filter(ChatMessageRecord.session_id == session_id)
            .order_by(ChatMessageRecord.created_at.desc(), ChatMessageRecord.id.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(messages))

    def consolidate_sessions(self, db: Session, user_id: int) -> list[ChatSession]:
        sessions = (
            db.query(ChatSession)
            .filter(ChatSession.user_id == user_id)
            .order_by(ChatSession.created_at.asc(), ChatSession.id.asc())
            .all()
        )
        if not sessions:
            return []
        primary = sessions[0]
        redundant_ids = [session.id for session in sessions[1:]]
        if redundant_ids:
            db.query(ChatMessageRecord).filter(
                ChatMessageRecord.session_id.in_(redundant_ids)
            ).update(
                {ChatMessageRecord.session_id: primary.id},
                synchronize_session=False,
            )
            db.query(ChatSession).filter(ChatSession.id.in_(redundant_ids)).delete(
                synchronize_session=False
            )
            newest_message = (
                db.query(ChatMessageRecord)
                .filter(ChatMessageRecord.session_id == primary.id)
                .order_by(ChatMessageRecord.created_at.desc(), ChatMessageRecord.id.desc())
                .first()
            )
            if newest_message is not None:
                primary.updated_at = newest_message.created_at
        db.commit()
        db.expire_all()
        return self.get_sessions_for_user(db, user_id)

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

    def add_message(
        self, db: Session, session_id: int, sender: str, content: str
    ) -> ChatMessageRecord:
        message = ChatMessageRecord(session_id=session_id, sender=sender, content=content)
        db.add(message)
        db.commit()
        db.refresh(message)

        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session:
            db.add(session)
            db.commit()

        return message

    def add_turn(
        self,
        db: Session,
        session_id: int,
        user_content: str,
        bot_content: str,
        *,
        image_data: bytes | None = None,
        image_content_type: str | None = None,
    ) -> tuple[ChatMessageRecord, ChatMessageRecord]:
        user_message = ChatMessageRecord(
            session_id=session_id,
            sender="user",
            content=user_content,
            image_data=image_data,
            image_content_type=image_content_type,
        )
        bot_message = ChatMessageRecord(
            session_id=session_id,
            sender="bot",
            content=bot_content,
        )
        db.add_all([user_message, bot_message])
        db.commit()
        db.refresh(user_message)
        db.refresh(bot_message)
        return user_message, bot_message

    def import_messages(
        self,
        db: Session,
        session_id: int,
        messages: list[tuple[str, str]],
    ) -> list[ChatMessageRecord]:
        records = [
            ChatMessageRecord(session_id=session_id, sender=sender, content=content)
            for sender, content in messages
        ]
        db.add_all(records)
        db.commit()
        for record in records:
            db.refresh(record)
        return records

    def rename_session(
        self, db: Session, session_id: int, user_id: int, title: str
    ) -> ChatSession | None:
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
