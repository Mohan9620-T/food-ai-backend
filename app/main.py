import logging
from contextlib import asynccontextmanager
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.chat import router as chat_router
from app.api.chat_documents import router as chat_documents_router
from app.api.chat_images import router as chat_images_router
from app.api.chat_suggestions import router as chat_suggestions_router
from app.api.code_assistant import router as code_assistant_router
from app.api.diet_plans import router as diet_plans_router
from app.api.invoice_extraction import extraction_error_handler
from app.api.invoice_extraction import router as invoice_extraction_router
from app.api.library import router as library_router
from app.api.meals import router as meals_router
from app.api.nvidia_chat import router as nvidia_chat_router
from app.api.profile import router as profile_router
from app.api.user_api import router as user_router
from app.api.workspaces import router as workspaces_router
from app.config import settings
from app.database.chroma_client import get_chroma_client
from app.database.database import engine
from app.database.migration_check import warn_if_migrations_pending
from app.database.redis_client import get_redis_client
from app.logging_config import configure_logging
from app.rate_limit import limiter
from app.services.invoice_extraction.errors import InvoiceExtractionError
from app.services.observability import RequestIdMiddleware, setup_tracing

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    if not settings.USDA_API_KEY:
        logger.warning("usda_api_key_missing")
    if not settings.OLLAMA_CHAT_VISION_MODEL:
        logger.warning(
            "ollama_chat_vision_model_missing",
            extra={
                "setup": "Set OLLAMA_CHAT_VISION_MODEL and pull it with Ollama before using general image chat."
            },
        )
    if settings.MIGRATION_CHECK_ENABLED:
        warn_if_migrations_pending(engine)
    from app.services.document.processing import DocumentWorker

    worker = DocumentWorker()
    await worker.start()
    try:
        yield
    finally:
        await worker.stop()
        from app.services.llm_gateway import gateway

        await gateway.close()


app = FastAPI(
    title="Food AI Backend",
    version="1.0.0",
    docs_url=None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, cast(Any, _rate_limit_exceeded_handler))
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(user_router)
app.include_router(accounts_router)
app.include_router(code_assistant_router)
app.include_router(admin_router)
app.include_router(chat_router)
app.include_router(library_router)
app.include_router(invoice_extraction_router)
app.add_exception_handler(InvoiceExtractionError, extraction_error_handler)
app.include_router(workspaces_router)
app.include_router(chat_suggestions_router)
app.include_router(chat_documents_router)
app.include_router(chat_images_router)
app.include_router(meals_router)
app.include_router(nvidia_chat_router)
app.include_router(profile_router)
app.include_router(diet_plans_router)

Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(
    app,
    endpoint="/metrics",
    include_in_schema=False,
)


@app.get("/")
def root():
    return {"message": "Food AI Backend Running"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "Healthy"}


@app.get("/health/live", summary="Check application liveness")
def liveness() -> dict[str, str]:
    """Confirm that the API process is running without checking dependencies."""
    return {"status": "Healthy"}


def _ollama_health_url() -> str:
    parts = urlsplit(settings.OLLAMA_URL)
    return urlunsplit((parts.scheme, parts.netloc, "/api/tags", "", ""))


@app.get(
    "/health/ready",
    summary="Check application readiness",
    responses={503: {"description": "PostgreSQL or Ollama is unavailable."}},
)
def readiness() -> JSONResponse:
    """Check whether PostgreSQL (and, unless disabled, Ollama) can accept requests."""
    checks = {"database": "up", "ollama": "up"}

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        checks["database"] = "down"
        logger.warning("readiness_dependency_unavailable", extra={"dependency": "database"})

    try:
        ollama_response = httpx.get(_ollama_health_url(), timeout=2.0)
        ollama_response.raise_for_status()
    except (httpx.HTTPError, OSError):
        checks["ollama"] = "down"
        logger.warning("readiness_dependency_unavailable", extra={"dependency": "ollama"})

    # Ollama's status is always reported for visibility, but only gates
    # readiness when it's actually part of this deployment's topology.
    gating_checks = dict(checks)
    if not settings.OLLAMA_REQUIRED_FOR_READINESS:
        gating_checks.pop("ollama", None)

    ready = all(value == "up" for value in gating_checks.values())

    # Redis is an optional cache: report its health for visibility, but never let
    # it being down or disabled affect overall readiness (chat degrades to Postgres).
    if settings.ENABLE_REDIS_CACHE:
        checks["redis"] = "up"
        try:
            client = get_redis_client()
            if client is None or not client.ping():
                raise RuntimeError("redis unavailable")
        except Exception:
            checks["redis"] = "down"
            logger.warning("readiness_dependency_unavailable", extra={"dependency": "redis"})

    # Chroma is an optional semantic-retrieval index: report its health for
    # visibility, but never let it being down or disabled affect overall
    # readiness (chat degrades to deterministic raw_text document context).
    if settings.ENABLE_SEMANTIC_RAG:
        checks["chroma"] = "up"
        try:
            chroma = get_chroma_client()
            if chroma is None:
                raise RuntimeError("chroma unavailable")
            chroma.heartbeat()
        except Exception:
            checks["chroma"] = "down"
            logger.warning("readiness_dependency_unavailable", extra={"dependency": "chroma"})
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "Ready" if ready else "Not Ready", "checks": checks},
    )


@app.get("/docs", include_in_schema=False)
def swagger_ui():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} - Swagger UI",
        swagger_js_url="/static/swagger/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger/swagger-ui.css",
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exception: Exception):
    logger.exception(
        "unhandled_request_exception",
        extra={
            "method": request.method,
            "path": request.url.path,
            "request_id": getattr(request.state, "request_id", ""),
        },
    )
    # Request IDs survive the outer server-error handler too.
    identifier = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            **({"request_id": identifier} if identifier else {}),
        },
        headers={"X-Request-ID": identifier} if identifier else {},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=settings.local_development_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exception: RequestValidationError):
    if request.url.path == "/documents/extract-invoice" and settings.ENABLE_INVOICE_EXTRACTION:
        return await extraction_error_handler(
            request,
            InvoiceExtractionError(
                422, "invalid_upload", "Provide one PDF or image in the multipart file field."
            ),
        )
    if request.url.path == "/users/oauth-login":
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
                    for error in exception.errors()
                ]
            },
        )
    return await request_validation_exception_handler(request, exception)


app.add_middleware(RequestIdMiddleware)
setup_tracing(app, engine)
