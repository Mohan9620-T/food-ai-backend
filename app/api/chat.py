import asyncio
import json
import logging
from typing import cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.rate_limit import limiter
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat import ChatHistoryMessage, ChatRequest, ChatResponse
from app.schemas.chat_session import (
    ChatSessionCreate,
    ChatSessionDetailOut,
    ChatSessionImport,
    ChatSessionOut,
    ChatSessionRename,
)
from app.services.chat_service import ChatModelUnavailableError, ChatService
from app.services.chat_vision_service import ChatVisionService
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.image_validation import InvalidImageError, validate_image_content
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/chat", tags=["AI Chat"])

service = ChatService()
vision_service = ChatVisionService()
repository = ChatRepository()
logger = logging.getLogger(__name__)
stream_tasks: set[asyncio.Task] = set()
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
            content=cast(str, message.content),
        )
        for message in repository.get_message_history(db, session_id)
    ]


@router.get(
    "/sessions",
    response_model=list[ChatSessionOut],
    summary="List chat sessions",
    description="Return all chat sessions belonging to the authenticated user.",
    responses={401: {"description": "Missing, invalid, or expired access token."}},
)
def list_sessions(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return repository.get_sessions_for_user(db, _get_user_id(current_user))


@router.post(
    "/sessions",
    response_model=ChatSessionOut,
    summary="Create a chat session",
    description="Create an empty chat session with the supplied title for the current user.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        422: {"description": "The session payload failed validation."},
    },
)
def create_session(
    payload: ChatSessionCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return repository.create_session(db, _get_user_id(current_user), payload.title)


@router.post(
    "/sessions/consolidate",
    response_model=list[ChatSessionOut],
    summary="Consolidate chat sessions",
    description="Merge compatible chat-session data for the authenticated user and return the result.",
    responses={401: {"description": "Missing, invalid, or expired access token."}},
)
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


@router.get(
    "/sessions/{session_id}",
    response_model=ChatSessionDetailOut,
    summary="Get a chat session",
    description="Return a chat session and its persisted messages when it belongs to the current user.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {"description": "The chat session does not exist or belongs to another user."},
        422: {"description": "The session ID failed validation."},
    },
)
def get_session(
    session_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    session = repository.get_session(db, session_id, _get_user_id(current_user))
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


@router.post(
    "/sessions/{session_id}/import",
    response_model=ChatSessionDetailOut,
    summary="Import chat messages",
    description="Append supplied messages to a chat session owned by the authenticated user.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {"description": "The chat session does not exist or belongs to another user."},
        422: {"description": "The session ID or import payload failed validation."},
    },
)
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
        cast(int, session.id),
        [(message.sender, message.content) for message in payload.messages],
    )
    return repository.get_session(db, cast(int, session.id), user_id)


@router.put(
    "/sessions/{session_id}",
    response_model=ChatSessionOut,
    summary="Rename a chat session",
    description="Change the title of a chat session owned by the authenticated user.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {"description": "The chat session does not exist or belongs to another user."},
        422: {"description": "The session ID or rename payload failed validation."},
    },
)
def rename_session(
    session_id: int,
    payload: ChatSessionRename,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    session = repository.rename_session(db, session_id, _get_user_id(current_user), payload.title)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


@router.delete(
    "/sessions/{session_id}",
    summary="Delete a chat session",
    description="Delete a chat session and its messages when it belongs to the current user.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {"description": "The chat session does not exist or belongs to another user."},
        422: {"description": "The session ID failed validation."},
    },
)
def delete_session(
    session_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    deleted = repository.delete_session(db, session_id, _get_user_id(current_user))
    if not deleted:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"detail": "Chat session deleted"}


@router.delete(
    "/sessions/{session_id}/messages/{message_id}",
    summary="Delete a user chat turn",
    description="Delete an owned user message and its immediately following assistant response.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {"description": "The session or user message was not found."},
        422: {"description": "A path parameter failed validation."},
    },
)
def delete_user_turn(
    session_id: int,
    message_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    deleted = repository.delete_user_turn(
        db, session_id, message_id, _get_user_id(current_user)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Chat message not found")
    return {"detail": "Chat turn deleted"}


@router.post(
    "/",
    response_model=ChatResponse,
    summary="Send a chat message",
    description="Generate a text response and persist the completed turn in a new or existing chat session.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {
            "description": "The requested chat session does not exist or belongs to another user."
        },
        422: {"description": "The message or session ID failed validation."},
        503: {"description": "The configured Ollama text model is unavailable."},
    },
)
def chat(
    request: ChatRequest,
    session_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _get_user_id(current_user)

    session = _get_or_create_chat_session(db, user_id, session_id, request.message)

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


@router.post(
    "/vision",
    response_model=ChatResponse,
    summary="Send an image chat message",
    description="Analyze an uploaded image and persist the image, prompt, and response in a chat session.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {
            "description": "The requested chat session does not exist or belongs to another user."
        },
        413: {"description": "The uploaded image exceeds 8 MB."},
        415: {"description": "The uploaded file is not a supported image type."},
        422: {"description": "The image or form data is invalid or empty."},
        429: {"description": "The image-chat rate limit was exceeded."},
        503: {"description": "The configured Ollama vision model is unavailable."},
    },
)
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


@router.post(
    "/stream",
    summary="Stream a chat response",
    description=(
        "Stream newline-delimited JSON events for a text chat turn. Events contain the session ID, "
        "generated tokens, completion, or a model-unavailable error; completed turns are persisted."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {
            "description": "The requested chat session does not exist or belongs to another user."
        },
        422: {"description": "The message or session ID failed validation."},
    },
)
async def stream_chat(
    payload: ChatRequest,
    request: Request,
    session_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _get_user_id(current_user)
    session = _get_or_create_chat_session(db, user_id, session_id, payload.message)

    history = _get_persisted_history(db, session.id)
    repository.add_message(db, session.id, "user", payload.message)
    logger.info("chat.stream_started", extra={"user_id": user_id, "session_id": session.id})

    events: asyncio.Queue[dict] = asyncio.Queue()
    database_bind = db.get_bind()

    async def produce_response():
        chunks: list[str] = []
        iterator = service.stream_chat(
            payload.message,
            history,
            payload.reference_history,
        )
        try:
            async for chunk in iterator:
                chunks.append(chunk)
                await events.put({"type": "token", "content": chunk})
        except ChatModelUnavailableError as error:
            logger.warning(
                "chat.stream_model_unavailable",
                extra={"user_id": user_id, "session_id": session.id},
            )
            await events.put({"type": "error", "message": str(error)})
            return
        except Exception:
            logger.exception(
                "chat.stream_generation_failed",
                extra={"user_id": user_id, "session_id": session.id},
            )
            await events.put(
                {
                    "type": "error",
                    "message": "The response could not be generated. Please try again.",
                }
            )
            return
        finally:
            close = getattr(iterator, "aclose", None)
            if close:
                await close()

        answer = "".join(chunks)
        if answer:
            worker_db = Session(bind=database_bind)
            try:
                repository.add_message(worker_db, session.id, "bot", answer)
            finally:
                worker_db.close()
        logger.info("chat.stream_completed", extra={"user_id": user_id, "session_id": session.id})
        await events.put({"type": "done"})

    producer = asyncio.create_task(produce_response())
    stream_tasks.add(producer)
    producer.add_done_callback(stream_tasks.discard)

    async def generate():
        yield json.dumps({"type": "session", "session_id": session.id}) + "\n"
        while True:
            event = await events.get()
            yield json.dumps(event) + "\n"
            if event["type"] in {"done", "error"}:
                return

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
