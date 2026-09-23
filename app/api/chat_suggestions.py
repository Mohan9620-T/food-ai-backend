import hashlib
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.models.chat import ChatMessageRecord, ChatSession
from app.rate_limit import limiter
from app.schemas.chat_suggestions import SuggestionRequest, SuggestionResponse
from app.services.chat_suggestion_service import ChatSuggestionService
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/chat", tags=["AI Chat"])
service = ChatSuggestionService()


@router.post("/sessions/{session_id}/suggestions", response_model=SuggestionResponse)
@limiter.limit("20/minute")
async def suggestions(
    session_id: int,
    payload: SuggestionRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["sub"])
    session = (
        db.query(ChatSession.id)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    recent = (
        db.query(ChatMessageRecord)
        .filter(
            ChatMessageRecord.session_id == session_id,
            ChatMessageRecord.is_internal.is_(False),
        )
        .order_by(ChatMessageRecord.id.desc())
        .limit(2)
        .all()
    )
    if not recent or recent[0].sender != "bot":
        raise HTTPException(status_code=409, detail="Wait for the current answer to finish")
    latest = recent[0]
    answer, message_id = str(latest.content).strip(), int(latest.id)
    if hashlib.sha256(answer.encode()).hexdigest() != payload.answer_hash:
        raise HTTPException(status_code=409, detail="This answer is no longer current")
    automation = cast(dict, latest.automation or {})
    status = automation.get("response", {}).get("status")
    if (
        status in {"clarification_required", "ready_for_review", "failed", "partial"}
        or not answer
        or "Response interrupted:" in answer
    ):
        return SuggestionResponse(message_id=message_id)
    question = str(recent[1].content) if len(recent) > 1 and recent[1].sender == "user" else ""
    # Do not hold a database connection while waiting for optional model output.
    db.rollback()
    result = await service.suggest(
        user_id=user_id,
        message_id=message_id,
        answer_hash=payload.answer_hash,
        question=question,
        answer=answer,
    )
    return SuggestionResponse(message_id=message_id, suggestions=result)
