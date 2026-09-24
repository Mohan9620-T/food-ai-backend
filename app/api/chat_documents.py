import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.models.chat import ChatDocumentAttachment, ChatMessageRecord
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat import (
    ChatDocumentAttachmentOut,
    ChatDocumentAutomationRequest,
    ChatDocumentAutomationResponse,
    ChatDocumentAutomationStepOut,
    ChatDocumentGenerateRequest,
    ChatDocumentPipelineRequest,
    ChatDocumentPipelineResponse,
    ChatDocumentPipelineStepOut,
    ChatDocumentResponse,
    ChatHistoryMessage,
    ChatSpreadsheetOperationRequest,
    ClarificationQuestionOut,
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
from app.services.document import semantic_retrieval
from app.services.document.document_automation_service import (
    AvailableDocument,
    DocumentAutomationPlanningError,
    DocumentAutomationService,
)
from app.services.document.document_generation_service import DocumentGenerationService
from app.services.document.document_intent_service import DocumentIntentError, DocumentIntentService
from app.services.document.document_modification_service import (
    DocumentModificationService,
    ModifiedDocument,
)
from app.services.document.document_operation_registry import (
    DocumentOperation,
    DocumentType,
    Fidelity,
)
from app.services.document.document_pipeline_service import (
    DocumentPipelineService,
    PipelineDocument,
)
from app.services.document.document_references import references_document, references_image
from app.services.document.document_validation_service import (
    FidelityAssessment,
    GeneratedDocumentValidationError,
)
from app.services.document.extraction_models import (
    DocumentEdit,
    ExtractedDocument,
    ExtractionMode,
    StructuredDocumentContent,
)
from app.services.image_validation import InvalidImageError, validate_image_content
from app.services.profile_service import ProfileService
from app.services.spreadsheet.row_append import AppendRowsRequest, WorkbookRowAppender
from app.services.spreadsheet.row_selection import RowSelection, SelectedRows, WorkbookRowSelector
from app.services.spreadsheet.spreadsheet_preview_service import SpreadsheetPreviewService
from app.services.spreadsheet.workbook_lookup_service import prepare_workbook_lookup
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/chat/documents", tags=["AI Chat Documents"])
repository = ChatRepository()
service = ChatDocumentService()
intent_service = DocumentIntentService(document_service=service)
modification_service = DocumentModificationService(reader=service.document_reader)
pipeline_service = DocumentPipelineService(
    intent_service=intent_service,
    document_service=service,
)
automation_service = DocumentAutomationService(
    pipeline_service=pipeline_service,
)
profile_service = ProfileService()
logger = logging.getLogger(__name__)
_pipeline_locks_guard = threading.Lock()
_pipeline_locks: dict[tuple[int, int], asyncio.Lock] = {}
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
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@asynccontextmanager
async def _session_pipeline_lock(session_id: int):
    """Serialize same-session pipelines in-process; Postgres also locks the session row."""
    loop_key = id(asyncio.get_running_loop())
    with _pipeline_locks_guard:
        lock = _pipeline_locks.setdefault((loop_key, session_id), asyncio.Lock())
    async with lock:
        yield


def _automation_history(db: Session, session_id: int) -> list[ChatHistoryMessage]:
    return [
        ChatHistoryMessage(
            role="user" if record.sender == "user" else "assistant",
            content=str(record.content),
        )
        for record in repository.get_message_history(db, session_id, limit=12)
    ]


def _save_automation_response(
    db: Session,
    request: ChatDocumentAutomationRequest,
    response: ChatDocumentAutomationResponse,
    record: ChatMessageRecord | None = None,
) -> ChatDocumentAutomationResponse:
    if record is None:
        _, record = repository.add_turn(
            db, request.session_id, request.instruction, response.response, commit=False
        )
    repository.save_automation_state(db, record, request, response)
    db.commit()
    return response


def _fidelity_message(results: list[tuple[str, str, str | None]]) -> str:
    if not results:
        return ""
    details = [
        f"{label}: {level}" + (f" ({note})" if note else "") for label, level, note in results
    ]
    return " Verified fidelity: " + "; ".join(details) + "."


def _completion_message(filenames: list[str], text_result: str | None) -> str:
    if not filenames:
        return text_result or "Done. I completed the document request."
    if len(filenames) == 1:
        completion = f"Done — I created {filenames[0]}. It is attached below."
    else:
        joined = ", ".join(filenames[:-1]) + f" and {filenames[-1]}"
        completion = f"Done — I created {joined}. All files are attached below."
    return f"{text_result}\n\n{completion}" if text_result else completion


async def _save_selected_rows(
    db: Session,
    source: ChatDocumentAttachment,
    instruction: str,
    selected: SelectedRows,
    selection: RowSelection,
    bot_record: ChatMessageRecord | None = None,
) -> ChatDocumentResponse:
    filename = DocumentGenerationService.safe_filename(
        f"{Path(str(source.filename)).stem}-selected-rows.xlsx", DocumentType.XLSX
    )
    response_text = (
        selected.markdown() + "\n\nDownload the Excel file below for all matching rows and columns."
    )
    session_id = int(source.session_id)
    extracted = await asyncio.to_thread(service.extract_document, selected.file_data, filename)
    try:
        if bot_record is None:
            _, bot_record = repository.add_turn(
                db, session_id, instruction, response_text, commit=False
            )
        else:
            setattr(bot_record, "content", response_text)
        attachment = repository.add_document_attachment(
            db,
            session_id=session_id,
            message_id=int(bot_record.id),
            filename=filename,
            content_type=DOCUMENT_TYPES[".xlsx"],
            file_data=selected.file_data,
            kind="generated",
            extracted=extracted,
            structured_summary=selected.summary,
            generation_metadata={
                "instruction": instruction,
                "source_document_ids": [int(source.id)],
                "operation": "extract_matching_rows",
                "parameters": selection.model_dump(exclude_none=True),
            },
        )
        db.commit()
        db.refresh(attachment)
    except Exception:
        db.rollback()
        raise
    return ChatDocumentResponse(
        response=response_text,
        session_id=session_id,
        attachment=attachment,
        analysis_status="complete",
        fidelity=Fidelity.HIGH.value,
        fidelity_note="Selected row values and column order preserved in a new workbook.",
    )


@router.post(
    "",
    response_model=ChatDocumentResponse,
    summary="Upload a chat document",
    description="Read and save an image, PDF, DOCX, XLSX, CSV, PPTX, TXT, or Markdown file before "
    "optional AI analysis. "
    "For XLSX formatting or category-sheet instructions, and XLSX/CSV column-filter instructions, "
    "a revised workbook is "
    "returned "
    "without requiring AI. "
    "An AI failure returns the saved file with analysis_status=unavailable, not an upload error. "
    "Use analyze=false to save without AI; image extraction is deferred until Build. "
    "Scanned PDF pages require Tesseract. Images support JPEG, PNG, WebP and GIF, up to 8 MB. "
    "Other documents support up to 15 MB.",
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
            detail="Upload an image, PDF, DOCX, XLSX, CSV, PPTX, TXT, or Markdown document.",
        )
    try:
        file_data = await file.read(MAX_CHAT_DOCUMENT_BYTES + 1)
    finally:
        await file.close()
    if len(file_data) > MAX_CHAT_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document is too large. Maximum size is 15 MB.")
    if not file_data:
        raise HTTPException(status_code=422, detail="The uploaded document is empty.")
    image_upload = expected_type.startswith("image/")
    if image_upload:
        if len(file_data) > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Image is too large. Maximum size is 8 MB.")
        try:
            validate_image_content(file_data, expected_type)
        except InvalidImageError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    user_id = int(current_user["sub"])
    resolved_session = None
    if session_id is not None:
        resolved_session = repository.get_session(db, session_id, user_id)
        if resolved_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
    try:
        if image_upload and not analyze:
            # Save the original before the chained automation request extracts its data.
            extracted = ExtractedDocument(
                document_type=DocumentType.IMAGE,
                text="",
                extraction_mode=ExtractionMode.OCR,
            )
            response_fidelity = FidelityAssessment(
                Fidelity.BEST_EFFORT, "The image is saved; its data has not been extracted yet."
            )
        else:
            extracted = await asyncio.to_thread(service.extract_document, file_data, filename)
            response_fidelity = pipeline_service.validator.fidelity_for_extraction(extracted)
        raw_text = extracted.text
        append_requested = (
            analyze and extension == ".xlsx" and AppendRowsRequest.is_request(message or "")
        )
        selection = (
            RowSelection.from_instruction(message or "", workbook_context=True)
            if analyze and extension == ".xlsx" and not append_requested
            else None
        )
        selected_rows = (
            await asyncio.to_thread(WorkbookRowSelector().select, file_data, selection)
            if selection is not None
            else None
        )
        row_count_response = (
            service.spreadsheet_row_count_response(extracted, filename)
            if analyze and service.is_spreadsheet_row_count_request(message)
            else None
        )
        # A save-only upload must never interpret an instruction as an edit.
        # The composer sends file actions to /automate after the source is saved.
        table_to_excel_requested = analyze and intent_service.is_table_to_excel_request(message)
        operation = (
            service.spreadsheet_operation(message)
            if analyze and selection is None and not append_requested
            else None
        )
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
            if table_to_excel_requested
            else None
        )
        modification_output: ModifiedDocument | None = None
        clarification: str | None = None
        if append_requested:
            append_request = AppendRowsRequest.from_instruction(message or "")
            if append_request is None:
                clarification = DocumentAutomationService.APPEND_NEEDS_DATA
            else:
                try:
                    updated, appended = await asyncio.to_thread(
                        WorkbookRowAppender().append, file_data, append_request
                    )
                    modification_output = ModifiedDocument(
                        file_data=updated,
                        filename=DocumentGenerationService.safe_filename(
                            f"{Path(filename).stem}-updated", DocumentType.XLSX
                        ),
                        content_type=DOCUMENT_TYPES[".xlsx"],
                        document_type=DocumentType.XLSX,
                        fidelity=Fidelity.HIGH,
                        summary=appended.summary,
                        fidelity_note="New rows added; existing records and formulas preserved.",
                    )
                except InvalidDocumentError as error:
                    clarification = str(error)
        if (
            analyze
            and not append_requested
            and selection is None
            and spreadsheet_output is None
            and table_to_excel_output is None
            and intent_service.is_modification_request(message, filename)
        ):
            plan = await asyncio.to_thread(
                intent_service.resolve_plan, message or "", input_file=filename
            )
            if plan.needs_clarification:
                clarification = plan.clarification_question
            else:
                step = plan.steps[0]
                edits = tuple(
                    DocumentEdit.model_validate(item)
                    for item in cast(list[object], step.parameters.get("edits", []))
                )
                if not edits:
                    clarification = (
                        "Describe the cells or content to change and provide the new values."
                    )
                else:
                    modification_output = await asyncio.to_thread(
                        modification_service.modify, file_data, filename, edits
                    )
    except DocumentIntentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (InvalidDocumentError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DocumentProcessingUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    user_text = (message or "").strip() or f"Uploaded {filename}"
    if resolved_session is None:
        resolved_session = repository.create_session(db, user_id, user_text[:60])
    resolved_session_id = cast(int, resolved_session.id)
    saved_notice = (
        "The image is saved."
        if image_upload and not analyze
        else "The file and its extracted text are saved. "
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
        extracted=extracted if not (image_upload and not analyze) else None,
    )
    if settings.ENABLE_SEMANTIC_RAG and not (image_upload and not analyze):
        await asyncio.to_thread(
            semantic_retrieval.index_document,
            user_id=user_id,
            session_id=resolved_session_id,
            document_id=cast(int, attachment.id),
            filename=filename,
            extracted=extracted,
        )
    if selected_rows is not None and selection is not None:
        return await _save_selected_rows(
            db, attachment, user_text, selected_rows, selection, bot_record
        )
    response_text = saved_notice
    analysis_status: Literal["complete", "unavailable", "skipped"] = "skipped"
    response_attachment = attachment
    if modification_output is not None:
        response_fidelity = FidelityAssessment(
            modification_output.fidelity, modification_output.fidelity_note
        )
        response_text = (
            f"Done. I created {modification_output.filename} with the requested targeted "
            f"changes. {modification_output.summary} The original upload is unchanged. "
            f"Actual fidelity: {modification_output.fidelity.value}."
        )
        setattr(bot_record, "content", response_text)
        try:
            response_attachment = repository.add_document_attachment(
                db,
                session_id=resolved_session_id,
                message_id=cast(int, bot_record.id),
                filename=modification_output.filename,
                content_type=modification_output.content_type,
                file_data=modification_output.file_data,
                kind="generated",
                extracted=await asyncio.to_thread(
                    service.extract_document,
                    modification_output.file_data,
                    modification_output.filename,
                ),
                structured_summary=response_text,
                generation_metadata={
                    "instruction": user_text,
                    "source_document_ids": [int(attachment.id)],
                },
            )
            db.commit()
            db.refresh(response_attachment)
            analysis_status = "complete"
        except Exception as error:
            db.rollback()
            logger.exception(
                "chat.document_modification_storage_failed",
                extra={"user_id": user_id, "session_id": resolved_session_id},
            )
            raise HTTPException(
                status_code=500,
                detail="The modified document could not be saved. Please try again.",
            ) from error
    elif clarification:
        response_text = clarification
        setattr(bot_record, "content", response_text)
        db.commit()
    elif spreadsheet_output is not None:
        response_fidelity = FidelityAssessment(
            Fidelity.HIGH if operation == SpreadsheetOperation.SPLIT_BY_CATEGORY else Fidelity.FULL,
            (
                "Rows were reorganized into category sheets; source cell values were preserved."
                if operation == SpreadsheetOperation.SPLIT_BY_CATEGORY
                else None
            ),
        )
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
                structured_summary=response_text,
                extracted=await asyncio.to_thread(
                    service.extract_document, updated_data, updated_filename
                ),
                generation_metadata={
                    "instruction": user_text,
                    "source_document_ids": [int(attachment.id)],
                },
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
        response_fidelity = FidelityAssessment(
            Fidelity.HIGH,
            "Extracted table values and order were validated; source visual layout was not retained.",
        )
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
                structured_summary=response_text,
                extracted=await asyncio.to_thread(
                    service.extract_document, updated_data, updated_filename
                ),
                generation_metadata={
                    "instruction": user_text,
                    "source_document_ids": [int(attachment.id)],
                },
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
    elif row_count_response is not None:
        response_text = row_count_response
        setattr(bot_record, "content", response_text)
        setattr(attachment, "structured_summary", response_text)
        analysis_status = "complete"
        db.commit()
        db.refresh(attachment)
    elif analyze:
        try:
            response_text = await service.summarize(extracted, message)
            response_fidelity = FidelityAssessment(
                Fidelity.HIGH,
                f"This AI summary used the extracted coverage: {extracted.coverage}.",
            )
            setattr(attachment, "structured_summary", response_text)
            analysis_status = "complete"
        except ChatModelUnavailableError:
            analysis_status = "unavailable"
            preview_text = raw_text[:6000]
            # A plain 4-space indent is not a fenced code block to every renderer;
            # use a real fence (widened if the extracted text itself contains one) so
            # the columns render in a monospace block instead of misaligning in prose.
            fence = "`" * 3
            while fence in preview_text:
                fence += "`"
            preview_label = (
                f"Extracted text preview (first 6,000 of {len(raw_text):,} characters):"
                if len(raw_text) > 6000
                else "Extracted text:"
            )
            response_text = (
                f"{saved_notice}\n\nAI analysis is currently unavailable. "
                "The text below was extracted from your file; it is not an AI answer or summary. "
                "You do not need to upload the file again.\n\n"
                f"{preview_label}\n\n{fence}\n{preview_text}\n{fence}"
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
        fidelity=response_fidelity.fidelity.value,
        fidelity_note=response_fidelity.note,
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
    try:
        lookup = prepare_workbook_lookup(db, payload.session_id, payload.instruction)
        if lookup is not None:
            selected = await asyncio.to_thread(lookup.select)
            return await _save_selected_rows(
                db, lookup.source, payload.instruction, selected, lookup.selection
            )
    except InvalidDocumentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    operation = service.spreadsheet_operation(payload.instruction)
    row_count_requested = service.is_spreadsheet_row_count_request(payload.instruction)
    if (
        operation
        not in {
            SpreadsheetOperation.FILTER_COLUMN,
            SpreadsheetOperation.EXPAND_DISH_BY_DIETARY_CATEGORY,
        }
        and not row_count_requested
    ):
        raise HTTPException(
            status_code=422,
            detail="This spreadsheet request is not supported.",
        )
    source = repository.get_spreadsheet_operation_source(db, payload.session_id)
    if source is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Upload an Excel or CSV spreadsheet first, then ask your row-count question."
                if row_count_requested
                else "Upload an Excel spreadsheet first, then ask me to add the filter."
            ),
        )
    source_filename = cast(str, source.filename)
    if row_count_requested and operation is None:
        try:
            extracted = await asyncio.to_thread(
                service.extract_document,
                cast(bytes, source.file_data),
                source_filename,
            )
            response_text = service.spreadsheet_row_count_response(extracted, source_filename)
        except InvalidDocumentError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        repository.add_turn(db, payload.session_id, payload.instruction, response_text)
        fidelity = pipeline_service.validator.fidelity_for_extraction(extracted)
        logger.info(
            "chat.spreadsheet_row_counted",
            extra={
                "user_id": user_id,
                "session_id": payload.session_id,
                "source_document_id": source.id,
            },
        )
        return ChatDocumentResponse(
            response=response_text,
            session_id=payload.session_id,
            analysis_status="complete",
            fidelity=fidelity.fidelity.value,
            fidelity_note=fidelity.note,
        )
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
    _, bot_record = repository.add_turn(
        db, payload.session_id, payload.instruction, response_text, commit=False
    )
    try:
        attachment = repository.add_document_attachment(
            db,
            session_id=payload.session_id,
            message_id=cast(int, bot_record.id),
            filename=updated_filename,
            content_type=DOCUMENT_TYPES[".xlsx"],
            file_data=updated_data,
            kind="generated",
            structured_summary=response_text,
            extracted=await asyncio.to_thread(
                service.extract_document, updated_data, updated_filename
            ),
            generation_metadata={
                "instruction": payload.instruction,
                "source_document_ids": [int(source.id)],
            },
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
        fidelity=Fidelity.FULL.value,
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
        if payload.mode == "export" and (
            source_document.kind != "uploaded" or not str(source_document.raw_text or "").strip()
        ):
            raise HTTPException(
                status_code=422, detail="This file has no extracted text to export."
            )
        session = repository.get_session(db, cast(int, source_document.session_id), user_id)
    if payload.session_id is not None:
        session = repository.get_session(db, payload.session_id, user_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
    content: str | StructuredDocumentContent
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
            if source_document is not None:
                selected_document = await asyncio.to_thread(
                    service.extract_document,
                    cast(bytes, source_document.file_data),
                    cast(str, source_document.filename),
                )
                content = await service.generate_content(
                    payload.instruction,
                    summaries,
                    history,
                    profile,
                    documents=[selected_document],
                )
            else:
                content = await service.generate_content(
                    payload.instruction, summaries, history, profile
                )
        response_content = service.content_for_chat(content)
        requested_filename = payload.filename
        if requested_filename is None and source_document is not None and payload.mode == "ai":
            requested_filename = f"{Path(cast(str, source_document.filename)).stem}_generated"
        file_data, filename, content_type = await asyncio.to_thread(
            service.render,
            content,
            payload.output_format,
            plain_text=payload.mode == "export",
            requested_filename=requested_filename,
        )
        output_type = (
            DocumentType.MARKDOWN
            if payload.output_format == "markdown"
            else DocumentType(payload.output_format)
        )
        source_text = (
            content.to_markdown()
            if isinstance(content, StructuredDocumentContent)
            and output_type == DocumentType.MARKDOWN
            else (
                content.to_plain_text()
                if isinstance(content, StructuredDocumentContent)
                else content
            )
        )
        response_fidelity = pipeline_service.validator.assess_generation(
            file_data, output_type, source_text=source_text
        )
        extracted_output = await asyncio.to_thread(service.extract_document, file_data, filename)
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
        if source_document is not None and payload.mode == "export"
        else payload.instruction
    )
    _, bot_record = repository.add_turn(
        db, resolved_session_id, user_text, response_content, commit=False
    )
    attachment = repository.add_document_attachment(
        db,
        session_id=resolved_session_id,
        message_id=cast(int, bot_record.id),
        filename=filename,
        content_type=content_type,
        file_data=file_data,
        kind="generated",
        raw_text=response_content,
        structured_summary=response_content,
        extracted=extracted_output,
        generation_metadata={
            "instruction": payload.instruction,
            "mode": payload.mode,
            "source_document_ids": [int(source_document.id)] if source_document is not None else [],
            "content": content.model_dump(mode="json")
            if isinstance(content, StructuredDocumentContent)
            else content,
            "assumptions": list(content.assumptions)
            if isinstance(content, StructuredDocumentContent)
            else [],
            "fidelity": response_fidelity.fidelity.value,
            "fidelity_note": response_fidelity.note,
        },
    )
    logger.info(
        "chat.document_generated",
        extra={"user_id": user_id, "session_id": resolved_session_id, "document_id": attachment.id},
    )
    return ChatDocumentResponse(
        response=response_content,
        session_id=resolved_session_id,
        attachment=attachment,
        fidelity=response_fidelity.fidelity.value,
        fidelity_note=response_fidelity.note,
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
    async with _session_pipeline_lock(payload.session_id):
        locked_session = repository.get_session_for_update(db, payload.session_id, user_id)
        if locked_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")

        source = None
        if payload.source_document_id is not None:
            source = repository.get_document_for_user(db, payload.source_document_id, user_id)
            if source is None or int(source.session_id) != payload.session_id:
                db.rollback()
                raise HTTPException(
                    status_code=404, detail="Source document not found in this chat"
                )
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
            db.rollback()
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
            db.rollback()
            raise HTTPException(status_code=422, detail=str(error)) from error
        except DocumentProcessingUnavailableError as error:
            db.rollback()
            raise HTTPException(status_code=503, detail=str(error)) from error

        repository.add_message(db, payload.session_id, "user", payload.instruction, commit=False)
        current_source = source
        response_steps: list[ChatDocumentPipelineStepOut] = []
        response_summaries: list[str] = []
        generated_records = []
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
                        raise InvalidDocumentError(
                            "A selected document is no longer available in this chat."
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
                extracted_output = await asyncio.to_thread(
                    service.extract_document,
                    result.document.file_data,
                    result.document.filename,
                )
                record, final_attachment = repository.save_generated_document(
                    db,
                    session_id=payload.session_id,
                    bot_content=f"Created {result.document.filename}.",
                    filename=result.document.filename,
                    content_type=result.document.content_type,
                    file_data=result.document.file_data,
                    raw_text=extracted_output.text,
                    extracted=extracted_output,
                    structured_summary=result.summary,
                    is_internal=True,
                    commit=False,
                    locked_session=locked_session,
                )
                generated_records.append(record)
                current_source = final_attachment
                response_summaries.append(result.summary)
                response_steps.append(
                    ChatDocumentPipelineStepOut(
                        position=step.position,
                        operation=step.intent.operation.value,
                        source_document_id=source_id,
                        output_document_id=cast(int, final_attachment.id),
                        filename=result.document.filename,
                        fidelity=result.document.fidelity.value,
                        fidelity_note=result.document.fidelity_note,
                    )
                )
        except (
            DocumentIntentError,
            UnsupportedDocumentConversionError,
            InvalidDocumentError,
        ) as error:
            db.commit()
            raise HTTPException(status_code=422, detail=str(error)) from error
        except GeneratedDocumentValidationError as error:
            db.commit()
            raise HTTPException(
                status_code=422,
                detail=f"A pipeline output failed validation and was not saved: {error}",
            ) from error
        except DocumentProcessingUnavailableError as error:
            db.commit()
            raise HTTPException(status_code=503, detail=str(error)) from error

        assert final_attachment is not None
        response_text = (
            f"Done. Completed {len(response_steps)} document step"
            f"{'s' if len(response_steps) != 1 else ''} in order. "
            f"{' '.join(response_summaries)} The final file is {final_attachment.filename}."
        )
        setattr(generated_records[-1], "is_internal", False)
        setattr(generated_records[-1], "content", response_text)
        db.commit()
        db.refresh(final_attachment)
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
        "Plan a document workflow from the instruction and chat history. Ambiguous requests "
        "return a clarifying question, optionally with choices. Ready requests execute directly, "
        "returning all requested text results and validated downloadable files."
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
    async with _session_pipeline_lock(payload.session_id):
        locked_session = repository.get_session_for_update(db, payload.session_id, user_id)
        if locked_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")

        request_instruction = payload.instruction
        source_document_id = payload.source_document_id
        if (
            payload.source_mode == "auto"
            and source_document_id is None
            and (
                references_image(request_instruction)
                or references_document(request_instruction, creation=True)
            )
        ):
            repository.ensure_image_document_sources(db, payload.session_id)
        documents = repository.get_documents_for_user(db, payload.session_id, user_id)
        selected_source = None
        if source_document_id is not None:
            selected_source = repository.get_document_for_user(db, source_document_id, user_id)
            if selected_source is None or int(selected_source.session_id) != payload.session_id:
                db.rollback()
                raise HTTPException(
                    status_code=404, detail="Source document not found in this chat"
                )
        else:
            selected_source = repository.get_latest_document_for_user(
                db, payload.session_id, user_id
            )
            if selected_source is None and documents:
                selected_source = documents[-1]

        selected_id = int(selected_source.id) if selected_source is not None else None
        available_documents = tuple(
            AvailableDocument(
                document_id=int(document.id),
                filename=str(document.filename),
                document_type=document_type,
                is_latest=int(document.id) == selected_id,
                kind=cast(Literal["uploaded", "generated"], str(document.kind)),
            )
            for document in documents
            if (
                document_type := intent_service.registry.document_type_from_filename(
                    str(document.filename)
                )
            )
            is not None
        )
        history = _automation_history(db, payload.session_id)
        try:
            plan = await asyncio.wait_for(
                asyncio.to_thread(
                    automation_service.plan,
                    request_instruction,
                    available_documents,
                    explicit_source=source_document_id is not None,
                    conversation_history=history,
                    source_mode=payload.source_mode,
                ),
                timeout=settings.DOCUMENT_AI_TIMEOUT_SECONDS,
            )
        except TimeoutError as error:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="The document planner took too long to respond. Please retry your request.",
            ) from error
        except ChatModelUnavailableError as error:
            db.rollback()
            raise HTTPException(status_code=503, detail=str(error)) from error
        except (
            DocumentAutomationPlanningError,
            DocumentIntentError,
            UnsupportedDocumentConversionError,
        ) as error:
            failure = f"I couldn't safely complete that document request. {error}"
            return _save_automation_response(
                db,
                payload,
                ChatDocumentAutomationResponse(
                    response=failure, session_id=payload.session_id, status="failed"
                ),
            )

        if plan.status == "clarification_required":
            question = cast(str, plan.clarifying_question)
            response_payload = ChatDocumentAutomationResponse(
                response=question,
                session_id=payload.session_id,
                status="clarification_required",
                clarification=ClarificationQuestionOut(
                    question=question, options=list(plan.clarification_options)
                ),
            )
            return _save_automation_response(db, payload, response_payload)

        for step in plan.steps:
            if step.intent.operation != DocumentOperation.APPEND_WORKBOOK_ROWS:
                continue
            source = next(
                (doc for doc in reversed(documents) if str(doc.filename) in step.source_filenames),
                selected_source,
            )
            if source is None:
                continue
            try:
                await asyncio.to_thread(
                    WorkbookRowAppender().resolve,
                    cast(bytes, source.file_data),
                    AppendRowsRequest.model_validate(step.intent.parameters),
                )
            except InvalidDocumentError as error:
                return _save_automation_response(
                    db,
                    payload,
                    ChatDocumentAutomationResponse(
                        response=str(error),
                        session_id=payload.session_id,
                        status="clarification_required",
                    ),
                )

        repository.add_message(db, payload.session_id, "user", payload.instruction, commit=False)
        runtime_documents = {str(document.filename).casefold(): document for document in documents}
        current_source = selected_source if plan.steps[0].source_filenames else None
        completed_attachments = []
        completed_records = []
        context_documents: list[ExtractedDocument] = []
        context_texts: list[str] = []
        lineage = {
            int(document.id): (
                {int(document.id)}
                if document.kind == "uploaded"
                else set(document.source_document_ids)
            )
            for document in documents
        }
        context_source_ids: set[int] = set()
        context_unknown_provenance = False
        response_steps: list[ChatDocumentAutomationStepOut] = []
        fidelity_results: list[tuple[str, str, str | None]] = []
        failure_reason = None
        failed_position = None
        completion_details: list[str] = []

        def persist_extraction(source: PipelineDocument, extracted: ExtractedDocument) -> None:
            if source.document_id is not None:
                attachment = db.get(ChatDocumentAttachment, source.document_id)
                if attachment is not None:
                    repository.save_document_extraction(attachment, extracted)

        try:
            for step in plan.steps:
                if step.explicitly_named_input:
                    selected_documents = [
                        runtime_documents.get(filename.casefold())
                        for filename in step.source_filenames
                    ]
                    if any(document is None for document in selected_documents):
                        raise InvalidDocumentError(
                            "A selected document is no longer available in this chat."
                        )
                    step_sources = tuple(
                        document for document in selected_documents if document is not None
                    )
                elif current_source is not None:
                    step_sources = (current_source,)
                else:
                    step_sources = ()

                source_ids = [int(document.id) for document in step_sources]
                unknown_provenance = any(
                    document.kind == "generated" and document.provenance is None
                    for document in step_sources
                )
                used_source_ids = set().union(
                    *(lineage.get(source_id, set()) for source_id in source_ids)
                )
                if step.intent.operation == DocumentOperation.CREATE_DOCUMENT:
                    used_source_ids.update(context_source_ids)
                    unknown_provenance = unknown_provenance or context_unknown_provenance
                result = await pipeline_service.execute_agent_step(
                    step,
                    tuple(
                        PipelineDocument(
                            file_data=cast(bytes, document.file_data),
                            filename=cast(str, document.filename),
                            document_id=int(document.id),
                        )
                        for document in step_sources
                    ),
                    request_instruction=request_instruction,
                    history=history,
                    context_documents=tuple(context_documents),
                    context_texts=tuple(context_texts),
                    on_extracted=persist_extraction,
                )
                known_context_sources = {
                    document.source.filename
                    for document in context_documents
                    if document.source and document.source.filename
                }
                context_documents.extend(
                    document
                    for document in result.extracted_documents
                    if not document.source
                    or not document.source.filename
                    or document.source.filename not in known_context_sources
                )
                if result.text is not None:
                    context_texts.append(result.text)
                if result.text is not None or result.extracted_documents:
                    context_source_ids.update(used_source_ids)
                    context_unknown_provenance = context_unknown_provenance or unknown_provenance

                generated = None
                if result.document is not None:
                    extracted_output = await asyncio.to_thread(
                        service.extract_document,
                        result.document.file_data,
                        result.document.filename,
                    )
                    record, generated = repository.save_generated_document(
                        db,
                        session_id=payload.session_id,
                        bot_content=f"Created {result.document.filename}.",
                        filename=result.document.filename,
                        content_type=result.document.content_type,
                        file_data=result.document.file_data,
                        raw_text=extracted_output.text,
                        extracted=extracted_output,
                        structured_summary=result.summary,
                        is_internal=True,
                        commit=False,
                        locked_session=locked_session,
                    )
                    completed_records.append(record)
                    completed_attachments.append(generated)
                    provenance = "uploaded_source" if used_source_ids else None
                    if (
                        step.intent.operation == DocumentOperation.CREATE_DOCUMENT
                        and not used_source_ids
                        and not unknown_provenance
                    ):
                        provenance = "general_knowledge"
                    elif (
                        not used_source_ids
                        and not unknown_provenance
                        and any(
                            document.provenance == "general_knowledge" for document in step_sources
                        )
                    ):
                        provenance = "general_knowledge"
                    setattr(
                        generated,
                        "generation_metadata",
                        {
                            "provenance": provenance,
                            "source_document_ids": sorted(used_source_ids),
                            "assumptions": list(result.assumptions),
                            "instruction": request_instruction,
                            "operation": step.intent.operation.value,
                            "fidelity": result.fidelity.value,
                            "fidelity_note": result.fidelity_note,
                            "content": result.structured_content.model_dump(mode="json")
                            if result.structured_content is not None
                            else None,
                            "source_extractions": [
                                {"document_id": source_id, "data": source.extracted_data}
                                for source_id in sorted(used_source_ids)
                                if (source := db.get(ChatDocumentAttachment, source_id)) is not None
                                and source.extracted_data is not None
                            ],
                        },
                    )
                    lineage[int(generated.id)] = used_source_ids
                    runtime_documents[str(generated.filename).casefold()] = generated
                    current_source = generated

                fidelity_results.append(
                    (
                        str(generated.filename)
                        if generated is not None
                        else f"step {step.position}",
                        result.fidelity.value,
                        result.fidelity_note,
                    )
                )

                if step.intent.operation in {
                    DocumentOperation.EXTRACT_MATCHING_ROWS,
                    DocumentOperation.APPEND_WORKBOOK_ROWS,
                    DocumentOperation.SPLIT_WORKBOOK_BY_COLUMN,
                }:
                    completion_details.append(result.summary)

                response_steps.append(
                    ChatDocumentAutomationStepOut(
                        position=step.position,
                        operation=step.intent.operation.value,
                        source_document_ids=source_ids,
                        output_type=(
                            step.intent.output_type.value
                            if step.intent.output_type is not None
                            else "text_response"
                        ),
                        parameters=step.intent.parameters,
                        output_document_id=int(generated.id) if generated is not None else None,
                        filename=str(generated.filename) if generated is not None else None,
                        status="completed",
                        fidelity=result.fidelity.value,
                        fidelity_note=result.fidelity_note,
                    )
                )
        except (
            DocumentIntentError,
            UnsupportedDocumentConversionError,
            InvalidDocumentError,
            GeneratedDocumentValidationError,
            DocumentProcessingUnavailableError,
            ChatModelUnavailableError,
        ) as error:
            failure_reason = str(error)
            failed_position = len(response_steps) + 1
        except Exception:
            logger.exception(
                "chat.document_automation_failed",
                extra={"user_id": user_id, "session_id": payload.session_id},
            )
            failure_reason = "A document could not be generated, validated, or stored."
            failed_position = len(response_steps) + 1

        if failure_reason is not None:
            for step in plan.steps[len(response_steps) :]:
                response_steps.append(
                    ChatDocumentAutomationStepOut(
                        position=step.position,
                        operation=step.intent.operation.value,
                        source_document_ids=[],
                        output_type=(
                            step.intent.output_type.value
                            if step.intent.output_type is not None
                            else "text_response"
                        ),
                        parameters=step.intent.parameters,
                        status="failed" if step.position == failed_position else "not_started",
                        detail=failure_reason if step.position == failed_position else None,
                    )
                )
            status: Literal["partial", "failed"] = (
                "partial"
                if any(step.status == "completed" for step in response_steps)
                else "failed"
            )
            response = (
                f"I completed part of your request, but couldn't finish it. {failure_reason}"
                if status == "partial"
                else f"I couldn't complete the requested document changes. {failure_reason}"
            )
            response += _fidelity_message(fidelity_results)
            if context_texts:
                response = "\n\n".join([*context_texts, response])
            if completed_records:
                setattr(completed_records[-1], "is_internal", False)
                setattr(completed_records[-1], "content", response)
                final_record = completed_records[-1]
            else:
                final_record = repository.add_message(
                    db, payload.session_id, "bot", response, commit=False
                )
            latest = completed_attachments[-1] if completed_attachments else None
            return _save_automation_response(
                db,
                payload,
                ChatDocumentAutomationResponse(
                    response=response,
                    session_id=payload.session_id,
                    status=status,
                    attachments=cast(list[ChatDocumentAttachmentOut], completed_attachments),
                    latest_document_id=int(latest.id) if latest is not None else selected_id,
                    steps=response_steps,
                ),
                final_record,
            )

        filenames = [str(attachment.filename) for attachment in completed_attachments]
        response = _completion_message(filenames, "\n\n".join(context_texts) or None)
        if completion_details:
            response += " " + " ".join(completion_details)
        response += _fidelity_message(fidelity_results)
        if completed_records:
            setattr(completed_records[-1], "is_internal", False)
            setattr(completed_records[-1], "content", response)
            final_record = completed_records[-1]
        else:
            final_record = repository.add_message(
                db, payload.session_id, "bot", response, commit=False
            )
        db.flush()
        latest = completed_attachments[-1] if completed_attachments else None
        logger.info(
            "chat.document_automation_completed",
            extra={
                "user_id": user_id,
                "session_id": payload.session_id,
                "latest_document_id": int(latest.id) if latest is not None else selected_id,
                "step_count": len(plan.steps),
                "attachment_count": len(completed_attachments),
            },
        )
        return _save_automation_response(
            db,
            payload,
            ChatDocumentAutomationResponse(
                response=response,
                session_id=payload.session_id,
                status="done",
                attachments=cast(list[ChatDocumentAttachmentOut], completed_attachments),
                latest_document_id=int(latest.id) if latest is not None else selected_id,
                steps=response_steps,
            ),
            final_record,
        )


@router.get("/{document_id}/data", summary="Read the saved document data and generation history")
def document_data(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    attachment = repository.get_document_for_user(db, document_id, int(current_user["sub"]))
    if attachment is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "id": attachment.id,
        "session_id": attachment.session_id,
        "filename": attachment.filename,
        "kind": attachment.kind,
        "created_at": attachment.created_at,
        "raw_text": attachment.raw_text,
        "extracted_data": attachment.extracted_data,
        "generation_metadata": attachment.generation_metadata,
    }


@router.get("/{document_id}/preview", summary="Read a page of the stored XLSX workbook")
def preview_document(
    document_id: int,
    sheet: str | None = None,
    row_offset: int = Query(default=0, ge=0, le=1048575),
    column_offset: int = Query(default=0, ge=0, le=16383),
    row_limit: int = Query(default=100, ge=1, le=200),
    column_limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    attachment = repository.get_document_for_user(db, document_id, int(current_user["sub"]))
    if attachment is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if str(attachment.content_type) != DOCUMENT_TYPES[".xlsx"]:
        raise HTTPException(status_code=415, detail="Grid preview supports XLSX files only")
    try:
        return SpreadsheetPreviewService().preview(
            bytes(attachment.file_data),
            sheet=sheet,
            row_offset=row_offset,
            column_offset=column_offset,
            row_limit=row_limit,
            column_limit=column_limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Worksheet not found") from error


@router.get("/{document_id}/download", summary="Download a chat document")
def download_document(
    document_id: int,
    inline: bool = False,
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
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(cast(str, attachment.filename))}",
            "X-Content-Type-Options": "nosniff",
        },
    )
