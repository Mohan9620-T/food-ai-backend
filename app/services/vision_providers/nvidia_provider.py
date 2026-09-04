import json
import logging

import requests
from pydantic import ValidationError

from app.config import settings
from app.schemas.vision_result import VisionResult
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.vision_providers.base import VisionProvider

logger = logging.getLogger(__name__)


class NvidiaConfigurationError(VisionModelUnavailableError):
    """Raised when LLM_PROVIDER=nvidia but NVIDIA_API_KEY is not set."""


class NvidiaVisionProvider(VisionProvider):
    """
    Hosted inference via NVIDIA's free NIM API catalog (build.nvidia.com).
    OpenAI-compatible /chat/completions endpoint. Unlike OllamaVisionProvider,
    this sends the image to NVIDIA's cloud over the internet and is subject to
    their free-tier rate limits — it is not a private/local alternative, it's
    a different trade-off (no local RAM/GPU cost, but no longer fully local).
    """

    def infer(self, system_prompt: str, user_prompt: str, encoded_image: str) -> VisionResult:
        if not settings.NVIDIA_API_KEY:
            raise NvidiaConfigurationError(
                "LLM_PROVIDER is set to 'nvidia' but NVIDIA_API_KEY is not "
                "configured. Get a free key at https://build.nvidia.com/settings "
                "and set NVIDIA_API_KEY in your .env file."
            )

        schema_instructions = (
            "\n\nRespond with ONLY a single JSON object (no markdown, no "
            "commentary) matching exactly this schema:\n"
            f"{json.dumps(VisionResult.model_json_schema())}"
        )

        try:
            response = requests.post(
                f"{settings.NVIDIA_API_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {settings.NVIDIA_API_KEY}"},
                json={
                    "model": settings.NVIDIA_CHAT_VISION_MODEL,
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": 1024,
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt + schema_instructions,
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"},
                                },
                            ],
                        },
                    ],
                },
                timeout=settings.NVIDIA_VISION_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.Timeout as error:
            logger.warning("chat.vision_model_timeout", extra={"provider": "nvidia"})
            raise VisionModelUnavailableError(
                "NVIDIA vision analysis timed out after "
                f"{settings.NVIDIA_VISION_TIMEOUT_SECONDS} seconds. Please try "
                "again."
            ) from error
        except requests.RequestException as error:
            logger.warning("chat.vision_model_unavailable", extra={"provider": "nvidia"})
            raise VisionModelUnavailableError(
                "NVIDIA vision API unavailable: configured model "
                f"'{settings.NVIDIA_CHAT_VISION_MODEL}'. Confirm NVIDIA_API_KEY "
                "is valid and you have remaining free-tier credits."
            ) from error

        try:
            content = response.json()["choices"][0]["message"]["content"]
            content = _strip_markdown_fence(content)
            return VisionResult.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as error:
            raise ValueError("NVIDIA vision response was not valid JSON") from error


def _strip_markdown_fence(content: str) -> str:
    """Some hosted models wrap JSON in ```json fences despite instructions not to."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()
