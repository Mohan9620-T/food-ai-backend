"""One background extraction worker; durable states and optional Redis notification queue."""

import asyncio
import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import SessionLocal
from app.database.redis_client import get_redis_client, invalidate_cached_history
from app.models.chat import ChatDocumentAttachment, ChatSession
from app.repositories.chat_repository import ChatRepository
from app.services.chat_document_service import ChatDocumentService
from app.services.document.semantic_retrieval import index_document

logger = logging.getLogger(__name__)
_QUEUE = "food-ai:document-processing"


def enqueue(document_id: int) -> None:
    if settings.ENABLE_REDIS_CACHE:
        try:
            redis = get_redis_client()
            if redis is not None:
                redis.lpush(_QUEUE, str(document_id))
        except Exception:
            logger.warning("document.redis_queue_unavailable")
    # The queued database row is the durable fallback. No task is lost when Redis fails.


def process_one(db: Session, document_id: int) -> None:
    document = (
        db.query(ChatDocumentAttachment)
        .filter(ChatDocumentAttachment.id == document_id)
        .with_for_update()
        .first()
    )
    if document is None or document.processing_status != "queued":
        return
    setattr(document, "processing_status", "processing")
    setattr(document, "processing_stage", "extracting")
    db.commit()
    try:
        extracted = ChatDocumentService().extract_document(
            bytes(document.file_data), str(document.filename)
        )
        ChatRepository().save_document_extraction(document, extracted)
        setattr(document, "processing_stage", "indexing")
        db.commit()
        owner = db.get(ChatSession, int(document.session_id))
        if owner is not None:
            index_document(
                user_id=int(owner.user_id),
                session_id=int(document.session_id),
                document_id=int(document.id),
                filename=str(document.filename),
                extracted=extracted,
            )
        setattr(document, "processing_status", "ready")
        setattr(document, "processing_stage", "complete")
        setattr(document, "processing_error", None)
        db.commit()
    except Exception:
        db.rollback()
        document = db.get(ChatDocumentAttachment, document_id)
        if document is not None:
            setattr(document, "processing_status", "failed")
            setattr(document, "processing_stage", "failed")
            setattr(
                document,
                "processing_error",
                "Document processing failed. Please upload the file again.",
            )
            db.commit()
        logger.warning("document.background_processing_failed", extra={"document_id": document_id})
    finally:
        if document is not None:
            owner = db.get(ChatSession, int(document.session_id))
            if owner is not None:
                invalidate_cached_history(int(owner.user_id), int(document.session_id))


class DocumentWorker:
    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory
        self.task: asyncio.Task | None = None

    async def start(self):
        if not settings.ENABLE_BACKGROUND_DOCUMENT_PROCESSING:
            return
        # A previously interrupted worker cannot leave documents permanently processing.
        with self.session_factory() as db:
            db.query(ChatDocumentAttachment).filter(
                ChatDocumentAttachment.processing_status == "processing"
            ).update(
                {
                    "processing_status": "failed",
                    "processing_stage": "failed",
                    "processing_error": "Processing was interrupted. Please upload the file again.",
                }
            )
            db.commit()
        self.task = asyncio.create_task(self.drain())

    def tick(self):
        with self.session_factory() as db:
            document_id = None
            if settings.ENABLE_REDIS_CACHE:
                try:
                    redis = get_redis_client()
                    if redis is not None:
                        value = redis.rpop(_QUEUE)
                        document_id = int(value) if isinstance(value, (str, bytes)) else None
                except Exception:
                    logger.warning("document.redis_queue_unavailable")
            if document_id is None:
                row = (
                    db.query(ChatDocumentAttachment.id)
                    .filter(ChatDocumentAttachment.processing_status == "queued")
                    .order_by(ChatDocumentAttachment.id)
                    .first()
                )
                document_id = row[0] if row else None
            if document_id is not None:
                process_one(db, document_id)

    async def drain(self):
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logger.warning("document.worker_unavailable")
            await asyncio.sleep(1)

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
