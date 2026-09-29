"""Provider fallback policy shared by chat and image analysis."""

from app.config import settings


def ollama_fallback_enabled() -> bool:
    # A hosted deployment has no local model server unless explicitly provisioned.
    configured = settings.ENABLE_OLLAMA_FALLBACK
    return configured if configured is not None else settings.APP_ENVIRONMENT != "production"
