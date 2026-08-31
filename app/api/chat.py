import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.chat_session import (
    ChatSessionOut,
    ChatSessionDetailOut,
    ChatSessionCreate,
    ChatSessionImport,
    ChatSessionRename,
)
from app.services.chat_service import ChatService
from app.repositories.chat_repository import ChatRepository
from app.utils.auth_dependency import get_current_user

router = APIRouter(
    prefix="/chat",
    tags=["AI Chat"]
)

service = ChatService()
repository = ChatRepository()
logger = logging.getLogger(__name__)


def _get_user_id(current_user: dict) -> int:
    return int(current_user["sub"])


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

    session = None
    if session_id:
        session = repository.get_session(db, session_id, user_id)
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")
    else:
        title = request.message[:60] if len(request.message) <= 60 else f"{request.message[:57]}..."
        session = repository.create_session(db, user_id, title)

    repository.add_message(db, session.id, "user", request.message)

    answer = service.chat(request.message, request.history, request.reference_history)

    repository.add_message(db, session.id, "bot", answer)

    logger.info("chat.completed", extra={"user_id": user_id, "session_id": session.id})

    return ChatResponse(response=answer, session_id=session.id)


@router.post("/stream")
async def stream_chat(
    payload: ChatRequest,
    request: Request,
    session_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _get_user_id(current_user)
    if session_id:
        session = repository.get_session(db, session_id, user_id)
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")
    else:
        title = payload.message[:60] if len(payload.message) <= 60 else f"{payload.message[:57]}..."
        session = repository.create_session(db, user_id, title)

    repository.add_message(db, session.id, "user", payload.message)
    logger.info("chat.stream_started", extra={"user_id": user_id, "session_id": session.id})

    async def generate():
        chunks: list[str] = []
        iterator = service.stream_chat(
            payload.message,
            payload.history,
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
                chunks.append(chunk)
                yield json.dumps({"type": "token", "content": chunk}) + "\n"

            answer = "".join(chunks)
            if answer:
                repository.add_message(db, session.id, "bot", answer)
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
