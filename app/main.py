import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.chat import router as chat_router
from app.api.diet_plans import router as diet_plans_router
from app.api.meals import router as meals_router
from app.api.profile import router as profile_router
from app.api.user_api import router as user_router
from app.config import settings
from app.database.database import engine
from app.database.migration_check import warn_if_migrations_pending
from app.logging_config import configure_logging
from app.rate_limit import limiter

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
    yield


app = FastAPI(
    title="Food AI Backend",
    version="1.0.0",
    docs_url=None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(user_router)
app.include_router(chat_router)
app.include_router(meals_router)
app.include_router(profile_router)
app.include_router(diet_plans_router)


@app.get("/")
def root():
    return {"message": "Food AI Backend Running"}


@app.get("/health")
def health():
    return {"status": "Healthy"}


@app.get("/docs", include_in_schema=False)
def swagger_ui():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_js_url="/static/swagger/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger/swagger-ui.css",
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exception: Exception):
    logger.exception(
        "unhandled_request_exception",
        extra={"method": request.method, "path": request.url.path},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
