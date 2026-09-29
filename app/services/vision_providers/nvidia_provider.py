import json
import logging
import time

import requests
from pydantic import ValidationError

from app.config import settings
from app.schemas.vision_result import VisionResult
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.vision_providers.base import VisionProvider

logger = logging.getLogger(__name__)
_HTTP_SESSION = requests.Session()


def _post_with_retry(url: str, *, headers: dict, json: dict, timeout: tuple) -> requests.Response:
    """Retry transient hosted-capacity errors within the original read budget."""
    started = time.monotonic()
    for attempt in range(3):
        remaining = timeout[1] if attempt == 0 else timeout[1] - (time.monotonic() - started)
        if remaining <= 0:
            raise requests.Timeout("Vision request deadline exceeded")
        response = _HTTP_SESSION.post(
            url, headers=headers, json=json, timeout=(min(timeout[0], remaining), remaining)
        )
        if response.status_code not in {429, 502, 503, 504} or attempt == 2:
            return response
        delay = float(2**attempt)
        try:
            delay = max(delay, min(5, float(response.headers.get("Retry-After", "0"))))
        except (TypeError, ValueError):
            pass
        if time.monotonic() - started + delay >= timeout[1]:
            return response
        response.close()
        time.sleep(delay)
    raise requests.Timeout("Vision request deadline exceeded")


class NvidiaConfigurationError(VisionModelUnavailableError):
    """Raised when NVIDIA is selected but its API key is not set."""


class VisionRequestDeclinedError(VisionModelUnavailableError):
    """A provider refusal is final, not a reason to switch providers."""


class NvidiaVisionProvider(VisionProvider):
    """
    Hosted inference via NVIDIA's free NIM API catalog (build.nvidia.com).
    OpenAI-compatible /chat/completions endpoint. Unlike OllamaVisionProvider,
    this sends the image to NVIDIA's cloud over the internet and is subject to
    their free-tier rate limits — it is not a private/local alternative, it's
    a different trade-off (no local RAM/GPU cost, but no longer fully local).
    """

    def infer(
        self,
        system_prompt: str,
        user_prompt: str,
        encoded_image: str,
        *,
        additional_images: tuple[str, ...] = (),
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> VisionResult:
        if not settings.NVIDIA_API_KEY:
            raise NvidiaConfigurationError("NVIDIA_API_KEY is not configured.")

        schema_instructions = (
            "\n\nRespond with ONLY a single JSON object (no markdown, no "
            "commentary) matching exactly this schema:\n"
            f"{json.dumps(VisionResult.model_json_schema())}"
        )

        budget = (
            min(timeout_seconds, settings.NVIDIA_VISION_TIMEOUT_SECONDS)
            if timeout_seconds is not None
            else settings.NVIDIA_VISION_TIMEOUT_SECONDS
        )
        started = time.monotonic()
        token_budget = max_tokens or settings.NVIDIA_VISION_MAX_TOKENS
        for attempt in range(2):
            remaining = budget if attempt == 0 else budget - (time.monotonic() - started)
            if remaining <= 0:
                raise VisionModelUnavailableError(
                    "Image analysis timed out. Please retry your message."
                )
            try:
                return self._infer_once(
                    system_prompt + schema_instructions,
                    user_prompt,
                    (encoded_image, *additional_images),
                    token_budget,
                    remaining,
                )
            except VisionRequestDeclinedError:
                raise
            except ValueError:
                logger.warning(
                    "chat.vision_response_invalid",
                    extra={"provider": "nvidia", "attempt": attempt + 1},
                )
                if attempt:
                    raise
                # Retry the same evidence before showing anything, with enough room
                # to finish JSON. Never ask another provider to bypass a refusal.
                token_budget = min(8192, max(4096, token_budget * 2))
        raise ValueError("NVIDIA vision response was not valid JSON")

    def _infer_once(
        self,
        system_prompt: str,
        user_prompt: str,
        images: tuple[str, ...],
        max_tokens: int,
        timeout_seconds: float,
    ) -> VisionResult:
        try:
            response = _post_with_retry(
                f"{settings.NVIDIA_API_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.NVIDIA_CHAT_VISION_MODEL,
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    **(
                        {"chat_template_kwargs": {"enable_thinking": False}}
                        if "nemotron-3-nano-omni" in settings.NVIDIA_CHAT_VISION_MODEL
                        else {}
                    ),
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_prompt},
                                *[
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                                    }
                                    for encoded in images
                                ],
                            ],
                        },
                    ],
                },
                timeout=(
                    settings.NVIDIA_VISION_CONNECT_TIMEOUT_SECONDS,
                    timeout_seconds,
                ),
            )
            response.raise_for_status()
        except requests.Timeout as error:
            logger.warning("chat.vision_model_timeout", extra={"provider": "nvidia"})
            raise VisionModelUnavailableError(
                "NVIDIA vision analysis timed out after "
                f"{settings.NVIDIA_VISION_TIMEOUT_SECONDS:g} seconds. Please try "
                "again."
            ) from error
        except requests.RequestException as error:
            logger.warning(
                "chat.vision_model_unavailable",
                extra={
                    "provider": "nvidia",
                    "error_type": type(error).__name__,
                    "status_code": error.response.status_code
                    if error.response is not None
                    else None,
                },
            )
            raise VisionModelUnavailableError(
                "NVIDIA vision API unavailable: configured model "
                f"'{settings.NVIDIA_CHAT_VISION_MODEL}'. Confirm NVIDIA_API_KEY "
                "is valid and you have remaining free-tier credits."
            ) from error

        try:
            choice = response.json()["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError("Missing vision choice")
            message = choice.get("message")
            if choice.get("finish_reason") == "content_filter" or (
                isinstance(message, dict) and message.get("refusal")
            ):
                raise VisionRequestDeclinedError(
                    "The image service declined this request. Please try a different request."
                )
            if not isinstance(message, dict):
                raise ValueError("Missing vision message")
            if choice.get("finish_reason") == "length":
                raise ValueError("Incomplete vision response")
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Missing vision response")
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
