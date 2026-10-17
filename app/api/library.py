"""File library and feature discovery; ownership is enforced before bytes or links."""

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.repositories.library_repository import LibraryRepository
from app.services.spreadsheet.spreadsheet_preview_service import SpreadsheetPreviewService
from app.storage import backend
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/library", tags=["Library"])
repository = LibraryRepository()
Kind = Literal["image", "document", "generated"]


@router.get("/capabilities")
def capabilities(current_user: dict = Depends(get_current_user)):
    return {
        "library": settings.ENABLE_LIBRARY,
        "api_keys": settings.ENABLE_API_KEYS,
        "code_assistant": settings.ENABLE_CODE_ASSISTANT,
        "workspaces": settings.ENABLE_WORKSPACES,
        "background_documents": settings.ENABLE_BACKGROUND_DOCUMENT_PROCESSING,
    }


def enabled():
    if not settings.ENABLE_LIBRARY:
        raise HTTPException(404, "Library is not enabled")


@router.get("")
def list_library(
    kind: Kind | None = None,
    q: str = Query("", max_length=200),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    enabled()
    return repository.list(db, int(current_user["sub"]), kind, q, limit, offset)


def owned(db, user, kind, item_id, *, read_data=True):
    enabled()
    result = repository.get(db, int(user["sub"]), kind, item_id, read_data=read_data)
    if result is None:
        raise HTTPException(404, "File not found")
    return result


@router.get("/{kind}/{item_id}/preview")
def preview(
    kind: Kind,
    item_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row, data, content_type, filename = owned(db, current_user, kind, item_id)
    if filename.lower().endswith(".xlsx"):
        return SpreadsheetPreviewService().preview(data, row_limit=100, column_limit=50)
    if content_type.startswith("image/") or content_type == "application/pdf":
        return Response(
            data,
            media_type=content_type,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
        )
    return {"text": str(getattr(row, "raw_text", "") or "")[:50000]}


@router.get("/{kind}/{item_id}/download")
def download(
    kind: Kind,
    item_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row, data, content_type, filename = owned(db, current_user, kind, item_id, read_data=False)
    if row.storage_key:
        name, key = row.storage_key.split(":", 1)
        store = backend(name)
        url = store.signed_url(key, settings.STORAGE_SIGNED_URL_TTL_SECONDS) if store else None
        if url:
            return RedirectResponse(url, status_code=307)
    data = bytes(row.file_data) if hasattr(row, "file_data") else bytes(row.image_data)
    return Response(
        data,
        media_type=content_type,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
