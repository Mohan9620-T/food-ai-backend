from app.config import settings
from app.services.vision_providers.base import VisionProvider
from app.services.vision_providers.failover_provider import FailoverVisionProvider
from app.services.vision_providers.ollama_provider import OllamaVisionProvider


def get_vision_provider() -> VisionProvider:
    """Return local-only development routing or NVIDIA-to-Ollama failover."""
    if settings.APP_ENVIRONMENT != "production" and settings.LLM_PROVIDER == "ollama":
        return OllamaVisionProvider()
    return FailoverVisionProvider()


__all__ = ["VisionProvider", "get_vision_provider"]
