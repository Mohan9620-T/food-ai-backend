import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.rate_limit import limiter
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.chat import ChatHistoryMessage
from app.schemas.chat_session import (
    ChatSessionOut,
    ChatSessionDetailOut,
    ChatSessionCreate,
    ChatSessionImport,
    ChatSessionRename,
)
from app.services.chat_service import ChatModelUnavailableError, ChatService
from app.services.chat_vision_service import ChatVisionService
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.image_validation import InvalidImageError, validate_image_content
from app.repositories.chat_repository import ChatRepository
from app.utils.auth_dependency import get_current_user

router = APIRouter(
    prefix="/chat",
    tags=["AI Chat"]
)

service = ChatService()
vision_service = ChatVisionService()
repository = ChatRepository()
logger = logging.getLogger(__name__)
MAX_CHAT_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_CHAT_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _get_user_id(current_user: dict) -> int:
    return int(current_user["sub"])


def _get_or_create_chat_session(
    db: Session,
    user_id: int,
    session_id: int | None,
    title_source: str,
):
    if session_id:
        session = repository.get_session(db, session_id, user_id)
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        return session
    title = title_source if len(title_source) <= 60 else f"{title_source[:57]}..."
    return repository.create_session(db, user_id, title)


def _get_persisted_history(db: Session, session_id: int) -> list[ChatHistoryMessage]:
    return [
        ChatHistoryMessage(
            role="assistant" if message.sender == "bot" else "user",
            content=message.content,
        )
        for message in repository.get_message_history(db, session_id)
    ]


@router.get("/sessions", response_model=list[ChatSessionOut])
def list_sessions(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return repository.get_sessions_for_user(db, _get_user_id(current_user))


@router.post("/sessions", response_model=ChatSessionOut)
def create_session(
    payload: ChatSessionCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    return repository.create_session(db, _get_user_id(current_user), payload.title)


@router.post("/sessions/consolidate", response_model=list[ChatSessionOut])
def consolidate_sessions(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _get_user_id(current_user)
    session = repository.consolidate_sessions(db, user_id)
    logger.info(
        "chat.sessions_consolidated",
        extra={"user_id": user_id, "session_count": len(session)},
    )
    return session


@router.get("/sessions/{session_id}", response_model=ChatSessionDetailOut)
def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    session = repository.get_session(db, session_id, _get_user_id(current_user))
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


@router.post("/sessions/{session_id}/import", response_model=ChatSessionDetailOut)
def import_session_messages(
    session_id: int,
    payload: ChatSessionImport,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _get_user_id(current_user)
    logger.info("chat.requested", extra={"user_id": user_id, "session_id": session_id})
    session = repository.get_session(db, session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    repository.import_messages(
        db,
        session.id,
        [(message.sender, message.content) for message in payload.messages],
    )
    return repository.get_session(db, session.id, user_id)


@router.put("/sessions/{session_id}", response_model=ChatSessionOut)
def rename_session(
    session_id: int,
    payload: ChatSessionRename,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    session = repository.rename_session(db, session_id, _get_user_id(current_user), payload.title)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    deleted = repository.delete_session(db, session_id, _get_user_id(current_user))
    if not deleted:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"detail": "Chat session deleted"}


@router.post("/", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    session_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    user_id = _get_user_id(current_user)

    session = _get_or_create_chat_session(
        db, user_id, session_id, request.message
    )

    history = _get_persisted_history(db, session.id)

    try:
        answer = service.chat(request.message, history, request.reference_history)
    except ChatModelUnavailableError as error:
        logger.warning(
            "chat.text_model_unavailable",
            extra={"user_id": user_id, "session_id": session.id},
        )
        raise HTTPException(status_code=503, detail=str(error))

    repository.add_turn(db, session.id, request.message, answer)

    logger.info("chat.completed", extra={"user_id": user_id, "session_id": session.id})

    return ChatResponse(response=answer, session_id=session.id)


@router.post("/vision", response_model=ChatResponse)
@limiter.limit(settings.CHAT_VISION_RATE_LIMIT)
async def chat_vision(
    request: Request,
    image: UploadFile = File(...),
    message: str | None = Form(default=None),
    session_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        content_type = (image.content_type or "").lower().split(";", 1)[0]
        if content_type not in ALLOWED_CHAT_IMAGE_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Unsupported file type. Upload a JPEG, PNG, WebP, or GIF image.",
            )
        image_bytes = await image.read(MAX_CHAT_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_CHAT_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Image is too large. Maximum size is 8 MB.",
            )
        if not image_bytes:
            raise HTTPException(status_code=422, detail="The uploaded image is empty.")
        try:
            validate_image_content(image_bytes, content_type)
        except InvalidImageError as error:
            raise HTTPException(status_code=422, detail=str(error))

        logger.info(
            "chat.vision_upload_validated",
            extra={
                "user_id": _get_user_id(current_user),
                "session_id": session_id,
                "has_message": bool(message and message.strip()),
                "content_type": content_type,
                "image_size_bytes": len(image_bytes),
            },
        )
        user_id = _get_user_id(current_user)
        message_text = (message or "").strip()
        persisted_user_message = message_text or "[Image]"
        session = _get_or_create_chat_session(
            db,
            user_id,
            session_id,
            persisted_user_message,
        )
        try:
            answer = vision_service.describe(image_bytes, message_text or None)
        except VisionModelUnavailableError as error:
            logger.warning(
                "chat.vision_unavailable",
                extra={"user_id": user_id, "session_id": session.id},
            )
            raise HTTPException(status_code=503, detail=str(error))
        if await request.is_disconnected():
            logger.info(
                "chat.vision_disconnected",
                extra={"user_id": user_id, "session_id": session.id},
            )
            return ChatResponse(response=answer, session_id=session.id)
        repository.add_turn(
            db,
            session.id,
            persisted_user_message,
            answer,
            image_data=image_bytes,
            image_content_type=content_type,
        )
        logger.info(
            "chat.vision_completed",
            extra={"user_id": user_id, "session_id": session.id},
        )
        return ChatResponse(response=answer, session_id=session.id)
    finally:
        await image.close()


@router.post("/stream")
async def stream_chat(
    payload: ChatRequest,
    request: Request,
    session_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _get_user_id(current_user)
    session = _get_or_create_chat_session(
        db, user_id, session_id, payload.message
    )

    history = _get_persisted_history(db, session.id)
    logger.info("chat.stream_started", extra={"user_id": user_id, "session_id": session.id})

    async def generate():
        chunks: list[str] = []
        iterator = service.stream_chat(
            payload.message,
            history,
            payload.reference_history,
        )
        try:
            yield json.dumps({"type": "session", "session_id": session.id}) + "\n"
            while True:
                if await request.is_disconnected():
                    logger.info(
                        "chat.stream_disconnected",
                        extra={"user_id": user_id, "session_id": session.id},
                    )
                    return
                try:
                    chunk = await anext(iterator)
                except StopAsyncIteration:
                    break
                except ChatModelUnavailableError as error:
                    logger.warning(
                        "chat.stream_model_unavailable",
                        extra={"user_id": user_id, "session_id": session.id},
                    )
                    yield json.dumps({"type": "error", "message": str(error)}) + "\n"
                    return
                chunks.append(chunk)
                yield json.dumps({"type": "token", "content": chunk}) + "\n"

            answer = "".join(chunks)
            if answer:
                repository.add_turn(db, session.id, payload.message, answer)
            logger.info("chat.stream_completed", extra={"user_id": user_id, "session_id": session.id})
            yield json.dumps({"type": "done"}) + "\n"
        finally:
            close = getattr(iterator, "aclose", None)
            if close:
                await close()

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
