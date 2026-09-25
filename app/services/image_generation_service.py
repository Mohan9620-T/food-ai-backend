"""NVIDIA FLUX image generation. Provider refusals are never retried or bypassed."""

import base64
import binascii
import json
import re
from io import BytesIO

import httpx
from PIL import Image, UnidentifiedImageError

from app.config import settings

IMAGE_ENDPOINT = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"
IMAGE_MODEL = "black-forest-labs/flux.2-klein-4b"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class ImageGenerationError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def image_capability_message() -> str:
    if not settings.ENABLE_IMAGE_GENERATION or not settings.NVIDIA_API_KEY:
        return "Image generation is not configured yet. An administrator needs to enable the image provider."
    return (
        "I can create a new image from a written description. Describe the subject, style, "
        "and setting, or select Create image and send your prompt. The image will appear "
        "here with a download button. Editing an uploaded image is not supported yet."
    )


def image_request_help(message: str) -> str | None:
    """Avoid spending an image request on a capability question or an empty command."""
    normalized = re.sub(r"[^a-z\s]", "", message.lower()).strip()
    if re.fullmatch(
        r"(?:(?:can|could|do|will) you |you can )?(?:generate|create|make|draw)"
        r"(?: or (?:generate|create|make|draw))? (?:an? |the |new )?"
        r"(?:image|images|picture|pictures|photo|photos)(?: please)?",
        normalized,
    ):
        if normalized.startswith(("can ", "could ", "do ", "will ", "you can ")):
            return image_capability_message()
        return "Describe the image you want: its subject, setting, and style."
    return None


class ImageGenerationService:
    async def generate(self, prompt: str, aspect_ratio: str = "auto") -> bytes:
        if not settings.ENABLE_IMAGE_GENERATION or not settings.NVIDIA_API_KEY:
            raise ImageGenerationError(image_capability_message())
        if aspect_ratio == "auto":
            aspect_ratio = (
                "9:16"
                if re.search(r"\b9\s*:\s*16\b|\bportrait\b", prompt, re.I)
                else (
                    "16:9"
                    if re.search(r"\b16\s*:\s*9\b|\blandscape\b|\bwidescreen\b", prompt, re.I)
                    else "1:1"
                )
            )
        width, height = {"1:1": (1024, 1024), "9:16": (768, 1344), "16:9": (1344, 768)}[
            aspect_ratio
        ]
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.NVIDIA_IMAGE_TIMEOUT_SECONDS, connect=10)
            ) as client:
                async with client.stream(
                    "POST",
                    IMAGE_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
                        "Accept": "application/json",
                    },
                    json={
                        "prompt": prompt,
                        "width": width,
                        "height": height,
                        "steps": 4,
                        "seed": 0,
                    },
                ) as response:
                    if response.status_code == 429:
                        raise ImageGenerationError(
                            "The image provider is busy or its quota is exhausted. Please try again later.",
                            429,
                        )
                    if response.status_code in {401, 403}:
                        raise ImageGenerationError(
                            "The image provider could not authorize this request. Please check the NVIDIA account's image access."
                        )
                    if response.status_code in {400, 422}:
                        raise ImageGenerationError(
                            "The image provider declined this prompt or its parameters. Try a different description.",
                            422,
                        )
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ImageGenerationError(
                                "The generated image exceeded the supported size. Try a simpler prompt."
                            )
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
                raise ValueError("Missing image artifacts")
            artifact = payload["artifacts"][0]
            if not isinstance(artifact, dict):
                raise ValueError("Invalid image artifact")
            if str(
                artifact.get("finishReason", artifact.get("finish_reason", "SUCCESS"))
            ).upper() not in {"SUCCESS", "STOP"}:
                raise ImageGenerationError(
                    "The image provider could not return this image. Try a different description.",
                    422,
                )
            encoded = artifact["base64"]
            data = base64.b64decode(encoded, validate=True)
            with Image.open(BytesIO(data)) as image:
                if image.width * image.height > 4_000_000:
                    raise ValueError("Unexpected image dimensions")
                image.load()
                output = BytesIO()
                image.convert("RGB").save(output, format="PNG")
            return output.getvalue()
        except ImageGenerationError:
            raise
        except httpx.TimeoutException as error:
            raise ImageGenerationError(
                "Image generation took too long. Your prompt was not shortened; please try again.",
                504,
            ) from error
        except httpx.HTTPError as error:
            raise ImageGenerationError(
                "The image provider is temporarily unavailable. Please try again later."
            ) from error
        except (
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            binascii.Error,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            OSError,
        ) as error:
            raise ImageGenerationError(
                "The provider did not return a valid image. Please try again."
            ) from error
