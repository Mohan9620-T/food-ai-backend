"""Authenticated image creation with the same private attachment/history storage as documents."""

import re
from typing import Literal, cast
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.redis_client import invalidate_cached_history
from app.models.chat import ChatDocumentAttachment
from app.rate_limit import limiter
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat import ChatDocumentAttachmentOut, ChatDocumentResponse
from app.services.image_generation_service import (
    IMAGE_MODEL,
    MAX_IMAGE_REQUEST_CHARS,
    ImageGenerationError,
    ImageGenerationService,
    image_request_help,
)
from app.services.image_subject_service import resolve_image_subject
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/chat/images", tags=["AI Chat Images"])
repository = ChatRepository()
service = ImageGenerationService()


class ImageGenerationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_IMAGE_REQUEST_CHARS, pattern=r"\S")
    session_id: int | None = Field(default=None, gt=0)
    aspect_ratio: Literal["auto", "1:1", "9:16", "16:9"] = "auto"


@router.post("", response_model=ChatDocumentResponse)
@limiter.limit("5/minute")
async def generate_image(
    request: Request,
    payload: ImageGenerationRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["sub"])
    session = (
        repository.get_session(db, payload.session_id, user_id) if payload.session_id else None
    )
    if payload.session_id and session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    prompt = payload.message.strip()
    help_message = image_request_help(prompt)
    if session is None:
        session = repository.create_session(db, user_id, prompt[:60])
    session_id = cast(int, session.id)
    repository.add_message(db, session_id, "user", prompt)
    invalidate_cached_history(user_id, session_id)
    subject = None
    try:
        if not help_message:
            subject = await resolve_image_subject(prompt)
        if subject:
            style_request = re.sub(
                r"\b(?:not|no|without)\s+(?:an?\s+)?(?:illustration|cartoon|caricature|painting|sketch)\b",
                "",
                prompt,
                flags=re.I,
            )
            if not re.search(
                r"\b(?:illustration|cartoon|caricature|painting|sketch|stylized|artistic)\b",
                style_request,
                re.I,
            ):
                source_url = quote(subject["subject_source_url"], safe="/:?&=%#@!$+,-._~")
                help_message = (
                    f"The current source identifies **{subject['subject']}** for your request: "
                    f"[view the source](<{source_url}>). "
                    "This image provider cannot reliably reproduce a real person's likeness or use "
                    "an uploaded reference portrait. I haven't generated a portrait because it could "
                    "show an inaccurate face. Use the source to check their actual appearance. "
                    "If you want artwork, ask for an artistic illustration; it will be labeled as "
                    "an AI interpretation with an unverified likeness."
                )
        image_prompt = (
            f"Subject: {subject['subject']}. Depict this named person. Original request: {prompt}"
            if subject
            else prompt
        )
        image = (
            None
            if help_message
            else await service.generate(
                image_prompt,
                payload.aspect_ratio,
                **({"subject_name": subject["subject"]} if subject else {}),
            )
        )
    except ImageGenerationError as error:
        repository.add_message(db, session_id, "bot", str(error))
        invalidate_cached_history(user_id, session_id)
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    answer = help_message or "Here is your generated image. You can view or download it below."
    if subject and image is not None:
        source_url = quote(subject["subject_source_url"], safe="/:?&=%#@!$+,-._~")
        answer = (
            f"AI-generated illustration intended to depict {subject['subject']}, "
            f"resolved from [this source](<{source_url}>). "
            "The image model cannot guarantee an accurate likeness. This is not an official "
            "photograph; use the source page to check the person's actual appearance."
        )
    try:
        bot = repository.add_message(db, session_id, "bot", answer, commit=False)
        attachment = None
        if image is not None:
            attachment = ChatDocumentAttachment(
                session_id=session_id,
                message_id=bot.id,
                filename=f"generated-image-{uuid4().hex[:12]}.png",
                content_type="image/png",
                file_size=len(image.data),
                file_data=image.data,
                kind="generated",
                generation_metadata={
                    "provider": "nvidia",
                    "model": IMAGE_MODEL,
                    "prompt": prompt,
                    "provider_prompt": image.provider_prompt,
                    "prompt_compacted": image.provider_prompt != prompt,
                    "aspect_ratio": image.aspect_ratio,
                    "width": image.width,
                    "height": image.height,
                    **(subject or {}),
                },
            )
            # Do not replace latest_document_id: an illustration is not the active spreadsheet.
            db.add(attachment)
        db.commit()
    except Exception:
        db.rollback()
        raise
    invalidate_cached_history(user_id, session_id)
    return ChatDocumentResponse(
        response=answer,
        session_id=session_id,
        attachment=ChatDocumentAttachmentOut.model_validate(attachment) if attachment else None,
    )
