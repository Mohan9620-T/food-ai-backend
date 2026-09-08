import logging

from app.schemas.vision_result import VisionResult
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.vision_providers.base import VisionProvider
from app.services.vision_providers.nvidia_provider import NvidiaVisionProvider
from app.services.vision_providers.ollama_provider import OllamaVisionProvider

logger = logging.getLogger(__name__)


class FailoverVisionProvider(VisionProvider):
    """Try NVIDIA once, then retry the same logical request once with Ollama."""

    def __init__(
        self,
        nvidia: VisionProvider | None = None,
        ollama: VisionProvider | None = None,
    ) -> None:
        self.nvidia = nvidia or NvidiaVisionProvider()
        self.ollama = ollama or OllamaVisionProvider()

    def infer(self, system_prompt: str, user_prompt: str, encoded_image: str) -> VisionResult:
        try:
            return self.nvidia.infer(system_prompt, user_prompt, encoded_image)
        except (VisionModelUnavailableError, ValueError):
            logger.warning("chat.vision_nvidia_fallback_to_ollama")
            return self.ollama.infer(system_prompt, user_prompt, encoded_image)
