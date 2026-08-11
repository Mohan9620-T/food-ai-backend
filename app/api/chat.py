from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.chat_session import (
    ChatSessionOut,
    ChatSessionDetailOut,
    ChatSessionCreate,
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

    return ChatResponse(response=answer)
