import logging
from typing import cast

from sqlalchemy.orm import Session

from app.config import settings
from app.models.chat import ChatSession
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat import ChatHistoryMessage
from app.services.chat_service import ChatModelUnavailableError, ChatService

logger = logging.getLogger(__name__)

ROLLING_SUMMARY_MAX_TOKENS = 600
ROLLING_SUMMARY_WORD_LIMIT = 500


def _build_summary_prompt(
    existing_summary: str | None, expiring_messages: list[ChatHistoryMessage]
) -> str:
    turns = "\n".join(f"{item.role}: {item.content}" for item in expiring_messages)
    prior = existing_summary.strip() if existing_summary else "(none yet)"
    return (
        "You maintain a rolling summary of an ongoing chat so older turns can be "
        "dropped from the visible conversation without losing what they established.\n\n"
        f"CURRENT ROLLING SUMMARY:\n{prior}\n\n"
        "NEW TURNS TO FOLD IN (oldest first, about to leave the visible window):\n"
        f"{turns}\n\n"
        "Write one updated rolling summary that merges the current summary with the "
        "new turns above. Preserve concrete facts, user preferences, decisions, "
        "constraints, and open questions. Do not repeat the turns verbatim and do not "
        f"invent anything not present. Keep it under {ROLLING_SUMMARY_WORD_LIMIT} words. "
        "Return only the updated summary text - no headings, no preamble."
    )


async def summarize_expiring_turns(
    chat_service: ChatService,
    existing_summary: str | None,
    expiring_messages: list[ChatHistoryMessage],
) -> str:
    prompt = _build_summary_prompt(existing_summary, expiring_messages)
    summary = await chat_service.complete_chat(
        prompt,
        [],
        [],
        temperature=settings.DOCUMENT_AI_TEMPERATURE,
        max_tokens=ROLLING_SUMMARY_MAX_TOKENS,
    )
    return summary.strip()


async def refresh_rolling_summary(
    db: Session,
    chat_service: ChatService,
    repository: ChatRepository,
    session_id: int,
) -> None:
    """Best-effort: fold turns about to leave the visible window into memory.

    Never raises - a failed or unavailable summarization call simply means the
    watermark does not advance this turn; get_messages_since picks up the same
    (and any further) backlog on a later turn, so nothing is lost, only delayed.
    """
    try:
        session = db.get(ChatSession, session_id)
        if session is None:
            return
        visible_window = repository.get_message_history(
            db, session_id, limit=ChatService.HISTORY_MESSAGE_LIMIT
        )
        if len(visible_window) < ChatService.HISTORY_MESSAGE_LIMIT:
            return  # Nothing has fallen out of the visible window yet.
        oldest_visible_id = cast(int, visible_window[0].id)
        expiring = repository.get_messages_since(
            db,
            session_id,
            after_message_id=cast(int | None, session.summary_covers_through_message_id),
            before_message_id=oldest_visible_id,
        )
        if not expiring:
            return
        expiring_history = [
            ChatHistoryMessage(
                role="assistant" if item.sender == "bot" else "user",
                content=cast(str, item.content),
            )
            for item in expiring
        ]
        summary = await summarize_expiring_turns(
            chat_service,
            cast(str | None, session.rolling_summary),
            expiring_history,
        )
        repository.update_rolling_summary(
            db,
            session_id,
            summary=summary,
            through_message_id=cast(int, expiring[-1].id),
        )
    except ChatModelUnavailableError:
        logger.warning("chat.rolling_summary_failed", extra={"session_id": session_id})
    except Exception:
        logger.exception("chat.rolling_summary_failed", extra={"session_id": session_id})
