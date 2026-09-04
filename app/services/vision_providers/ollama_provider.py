import logging

import requests
from pydantic import ValidationError

from app.config import settings
from app.schemas.vision_result import VisionResult
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.vision_providers.base import VisionProvider

logger = logging.getLogger(__name__)


class OllamaVisionProvider(VisionProvider):
    """Local, private, free-forever inference via a self-hosted Ollama server."""

    def infer(self, system_prompt: str, user_prompt: str, encoded_image: str) -> VisionResult:
        try:
            response = requests.post(
                settings.OLLAMA_URL,
                json={
                    "model": settings.OLLAMA_CHAT_VISION_MODEL,
                    "stream": False,
                    "think": False,
                    "keep_alive": settings.OLLAMA_KEEP_ALIVE,
                    "format": VisionResult.model_json_schema(),
                    "options": {
                        "temperature": 0,
                        "num_ctx": 8192,
                        "num_predict": 1024,
                    },
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": user_prompt,
                            "images": [encoded_image],
                        },
                    ],
                },
                timeout=settings.OLLAMA_CHAT_VISION_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.Timeout as error:
            logger.warning("chat.vision_model_timeout", extra={"provider": "ollama"})
            raise VisionModelUnavailableError(
                "Chat vision analysis timed out after "
                f"{settings.OLLAMA_CHAT_VISION_TIMEOUT_SECONDS} seconds. "
                "The model may still be finishing another request; please try again."
            ) from error
        except requests.RequestException as error:
            logger.warning("chat.vision_model_unavailable", extra={"provider": "ollama"})
            raise VisionModelUnavailableError(
                "Chat vision model unavailable: configured model "
                f"'{settings.OLLAMA_CHAT_VISION_MODEL}'. "
                "Confirm Ollama is running and the model is installed."
            ) from error

        try:
            message = response.json()["message"]
            content = message.get("content") or message.get("thinking")
            return VisionResult.model_validate_json(content)
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise ValueError("Ollama vision response was not valid JSON") from error
