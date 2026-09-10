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
    ChatDocumentAutomationRequest,
    ChatDocumentAutomationResponse,
    ChatDocumentGenerateRequest,
    ChatDocumentPipelineRequest,
    ChatDocumentPipelineResponse,
    ChatDocumentPipelineStepOut,
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
from app.services.conversion.document_conversion_service import (
    UnsupportedDocumentConversionError,
)
from app.services.document.document_automation_service import (
    AvailableDocument,
    DocumentAutomationPlanningError,
    DocumentAutomationService,
)
from app.services.document.document_intent_service import DocumentIntentError, DocumentIntentService
from app.services.document.document_pipeline_service import (
    DocumentPipelineService,
    PipelineDocument,
)
from app.services.document.document_validation_service import GeneratedDocumentValidationError
from app.services.profile_service import ProfileService
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/chat/documents", tags=["AI Chat Documents"])
repository = ChatRepository()
service = ChatDocumentService()
intent_service = DocumentIntentService(document_service=service)
pipeline_service = DocumentPipelineService(
    intent_service=intent_service,
    document_service=service,
)
automation_service = DocumentAutomationService(
    pipeline_service=pipeline_service,
)
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


@router.post(
    "/pipeline",
    response_model=ChatDocumentPipelineResponse,
    summary="Run an ordered document pipeline",
    description=(
        "Execute explicit document operations in order. Separate steps with 'then'. Each step "
        "uses an explicitly named file or the chat's persisted latest_document_id, and every "
        "output is reopened and validated before it becomes the next input."
    ),
    responses={
        404: {"description": "Chat session or explicitly selected document was not found."},
        422: {"description": "Missing input, invalid step, or unsupported conversion."},
        503: {"description": "A required converter such as LibreOffice is unavailable."},
    },
)
async def run_document_pipeline(
    payload: ChatDocumentPipelineRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["sub"])
    session = repository.get_session(db, payload.session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    source = None
    if payload.source_document_id is not None:
        source = repository.get_document_for_user(db, payload.source_document_id, user_id)
        if source is None or int(source.session_id) != payload.session_id:
            raise HTTPException(status_code=404, detail="Source document not found in this chat")
    else:
        named_input = intent_service.filename_in(payload.instruction)
        if named_input is not None:
            source = repository.get_document_by_filename_for_user(
                db,
                session_id=payload.session_id,
                filename=named_input,
                user_id=user_id,
            )
        if source is None:
            source = repository.get_latest_document_for_user(db, payload.session_id, user_id)
    if source is None:
        raise HTTPException(
            status_code=422,
            detail="Upload or select a document in this chat before running the pipeline.",
        )

    try:
        plan = pipeline_service.plan(
            payload.instruction,
            input_filename=cast(str, source.filename),
        )
    except (DocumentIntentError, UnsupportedDocumentConversionError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    repository.add_message(db, payload.session_id, "user", payload.instruction)
    current_source = source
    response_steps: list[ChatDocumentPipelineStepOut] = []
    final_attachment = None
    try:
        for step in plan:
            if step.explicitly_named_input:
                named_source = repository.get_document_by_filename_for_user(
                    db,
                    session_id=payload.session_id,
                    filename=cast(str, step.intent.input_file),
                    user_id=user_id,
                )
                if named_source is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Document '{step.intent.input_file}' was not found in this chat.",
                    )
                current_source = named_source
            source_id = cast(int, current_source.id)
            result = await asyncio.to_thread(
                pipeline_service.execute_step,
                step,
                PipelineDocument(
                    file_data=cast(bytes, current_source.file_data),
                    filename=cast(str, current_source.filename),
                ),
            )
            raw_text = await asyncio.to_thread(
                service.extract,
                result.document.file_data,
                result.document.filename,
            )
            _, final_attachment = repository.save_generated_document(
                db,
                session_id=payload.session_id,
                bot_content=f"Step {step.position} complete. {result.summary}",
                filename=result.document.filename,
                content_type=result.document.content_type,
                file_data=result.document.file_data,
                raw_text=raw_text,
                structured_summary=result.summary,
            )
            current_source = final_attachment
            response_steps.append(
                ChatDocumentPipelineStepOut(
                    position=step.position,
                    operation=step.intent.operation.value,
                    source_document_id=source_id,
                    output_document_id=cast(int, final_attachment.id),
                    filename=result.document.filename,
                )
            )
    except HTTPException:
        raise
    except (DocumentIntentError, UnsupportedDocumentConversionError, InvalidDocumentError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except GeneratedDocumentValidationError as error:
        raise HTTPException(
            status_code=422,
            detail=f"A pipeline output failed validation and was not saved: {error}",
        ) from error
    except DocumentProcessingUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    assert final_attachment is not None
    response_text = (
        f"Done. Completed {len(response_steps)} document step"
        f"{'s' if len(response_steps) != 1 else ''} in order. "
        f"The final file is {final_attachment.filename}."
    )
    logger.info(
        "chat.document_pipeline_completed",
        extra={
            "user_id": user_id,
            "session_id": payload.session_id,
            "step_count": len(response_steps),
            "latest_document_id": final_attachment.id,
        },
    )
    return ChatDocumentPipelineResponse(
        response=response_text,
        session_id=payload.session_id,
        attachment=final_attachment,
        latest_document_id=cast(int, final_attachment.id),
        steps=response_steps,
    )


@router.post(
    "/automate",
    response_model=ChatDocumentAutomationResponse,
    summary="Automate document changes from natural language",
    description=(
        "Build and validate an execution plan from a natural-language request, then run it "
        "through the deterministic document pipeline. Ambiguous requests return one clarifying "
        "question. Successful requests return only a simple completion message and the final file."
    ),
    responses={
        404: {"description": "Chat session or selected source document was not found."},
        503: {"description": "The configured text provider could not build a plan."},
    },
)
async def automate_document(
    payload: ChatDocumentAutomationRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["sub"])
    session = repository.get_session(db, payload.session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    documents = repository.get_documents_for_user(db, payload.session_id, user_id)
    selected_source = None
    if payload.source_document_id is not None:
        selected_source = repository.get_document_for_user(db, payload.source_document_id, user_id)
        if selected_source is None or int(selected_source.session_id) != payload.session_id:
            raise HTTPException(status_code=404, detail="Source document not found in this chat")
    else:
        selected_source = repository.get_latest_document_for_user(db, payload.session_id, user_id)
        if selected_source is None and documents:
            selected_source = documents[-1]

    selected_id = int(selected_source.id) if selected_source is not None else None
    available_documents = tuple(
        AvailableDocument(
            document_id=int(document.id),
            filename=str(document.filename),
            document_type=document_type,
            is_latest=int(document.id) == selected_id,
        )
        for document in documents
        if (
            document_type := intent_service.registry.document_type_from_filename(
                str(document.filename)
            )
        )
        is not None
    )
    try:
        plan = await asyncio.to_thread(
            automation_service.plan,
            payload.instruction,
            available_documents,
            explicit_source=payload.source_document_id is not None,
        )
    except ChatModelUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (
        DocumentAutomationPlanningError,
        DocumentIntentError,
        UnsupportedDocumentConversionError,
    ) as error:
        failure = f"I couldn't safely automate that request. {error}"
        repository.add_turn(db, payload.session_id, payload.instruction, failure)
        return ChatDocumentAutomationResponse(
            response=failure,
            session_id=payload.session_id,
            status="failed",
        )

    if plan.status == "clarification_required":
        question = cast(str, plan.clarifying_question)
        repository.add_turn(db, payload.session_id, payload.instruction, question)
        return ChatDocumentAutomationResponse(
            response=question,
            session_id=payload.session_id,
            status="clarification_required",
        )

    assert selected_source is not None
    repository.add_message(db, payload.session_id, "user", payload.instruction)
    current_source = selected_source
    completed_attachments = []
    failure_reason = None
    try:
        for index, step in enumerate(plan.steps):
            if step.explicitly_named_input:
                named_source = repository.get_document_by_filename_for_user(
                    db,
                    session_id=payload.session_id,
                    filename=cast(str, step.intent.input_file),
                    user_id=user_id,
                )
                if named_source is None:
                    raise InvalidDocumentError(
                        "The selected document is no longer available in this chat."
                    )
                current_source = named_source
            result = await asyncio.to_thread(
                pipeline_service.execute_step,
                step,
                PipelineDocument(
                    file_data=cast(bytes, current_source.file_data),
                    filename=cast(str, current_source.filename),
                ),
            )
            raw_text = await asyncio.to_thread(
                service.extract,
                result.document.file_data,
                result.document.filename,
            )
            is_final = index == len(plan.steps) - 1
            _, generated = repository.save_generated_document(
                db,
                session_id=payload.session_id,
                bot_content="Done." if is_final else "Document automation intermediate output.",
                filename=result.document.filename,
                content_type=result.document.content_type,
                file_data=result.document.file_data,
                raw_text=raw_text,
                structured_summary=result.summary,
                is_internal=not is_final,
            )
            completed_attachments.append(generated)
            current_source = generated
    except (
        DocumentIntentError,
        UnsupportedDocumentConversionError,
        InvalidDocumentError,
        GeneratedDocumentValidationError,
        DocumentProcessingUnavailableError,
    ) as error:
        failure_reason = str(error)
    except Exception:
        logger.exception(
            "chat.document_automation_failed",
            extra={"user_id": user_id, "session_id": payload.session_id},
        )
        failure_reason = "A document could not be generated, validated, or stored."

    if failure_reason is not None:
        status: Literal["partial", "failed"] = "partial" if completed_attachments else "failed"
        response = (
            f"I completed part of your request, but couldn't finish it. {failure_reason}"
            if status == "partial"
            else f"I couldn't complete the requested document changes. {failure_reason}"
        )
        repository.add_message(db, payload.session_id, "bot", response)
        latest = completed_attachments[-1] if completed_attachments else None
        return ChatDocumentAutomationResponse(
            response=response,
            session_id=payload.session_id,
            status=status,
            attachments=[latest] if latest is not None else [],
            latest_document_id=int(latest.id) if latest is not None else selected_id,
        )

    final_attachment = completed_attachments[-1]
    logger.info(
        "chat.document_automation_completed",
        extra={
            "user_id": user_id,
            "session_id": payload.session_id,
            "document_id": final_attachment.id,
            "step_count": len(plan.steps),
        },
    )
    return ChatDocumentAutomationResponse(
        response="Done.",
        session_id=payload.session_id,
        status="done",
        attachments=[final_attachment],
        latest_document_id=cast(int, final_attachment.id),
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
