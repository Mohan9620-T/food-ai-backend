import logging
from time import monotonic
from typing import TypedDict

from app.schemas.vision_result import VisionResult
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.vision_providers.base import VisionProvider
from app.services.vision_providers.nvidia_provider import NvidiaVisionProvider
from app.services.vision_providers.ollama_provider import OllamaVisionProvider

logger = logging.getLogger(__name__)


class _InferenceOptions(TypedDict, total=False):
    max_tokens: int
    timeout_seconds: float


class FailoverVisionProvider(VisionProvider):
    """Try NVIDIA once, then retry the same logical request once with Ollama."""

    def __init__(
        self,
        nvidia: VisionProvider | None = None,
        ollama: VisionProvider | None = None,
    ) -> None:
        self.nvidia = nvidia or NvidiaVisionProvider()
        self.ollama = ollama or OllamaVisionProvider()

    def infer(
        self,
        system_prompt: str,
        user_prompt: str,
        encoded_image: str,
        *,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> VisionResult:
        started = monotonic()
        options: _InferenceOptions = {}
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if timeout_seconds is not None:
            options["timeout_seconds"] = timeout_seconds
        try:
            return self.nvidia.infer(system_prompt, user_prompt, encoded_image, **options)
        except (VisionModelUnavailableError, ValueError):
            logger.warning("chat.vision_nvidia_fallback_to_ollama")
            if timeout_seconds is not None:
                remaining = timeout_seconds - (monotonic() - started)
                if remaining <= 0:
                    raise VisionModelUnavailableError("Image extraction timed out. Please retry.")
                options["timeout_seconds"] = remaining
            return self.ollama.infer(system_prompt, user_prompt, encoded_image, **options)
