"""Stateless invoice API: bounded evidence in, validated invoice out."""

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.rate_limit import limiter
from app.schemas.invoice import InvoiceErrorDetail, InvoiceErrorOut, InvoiceExtractionOut
from app.services.invoice_extraction.errors import InvoiceExtractionError
from app.services.invoice_extraction.pipeline import extract
from app.services.invoice_extraction.provider import InvoiceVLM, get_invoice_vlm
from app.utils.auth_dependency import get_current_user

logger = logging.getLogger(__name__)


def enabled():
    if not settings.ENABLE_INVOICE_EXTRACTION:
        raise HTTPException(404, "Invoice extraction is not enabled")


router = APIRouter(
    prefix="/documents",
    tags=["Invoice Extraction"],
    dependencies=[Depends(enabled), Depends(get_current_user)],
)


def identifier(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid4().hex


async def extraction_error_handler(request: Request, exception: Exception):
    assert isinstance(exception, InvoiceExtractionError)
    request_id = identifier(request)
    logger.warning(
        "invoice.extraction_failed",
        extra={"request_id": request_id, "code": exception.code, "status_code": exception.status},
    )
    body = InvoiceErrorOut(
        request_id=request_id,
        error=InvoiceErrorDetail(
            code=exception.code,
            message=exception.message,
            retryable=exception.retryable,
            violations=exception.violations,
        ),
    )
    return JSONResponse(
        status_code=exception.status,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/extract-invoice",
    response_model=InvoiceExtractionOut,
    responses={code: {"model": InvoiceErrorOut} for code in (413, 415, 422, 502, 504)},
)
@limiter.limit(settings.INVOICE_EXTRACTION_RATE_LIMIT)
async def extract_invoice(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    provider: InvoiceVLM = Depends(get_invoice_vlm),
):
    try:
        data = await file.read(settings.INVOICE_MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    extension = Path((file.filename or "").replace("\\", "/")).suffix.lower()
    declared = (file.content_type or "").lower().split(";", 1)[0].strip()
    result = await extract(data, extension, declared, provider)
    response.headers["Cache-Control"] = "no-store"
    return InvoiceExtractionOut(request_id=identifier(request), **result)


@router.get("/vlm/provider")
def provider_info(provider: InvoiceVLM = Depends(get_invoice_vlm)):
    return {
        "provider": provider.provider,
        "model": provider.model,
        "schema_version": "1.0.0",
        "prompt_version": "1.0.0",
        "max_pages": settings.INVOICE_MAX_PAGES,
        "max_upload_bytes": settings.INVOICE_MAX_UPLOAD_BYTES,
    }


@router.get("/vlm/health")
async def provider_health(response: Response, provider: InvoiceVLM = Depends(get_invoice_vlm)):
    try:
        async with asyncio.timeout(min(5, settings.INVOICE_VLM_TIMEOUT_SECONDS)):
            healthy = await provider.health()
    except Exception:
        healthy = False
    if not healthy:
        response.status_code = 503
    response.headers["Cache-Control"] = "no-store"
    return {
        "healthy": healthy,
        "provider": provider.provider,
        "model": provider.model,
        "schema_version": "1.0.0",
    }
