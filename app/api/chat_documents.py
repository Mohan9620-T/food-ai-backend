import asyncio
import logging
from io import BytesIO
from pathlib import Path
from typing import Literal, cast
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
    ChatSpreadsheetOperationRequest,
)
from app.services.chat_document_service import (
    ChatDocumentService,
    DocumentProcessingUnavailableError,
    InvalidDocumentError,
    SpreadsheetOperation,
)
from app.services.chat_service import ChatModelUnavailableError
from app.services.document.document_intent_service import DocumentIntentService
from app.services.profile_service import ProfileService
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/chat/documents", tags=["AI Chat Documents"])
repository = ChatRepository()
service = ChatDocumentService()
intent_service = DocumentIntentService(document_service=service)
profile_service = ProfileService()
logger = logging.getLogger(__name__)
MAX_CHAT_DOCUMENT_BYTES = 15 * 1024 * 1024
DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


@router.post(
    "",
    response_model=ChatDocumentResponse,
    summary="Upload a chat document",
    description="Read and save a PDF, DOCX, XLSX, CSV, PPTX, TXT, or Markdown file before "
    "optional AI analysis. "
    "For XLSX formatting or category-sheet instructions, and XLSX/CSV column-filter instructions, "
    "a revised workbook is "
    "returned "
    "without requiring AI. "
    "An AI failure returns the saved file with analysis_status=unavailable, not an upload error. "
    "Use analyze=false to save/extract without AI. Scanned PDF pages require Tesseract. "
    "Maximum upload size is 15 MB.",
    responses={
        404: {"description": "Chat session not found or not owned by this user."},
        413: {"description": "File exceeds 15 MB."},
        415: {"description": "Unsupported file type."},
        422: {"description": "Empty, unreadable, or password-protected document."},
        503: {"description": "A required extraction dependency, such as OCR, is unavailable."},
    },
)
async def upload_document(
    file: UploadFile = File(...),
    message: str | None = Form(default=None),
    session_id: int | None = Form(default=None),
    analyze: bool = Form(default=True),
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
    if extension in {".md", ".markdown"}:
        accepted_types.add("text/plain")
    if expected_type is None or content_type not in accepted_types:
        raise HTTPException(
            status_code=415,
            detail="Upload a PDF, DOCX, XLSX, CSV, PPTX, TXT, or Markdown document.",
        )
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
        extracted = (
            await asyncio.to_thread(service.extract_document, file_data, filename)
            if intent_service.is_table_to_excel_request(message)
            else None
        )
        operation = service.spreadsheet_operation(message)
        if (
            operation == SpreadsheetOperation.EXPAND_DISH_BY_DIETARY_CATEGORY
            and extension != ".xlsx"
        ):
            raise InvalidDocumentError("Dish category row expansion requires an XLSX workbook.")
        spreadsheet_output = (
            await asyncio.to_thread(service.format_spreadsheet, file_data, filename, message or "")
            if (extension == ".xlsx" and operation is not None)
            or (extension == ".csv" and operation == SpreadsheetOperation.FILTER_COLUMN)
            else None
        )
        table_to_excel_output = (
            await asyncio.to_thread(service.extract_tables_to_excel, extracted, filename)
            if extracted is not None
            else None
        )
    except InvalidDocumentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DocumentProcessingUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    user_text = (message or "").strip() or f"Uploaded {filename}"
    if resolved_session is None:
        resolved_session = repository.create_session(db, user_id, user_text[:60])
    resolved_session_id = cast(int, resolved_session.id)
    saved_notice = (
        "The file and its extracted text are saved. "
        "You can download the original or export the saved text as PDF or Word."
    )
    bot_record, attachment = repository.save_document_upload(
        db,
        session_id=resolved_session_id,
        user_content=user_text,
        bot_content=saved_notice,
        filename=filename,
        content_type=expected_type,
        file_data=file_data,
        raw_text=raw_text,
    )
    response_text = saved_notice
    analysis_status: Literal["complete", "unavailable", "skipped"] = "skipped"
    response_attachment = attachment
    if spreadsheet_output is not None:
        updated_data, updated_filename, actions = spreadsheet_output
        response_text = (
            f"Done — I split the item list from {filename} category-wise and created a new "
            "Excel workbook with a separate sheet for each category.\n\n"
            if operation == SpreadsheetOperation.SPLIT_BY_CATEGORY
            else (
                f"Done. I added the requested column filter and generated the updated Excel "
                f"workbook from {filename}.\n\n"
                if operation == SpreadsheetOperation.FILTER_COLUMN
                else (
                    "Done. I transformed the dish master Excel file into category-specific "
                    "rows for Regular, Easy to Chew, Soft & Bite, Minced & Moist, and Pureed, "
                    "and generated a new Excel workbook.\n\n"
                    if operation == SpreadsheetOperation.EXPAND_DISH_BY_DIETARY_CATEGORY
                    else f"Done — I created a revised Excel workbook from {filename}.\n\n"
                )
            )
        )
        response_text += (
            f"Applied: {', '.join(actions)}.\n\n"
            "The original upload is unchanged. Use Download to get the generated workbook."
        )
        setattr(bot_record, "content", response_text)
        try:
            response_attachment = repository.add_document_attachment(
                db,
                session_id=resolved_session_id,
                message_id=cast(int, bot_record.id),
                filename=updated_filename,
                content_type=DOCUMENT_TYPES[".xlsx"],
                file_data=updated_data,
                kind="generated",
                raw_text=raw_text,
                structured_summary=response_text,
            )
            db.commit()
            db.refresh(response_attachment)
        except Exception as error:
            db.rollback()
            logger.exception(
                "chat.spreadsheet_storage_failed",
                extra={
                    "user_id": user_id,
                    "session_id": resolved_session_id,
                    "generated_filename": updated_filename,
                },
            )
            raise HTTPException(
                status_code=500,
                detail="The generated Excel workbook could not be saved. Please try again.",
            ) from error
    elif table_to_excel_output is not None:
        assert extracted is not None
        updated_data, updated_filename, updated_content_type = table_to_excel_output
        response_text = (
            f"Done. I extracted {len(extracted.tables)} table"
            f"{'s' if len(extracted.tables) != 1 else ''} from {filename} and created an "
            "Excel workbook. The original PDF is unchanged. Use Download to get the workbook."
        )
        setattr(bot_record, "content", response_text)
        try:
            response_attachment = repository.add_document_attachment(
                db,
                session_id=resolved_session_id,
                message_id=cast(int, bot_record.id),
                filename=updated_filename,
                content_type=updated_content_type,
                file_data=updated_data,
                kind="generated",
                raw_text=raw_text,
                structured_summary=response_text,
            )
            db.commit()
            db.refresh(response_attachment)
        except Exception as error:
            db.rollback()
            logger.exception(
                "chat.pdf_table_storage_failed",
                extra={"user_id": user_id, "session_id": resolved_session_id},
            )
            raise HTTPException(
                status_code=500,
                detail="The extracted Excel workbook could not be saved. Please try again.",
            ) from error
    elif analyze:
        try:
            response_text = await service.summarize(raw_text, message)
            setattr(attachment, "structured_summary", response_text)
            analysis_status = "complete"
        except ChatModelUnavailableError:
            analysis_status = "unavailable"
            preview = "\n".join(f"    {line}" for line in raw_text[:6000].splitlines())
            preview_label = (
                f"Extracted text preview (first 6,000 of {len(raw_text):,} characters):"
                if len(raw_text) > 6000
                else "Extracted text:"
            )
            response_text = (
                f"{saved_notice}\n\nAI analysis is currently unavailable. "
                "The text below was extracted from your file; it is not an AI answer or summary. "
                "You do not need to upload the file again.\n\n"
                f"{preview_label}\n\n{preview}"
            )
            logger.warning(
                "chat.document_analysis_unavailable",
                extra={
                    "user_id": user_id,
                    "session_id": resolved_session_id,
                    "document_id": attachment.id,
                },
            )
        setattr(bot_record, "content", response_text)
        db.commit()
        db.refresh(attachment)
    logger.info(
        "chat.document_uploaded",
        extra={"user_id": user_id, "session_id": resolved_session_id, "document_id": attachment.id},
    )
    return ChatDocumentResponse(
        response=response_text,
        session_id=resolved_session_id,
        attachment=response_attachment,
        analysis_status=analysis_status,
    )


@router.post(
    "/spreadsheet",
    response_model=ChatDocumentResponse,
    summary="Update the current chat spreadsheet",
    description="Apply a deterministic spreadsheet operation to the latest generated XLSX in "
    "the session, or to the latest uploaded spreadsheet when no generated XLSX exists.",
    responses={
        404: {"description": "Chat session not found or not owned by this user."},
        422: {"description": "No source workbook, unsupported request, or invalid workbook."},
    },
)
async def update_session_spreadsheet(
    payload: ChatSpreadsheetOperationRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["sub"])
    session = repository.get_session(db, payload.session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    operation = service.spreadsheet_operation(payload.instruction)
    if operation not in {
        SpreadsheetOperation.FILTER_COLUMN,
        SpreadsheetOperation.EXPAND_DISH_BY_DIETARY_CATEGORY,
    }:
        raise HTTPException(
            status_code=422,
            detail="This spreadsheet request is not supported.",
        )
    source = repository.get_spreadsheet_operation_source(db, payload.session_id)
    if source is None:
        raise HTTPException(
            status_code=422,
            detail="Upload an Excel spreadsheet first, then ask me to add the filter.",
        )
    source_filename = cast(str, source.filename)
    if (
        operation == SpreadsheetOperation.EXPAND_DISH_BY_DIETARY_CATEGORY
        and Path(source_filename).suffix.casefold() != ".xlsx"
    ):
        raise HTTPException(
            status_code=422,
            detail="Dish category row expansion requires an XLSX workbook.",
        )
    try:
        updated_data, updated_filename, actions = await asyncio.to_thread(
            service.format_spreadsheet,
            cast(bytes, source.file_data),
            source_filename,
            payload.instruction,
        )
    except InvalidDocumentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if operation == SpreadsheetOperation.EXPAND_DISH_BY_DIETARY_CATEGORY:
        response_text = (
            "Done. I transformed the dish master Excel file into category-specific rows for "
            "Regular, Easy to Chew, Soft & Bite, Minced & Moist, and Pureed, and generated a "
            "new Excel workbook.\n\n"
            f"Applied: {', '.join(actions)}.\n\n"
            "The source workbook is unchanged. Use Download to get the generated workbook."
        )
    else:
        response_text = (
            "Done. I added the requested column filter and generated the updated Excel workbook.\n\n"
            f"Applied: {', '.join(actions)}.\n\n"
            "The source workbook is unchanged. Use Download to get the generated workbook."
        )
    _, bot_record = repository.add_turn(db, payload.session_id, payload.instruction, response_text)
    try:
        attachment = repository.add_document_attachment(
            db,
            session_id=payload.session_id,
            message_id=cast(int, bot_record.id),
            filename=updated_filename,
            content_type=DOCUMENT_TYPES[".xlsx"],
            file_data=updated_data,
            kind="generated",
            raw_text=cast(str | None, source.raw_text),
            structured_summary=response_text,
        )
    except Exception as error:
        db.rollback()
        logger.exception(
            "chat.spreadsheet_storage_failed",
            extra={
                "user_id": user_id,
                "session_id": payload.session_id,
                "generated_filename": updated_filename,
            },
        )
        raise HTTPException(
            status_code=500,
            detail="The generated Excel workbook could not be saved. Please try again.",
        ) from error
    logger.info(
        "chat.spreadsheet_updated",
        extra={
            "user_id": user_id,
            "session_id": payload.session_id,
            "source_document_id": source.id,
            "document_id": attachment.id,
        },
    )
    return ChatDocumentResponse(
        response=response_text,
        session_id=payload.session_id,
        attachment=attachment,
        analysis_status="skipped",
    )


@router.post(
    "/generate",
    response_model=ChatDocumentResponse,
    summary="Generate a chat document",
    description="Create a PDF, DOCX, XLSX, CSV, PPTX, TXT, or Markdown document from an "
    "instruction and optional existing chat. "
    "Omit session_id to create a new chat; uploading a file first is not required. "
    "Use mode=export to save instruction text directly without calling any AI provider. "
    "In export mode, source_document_id instead exports the full extracted text of a saved "
    "uploaded file; instruction may be omitted and is not used as document content. "
    "AI mode uses a total document deadline (45 seconds by default).",
    responses={
        404: {"description": "Chat session not found or not owned by this user."},
        422: {"description": "Missing instruction or unsupported output format."},
        503: {"description": "AI generation timed out or failed. Retry or use mode=export."},
    },
)
async def generate_document(
    payload: ChatDocumentGenerateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["sub"])
    session = None
    source_document = None
    if payload.source_document_id is not None:
        source_document = repository.get_document_for_user(db, payload.source_document_id, user_id)
        if source_document is None or (
            payload.session_id is not None and payload.session_id != source_document.session_id
        ):
            raise HTTPException(status_code=404, detail="Source document not found in this chat")
        if source_document.kind != "uploaded" or not str(source_document.raw_text or "").strip():
            raise HTTPException(
                status_code=422, detail="This file has no extracted text to export."
            )
        session = repository.get_session(db, cast(int, source_document.session_id), user_id)
    if payload.session_id is not None:
        session = repository.get_session(db, payload.session_id, user_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
    try:
        if payload.mode == "export":
            # Explicit export does not reinterpret source text, read other history,
            # or depend on provider availability. It is not an AI-generated answer.
            content = (
                str(source_document.raw_text)
                if source_document is not None
                else payload.instruction
            )
        else:
            records = (
                repository.get_message_history(db, payload.session_id) if payload.session_id else []
            )
            summaries = (
                repository.get_document_summaries(db, payload.session_id)
                if payload.session_id
                else []
            )
            history = [
                ChatHistoryMessage(
                    role="assistant" if row.sender == "bot" else "user", content=str(row.content)
                )
                for row in records
            ]
            profile_record = profile_service.get(db, user_id)
            profile = (
                profile_service.serialize(profile_record) if profile_record is not None else None
            )
            content = await service.generate_content(
                payload.instruction, summaries, history, profile
            )
        file_data, filename, content_type = await asyncio.to_thread(
            service.render,
            content,
            payload.output_format,
            plain_text=payload.mode == "export",
            requested_filename=payload.filename,
        )
    except ChatModelUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except InvalidDocumentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    # Failed generation must not leave empty sessions behind.
    if session is None:
        session = repository.create_session(db, user_id, payload.instruction[:60])
    resolved_session_id = cast(int, session.id)
    user_text = (
        f"Export extracted text from {source_document.filename} as {payload.output_format.upper()}"
        if source_document is not None
        else payload.instruction
    )
    _, bot_record = repository.add_turn(db, resolved_session_id, user_text, content)
    attachment = repository.add_document_attachment(
        db,
        session_id=resolved_session_id,
        message_id=cast(int, bot_record.id),
        filename=filename,
        content_type=content_type,
        file_data=file_data,
        kind="generated",
        structured_summary=content,
    )
    logger.info(
        "chat.document_generated",
        extra={"user_id": user_id, "session_id": resolved_session_id, "document_id": attachment.id},
    )
    return ChatDocumentResponse(
        response=content, session_id=resolved_session_id, attachment=attachment
    )


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
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(cast(str, attachment.filename))}"
        },
    )
