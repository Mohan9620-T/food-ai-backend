from datetime import datetime, timezone
from typing import cast

from fastapi.encoders import jsonable_encoder
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.chat import ChatDocumentAttachment, ChatMessageRecord, ChatSession
from app.schemas.chat import (
    ChatDocumentAutomationRequest,
    ChatDocumentAutomationResponse,
    ChatDocumentAutomationState,
)
from app.services.document.extraction_models import ExtractedDocument


class ChatRepository:
    @staticmethod
    def save_document_extraction(
        attachment: ChatDocumentAttachment, extracted: ExtractedDocument
    ) -> None:
        """Keep full extraction, tables, locations and warnings in the same transaction."""
        setattr(attachment, "raw_text", extracted.text)
        setattr(attachment, "extracted_data", jsonable_encoder(extracted))

    def save_automation_state(
        self,
        db: Session,
        record: ChatMessageRecord,
        request: ChatDocumentAutomationRequest,
        response: ChatDocumentAutomationResponse,
    ) -> None:
        db.flush()
        previous = (
            db.query(ChatMessageRecord)
            .filter(
                ChatMessageRecord.session_id == request.session_id,
                ChatMessageRecord.sender == "bot",
                ChatMessageRecord.id < record.id,
                ChatMessageRecord.is_internal.is_(False),
            )
            .order_by(ChatMessageRecord.id.desc())
            .first()
        )
        prior = cast(dict | None, previous.automation) if previous is not None else None
        if prior and prior.get("response", {}).get("status") not in {
            "clarification_required",
            "ready_for_review",
        }:
            prior = None
        choices = list(prior.get("choices", [])) if prior else []
        if (
            prior
            and not request.confirm
            and prior["response"]["status"] == "clarification_required"
        ):
            choices.append(
                {"question": prior["response"]["response"], "answer": request.instruction}
            )
        state = ChatDocumentAutomationState(
            instruction=request.instruction,
            request_instruction=prior["request_instruction"] if prior else request.instruction,
            source_document_id=request.source_document_id,
            confirmed=request.confirm,
            choices=choices,
            response=response,
        )
        setattr(record, "automation", state.model_dump(mode="json"))

    @staticmethod
    def _touch_session(db: Session, session_id: int) -> None:
        session = db.get(ChatSession, session_id)
        if session is not None:
            setattr(session, "updated_at", datetime.now(timezone.utc))

    def ensure_image_document_sources(self, db: Session, session_id: int) -> None:
        """Make existing image turns selectable by an owned, locked document pipeline.

        The caller checks session ownership and commits/rolls back the whole request.
        Retain the original image message and reuse its attachment on later builds.
        """
        records = (
            db.query(ChatMessageRecord)
            .filter(
                ChatMessageRecord.session_id == session_id, ChatMessageRecord.image_data.isnot(None)
            )
            .order_by(ChatMessageRecord.created_at.asc(), ChatMessageRecord.id.asc())
            .all()
        )
        extensions = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
        }
        for record in records:
            if record.document_attachment is not None:
                continue
            extension = extensions.get(str(record.image_content_type))
            if extension is None:
                continue
            db.add(
                ChatDocumentAttachment(
                    session_id=session_id,
                    message_id=record.id,
                    filename=f"image-{record.id}.{extension}",
                    content_type=record.image_content_type,
                    file_size=len(record.image_data),
                    file_data=record.image_data,
                    kind="uploaded",
                    created_at=record.created_at,
                )
            )
        db.flush()

        if records:
            latest = (
                db.query(ChatDocumentAttachment)
                .filter(ChatDocumentAttachment.session_id == session_id)
                .order_by(
                    ChatDocumentAttachment.created_at.desc(), ChatDocumentAttachment.id.desc()
                )
                .first()
            )
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if latest is not None and session is not None:
                session.latest_document_id = latest.id

    def get_message_history(
        self,
        db: Session,
        session_id: int,
        limit: int = 12,
    ) -> list[ChatMessageRecord]:
        messages = (
            db.query(ChatMessageRecord)
            .filter(
                ChatMessageRecord.session_id == session_id,
                ChatMessageRecord.is_internal.is_(False),
            )
            .order_by(ChatMessageRecord.created_at.desc(), ChatMessageRecord.id.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(messages))

    def get_image_turns(
        self,
        db: Session,
        session_id: int,
    ) -> list[tuple[ChatMessageRecord, str]]:
        """Return every persisted image and its immediate assistant response."""
        messages = (
            db.query(ChatMessageRecord)
            .filter(ChatMessageRecord.session_id == session_id)
            .order_by(ChatMessageRecord.created_at.asc(), ChatMessageRecord.id.asc())
            .all()
        )
        turns: list[tuple[ChatMessageRecord, str]] = []
        for index, message in enumerate(messages):
            if message.image_data is None or message.image_content_type is None:
                continue
            response = ""
            if index + 1 < len(messages) and messages[index + 1].sender == "bot":
                response = str(messages[index + 1].content)
            turns.append((message, response))
        return turns

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
            # Attachments have their own session FK with ON DELETE CASCADE.
            # Move them before removing sessions or their bytes are lost.
            db.query(ChatDocumentAttachment).filter(
                ChatDocumentAttachment.session_id.in_(redundant_ids)
            ).update({ChatDocumentAttachment.session_id: primary.id}, synchronize_session=False)
            latest_document = (
                db.query(ChatDocumentAttachment)
                .filter(ChatDocumentAttachment.session_id == primary.id)
                .order_by(
                    ChatDocumentAttachment.created_at.desc(), ChatDocumentAttachment.id.desc()
                )
                .first()
            )
            setattr(primary, "latest_document_id", latest_document.id if latest_document else None)
            db.flush()
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

    def get_session_for_update(
        self, db: Session, session_id: int, user_id: int
    ) -> ChatSession | None:
        """Lock the session while a pipeline advances its latest-document pointer."""
        return (
            db.query(ChatSession)
            .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .with_for_update()
            .first()
        )

    def add_message(
        self,
        db: Session,
        session_id: int,
        sender: str,
        content: str,
        *,
        commit: bool = True,
    ) -> ChatMessageRecord:
        message = ChatMessageRecord(session_id=session_id, sender=sender, content=content)
        db.add(message)
        self._touch_session(db, session_id)
        if commit:
            db.commit()
            db.refresh(message)
        else:
            db.flush()

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
        commit: bool = True,
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
        self._touch_session(db, session_id)
        if commit:
            db.commit()
            db.refresh(user_message)
            db.refresh(bot_message)
        else:
            db.flush()
        return user_message, bot_message

    def add_document_attachment(
        self,
        db: Session,
        *,
        session_id: int,
        message_id: int,
        filename: str,
        content_type: str,
        file_data: bytes,
        kind: str,
        raw_text: str | None = None,
        structured_summary: str | None = None,
        extracted: ExtractedDocument | None = None,
        generation_metadata: dict | None = None,
    ) -> ChatDocumentAttachment:
        attachment = ChatDocumentAttachment(
            session_id=session_id,
            message_id=message_id,
            filename=filename,
            content_type=content_type,
            file_size=len(file_data),
            file_data=file_data,
            kind=kind,
            raw_text=raw_text,
            structured_summary=structured_summary,
            generation_metadata=generation_metadata,
        )
        if extracted is not None:
            self.save_document_extraction(attachment, extracted)
            # The generation endpoint also keeps the original Markdown response.
            # Parsed file text remains available separately in extracted_data.
            if raw_text is not None:
                setattr(attachment, "raw_text", raw_text)
        db.add(attachment)
        db.flush()
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session is not None:
            session.latest_document_id = attachment.id
        db.commit()
        db.refresh(attachment)
        return attachment

    def save_generated_document(
        self,
        db: Session,
        *,
        session_id: int,
        bot_content: str,
        filename: str,
        content_type: str,
        file_data: bytes,
        raw_text: str | None = None,
        structured_summary: str | None = None,
        is_internal: bool = False,
        commit: bool = True,
        locked_session: ChatSession | None = None,
        extracted: ExtractedDocument | None = None,
    ) -> tuple[ChatMessageRecord, ChatDocumentAttachment]:
        """Commit a completion message, generated file, and latest pointer atomically."""
        bot_record = ChatMessageRecord(
            session_id=session_id,
            sender="bot",
            content=bot_content,
            is_internal=is_internal,
        )
        try:
            db.add(bot_record)
            db.flush()
            attachment = ChatDocumentAttachment(
                session_id=session_id,
                message_id=bot_record.id,
                filename=filename,
                content_type=content_type,
                file_size=len(file_data),
                file_data=file_data,
                kind="generated",
                raw_text=raw_text,
                structured_summary=structured_summary,
            )
            if extracted is not None:
                self.save_document_extraction(attachment, extracted)
            db.add(attachment)
            db.flush()
            session = locked_session or (
                db.query(ChatSession).filter(ChatSession.id == session_id).with_for_update().first()
            )
            if session is not None:
                session.latest_document_id = attachment.id
            if commit:
                db.commit()
        except Exception:
            db.rollback()
            raise
        if commit:
            db.refresh(bot_record)
            db.refresh(attachment)
        return bot_record, attachment

    def save_document_upload(
        self,
        db: Session,
        *,
        session_id: int,
        user_content: str,
        bot_content: str,
        filename: str,
        content_type: str,
        file_data: bytes,
        raw_text: str,
        extracted: ExtractedDocument | None = None,
    ) -> tuple[ChatMessageRecord, ChatDocumentAttachment]:
        """Commit the turn and original file together, before any model request."""
        user_record = ChatMessageRecord(
            session_id=session_id,
            sender="user",
            content=user_content,
            image_data=file_data if content_type.startswith("image/") else None,
            image_content_type=content_type if content_type.startswith("image/") else None,
        )
        bot_record = ChatMessageRecord(session_id=session_id, sender="bot", content=bot_content)
        try:
            db.add_all([user_record, bot_record])
            db.flush()
            attachment = ChatDocumentAttachment(
                session_id=session_id,
                message_id=user_record.id,
                filename=filename,
                content_type=content_type,
                file_size=len(file_data),
                file_data=file_data,
                kind="uploaded",
                raw_text=raw_text,
            )
            if extracted is not None:
                self.save_document_extraction(attachment, extracted)
            db.add(attachment)
            db.flush()
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session is not None:
                session.latest_document_id = attachment.id
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(bot_record)
        db.refresh(attachment)
        return bot_record, attachment

    def get_document_for_user(
        self, db: Session, document_id: int, user_id: int
    ) -> ChatDocumentAttachment | None:
        return (
            db.query(ChatDocumentAttachment)
            .join(ChatSession, ChatSession.id == ChatDocumentAttachment.session_id)
            .filter(ChatDocumentAttachment.id == document_id, ChatSession.user_id == user_id)
            .first()
        )

    def get_documents_for_user(
        self, db: Session, session_id: int, user_id: int
    ) -> list[ChatDocumentAttachment]:
        return (
            db.query(ChatDocumentAttachment)
            .join(ChatSession, ChatSession.id == ChatDocumentAttachment.session_id)
            .filter(
                ChatDocumentAttachment.session_id == session_id,
                ChatSession.user_id == user_id,
            )
            .order_by(
                ChatDocumentAttachment.created_at.asc(),
                ChatDocumentAttachment.id.asc(),
            )
            .all()
        )

    def get_latest_document_for_user(
        self, db: Session, session_id: int, user_id: int
    ) -> ChatDocumentAttachment | None:
        session = self.get_session(db, session_id, user_id)
        if session is None or session.latest_document_id is None:
            return None
        return self.get_document_for_user(db, int(session.latest_document_id), user_id)

    def get_document_by_filename_for_user(
        self,
        db: Session,
        *,
        session_id: int,
        filename: str,
        user_id: int,
    ) -> ChatDocumentAttachment | None:
        return (
            db.query(ChatDocumentAttachment)
            .join(ChatSession, ChatSession.id == ChatDocumentAttachment.session_id)
            .filter(
                ChatDocumentAttachment.session_id == session_id,
                ChatSession.user_id == user_id,
                ChatDocumentAttachment.filename.ilike(filename),
            )
            .order_by(
                ChatDocumentAttachment.created_at.desc(),
                ChatDocumentAttachment.id.desc(),
            )
            .first()
        )

    def get_document_summaries(self, db: Session, session_id: int) -> list[str]:
        rows = (
            db.query(ChatDocumentAttachment)
            .filter(
                ChatDocumentAttachment.session_id == session_id,
            )
            .order_by(ChatDocumentAttachment.created_at.asc(), ChatDocumentAttachment.id.asc())
            .all()
        )
        return [
            str(row.structured_summary)
            if row.structured_summary
            else f"Extracted document text (not AI analysis):\n{row.raw_text}"
            for row in rows
            if row.structured_summary or row.raw_text
        ]

    def get_document_contexts(
        self, db: Session, session_id: int, limit: int = 4
    ) -> list[tuple[str, str]]:
        """Return recent original uploads as deterministic evidence for chat Q&A."""
        rows = (
            db.query(ChatDocumentAttachment)
            .filter(
                ChatDocumentAttachment.session_id == session_id,
                ChatDocumentAttachment.kind == "uploaded",
                ChatDocumentAttachment.raw_text.isnot(None),
            )
            .order_by(
                ChatDocumentAttachment.created_at.desc(),
                ChatDocumentAttachment.id.desc(),
            )
            .limit(limit)
            .all()
        )
        return [
            (str(row.filename), str(row.raw_text))
            for row in reversed(rows)
            if str(row.raw_text or "").strip()
        ]

    def get_spreadsheet_operation_source(
        self, db: Session, session_id: int
    ) -> ChatDocumentAttachment | None:
        """Choose the latest generated XLSX, otherwise the latest uploaded table."""
        generated = (
            db.query(ChatDocumentAttachment)
            .filter(
                ChatDocumentAttachment.session_id == session_id,
                ChatDocumentAttachment.kind == "generated",
                ChatDocumentAttachment.filename.ilike("%.xlsx"),
            )
            .order_by(
                ChatDocumentAttachment.created_at.desc(),
                ChatDocumentAttachment.id.desc(),
            )
            .first()
        )
        if generated is not None:
            return generated
        return (
            db.query(ChatDocumentAttachment)
            .filter(
                ChatDocumentAttachment.session_id == session_id,
                ChatDocumentAttachment.kind == "uploaded",
                or_(
                    ChatDocumentAttachment.filename.ilike("%.xlsx"),
                    ChatDocumentAttachment.filename.ilike("%.csv"),
                ),
            )
            .order_by(
                ChatDocumentAttachment.created_at.desc(),
                ChatDocumentAttachment.id.desc(),
            )
            .first()
        )

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
        setattr(session, "title", title)
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

    def delete_user_turn(self, db: Session, session_id: int, message_id: int, user_id: int) -> bool:
        session = self.get_session(db, session_id, user_id)
        if not session:
            return False
        messages = list(session.messages)
        message_index = next(
            (index for index, message in enumerate(messages) if message.id == message_id), None
        )
        if message_index is None or messages[message_index].sender != "user":
            return False
        db.delete(messages[message_index])
        for following in messages[message_index + 1 :]:
            if following.sender == "user":
                break
            db.delete(following)
        db.commit()
        return True
