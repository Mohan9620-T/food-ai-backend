from abc import ABC, abstractmethod

from app.schemas.vision_result import VisionResult


class VisionProvider(ABC):
    """
    A vision provider turns (system_prompt, user_prompt, base64 image) into a
    validated VisionResult. Swapping LLM_PROVIDER in .env swaps the concrete
    implementation used at runtime — callers (ChatVisionService) never talk to
    Ollama or NVIDIA directly, only to this interface.

    Implementations must raise VisionModelUnavailableError (imported from
    app.services.image_parser_service) on network/timeout/HTTP failures, so
    the caller's existing error handling keeps working unchanged.
    """

    @abstractmethod
    def infer(self, system_prompt: str, user_prompt: str, encoded_image: str) -> VisionResult:
        """Run vision inference and return a validated VisionResult.

        Raises:
            VisionModelUnavailableError: on timeout or the provider being
                unreachable/unconfigured.
            ValueError: if the provider responded but the content could not
                be parsed into VisionResult (caller decides how to degrade).
        """
        raise NotImplementedError
