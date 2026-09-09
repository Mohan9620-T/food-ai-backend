import asyncio
import logging
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat import (
    ChatDocumentGenerateRequest,
    ChatDocumentResponse,
    ChatHistoryMessage,
)
from app.services.chat_document_service import (
    ChatDocumentService,
    DocumentProcessingUnavailableError,
    InvalidDocumentError,
)
from app.services.chat_service import ChatModelUnavailableError
from app.services.profile_service import ProfileService
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/chat/documents", tags=["AI Chat Documents"])
repository = ChatRepository()
service = ChatDocumentService()
profile_service = ProfileService()
logger = logging.getLogger(__name__)
MAX_CHAT_DOCUMENT_BYTES = 15 * 1024 * 1024
DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.post(
    "", response_model=ChatDocumentResponse, summary="Upload a chat document",
    description="Read a PDF, DOCX, TXT, CSV, or XLSX file and save it with its summary. "
    "Scanned PDF pages require Tesseract on the backend. Maximum upload size is 15 MB.",
    responses={
        404: {"description": "Chat session not found or not owned by this user."},
        413: {"description": "File exceeds 15 MB."},
        415: {"description": "Unsupported file type."},
        422: {"description": "Empty, unreadable, or password-protected document."},
        503: {"description": "OCR or the chat model is unavailable."},
    },
)
async def upload_document(
    file: UploadFile = File(...),
    message: str | None = Form(default=None),
    session_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    filename = Path(file.filename or "document").name
    extension = Path(filename).suffix.lower()
    expected_type = DOCUMENT_TYPES.get(extension)
    content_type = (file.content_type or "").lower().split(";", 1)[0]
    accepted_types = {expected_type, "", "application/octet-stream"}
    if extension == ".csv":
        # Windows/browser CSV associations also commonly use these MIME types.
        accepted_types.update({"application/vnd.ms-excel", "text/plain"})
    if expected_type is None or content_type not in accepted_types:
        raise HTTPException(status_code=415, detail="Upload a PDF, DOCX, TXT, CSV, or XLSX document.")
    try:
        file_data = await file.read(MAX_CHAT_DOCUMENT_BYTES + 1)
    finally:
        await file.close()
    if len(file_data) > MAX_CHAT_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document is too large. Maximum size is 15 MB.")
    if not file_data:
        raise HTTPException(status_code=422, detail="The uploaded document is empty.")
    user_id = int(current_user["sub"])
    resolved_session = None
    if session_id is not None:
        resolved_session = repository.get_session(db, session_id, user_id)
        if resolved_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
    try:
        raw_text = await asyncio.to_thread(service.extract, file_data, filename)
        summary = await asyncio.to_thread(service.summarize, raw_text, message)
    except InvalidDocumentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (ChatModelUnavailableError, DocumentProcessingUnavailableError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    user_text = (message or "").strip() or f"Uploaded {filename}"
    if resolved_session is None:
        resolved_session = repository.create_session(db, user_id, user_text[:60])
    resolved_session_id = cast(int, resolved_session.id)
    user_record, _ = repository.add_turn(db, resolved_session_id, user_text, summary)
    attachment = repository.add_document_attachment(
        db, session_id=resolved_session_id, message_id=cast(int, user_record.id), filename=filename,
        content_type=expected_type, file_data=file_data, kind="uploaded",
        raw_text=raw_text, structured_summary=summary,
    )
    logger.info("chat.document_uploaded", extra={"user_id": user_id, "session_id": resolved_session_id, "document_id": attachment.id})
    return ChatDocumentResponse(response=summary, session_id=resolved_session_id, attachment=attachment)


@router.post(
    "/generate", response_model=ChatDocumentResponse, summary="Generate a chat document",
    description="Create a PDF or Word document from an instruction and optional existing chat. "
    "Omit session_id to create a new chat; uploading a file first is not required.",
    responses={
        404: {"description": "Chat session not found or not owned by this user."},
        422: {"description": "Missing instruction or unsupported output format."},
        503: {"description": "Chat model unavailable."},
    },
)
async def generate_document(
    payload: ChatDocumentGenerateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["sub"])
    session = None
    if payload.session_id is not None:
        session = repository.get_session(db, payload.session_id, user_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
    records = repository.get_message_history(db, payload.session_id) if payload.session_id else []
    summaries = repository.get_document_summaries(db, payload.session_id) if payload.session_id else []
    history = [ChatHistoryMessage(role="assistant" if row.sender == "bot" else "user", content=str(row.content)) for row in records]
    profile_record = profile_service.get(db, user_id)
    profile = profile_service.serialize(profile_record) if profile_record is not None else None
    try:
        content = await asyncio.to_thread(service.generate_content, payload.instruction, summaries, history, profile)
        file_data, filename, content_type = await asyncio.to_thread(service.render, content, payload.output_format)
    except ChatModelUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    # Failed generation must not leave empty sessions behind.
    if session is None:
        session = repository.create_session(db, user_id, payload.instruction[:60])
    resolved_session_id = cast(int, session.id)
    _, bot_record = repository.add_turn(db, resolved_session_id, payload.instruction, content)
    attachment = repository.add_document_attachment(
        db, session_id=resolved_session_id, message_id=cast(int, bot_record.id), filename=filename,
        content_type=content_type, file_data=file_data, kind="generated", structured_summary=content,
    )
    logger.info("chat.document_generated", extra={"user_id": user_id, "session_id": resolved_session_id, "document_id": attachment.id})
    return ChatDocumentResponse(response=content, session_id=resolved_session_id, attachment=attachment)


@router.get("/{document_id}/download", summary="Download a chat document")
def download_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    attachment = repository.get_document_for_user(db, document_id, int(current_user["sub"]))
    if attachment is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return StreamingResponse(
        BytesIO(cast(bytes, attachment.file_data)),
        media_type=cast(str, attachment.content_type),
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(cast(str, attachment.filename))}"},
    )
