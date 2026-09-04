from app.config import settings
from app.services.vision_providers.base import VisionProvider
from app.services.vision_providers.nvidia_provider import NvidiaVisionProvider
from app.services.vision_providers.ollama_provider import OllamaVisionProvider

_PROVIDERS: dict[str, type[VisionProvider]] = {
    "ollama": OllamaVisionProvider,
    "nvidia": NvidiaVisionProvider,
}


def get_vision_provider() -> VisionProvider:
    """
    Returns the vision provider selected by the LLM_PROVIDER env var
    ("ollama" or "nvidia"; defaults to "ollama" if unset or unrecognized).

    Add a new provider by: (1) implementing VisionProvider in a new module
    here, (2) registering it in _PROVIDERS above. No caller code changes.
    """
    provider_class = _PROVIDERS.get(settings.LLM_PROVIDER, OllamaVisionProvider)
    return provider_class()


__all__ = ["VisionProvider", "get_vision_provider"]
