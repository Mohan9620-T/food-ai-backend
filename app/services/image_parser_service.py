import base64
import logging
from typing import Any

import requests
from pydantic import ValidationError

from app.config import settings
from app.schemas.vision_result import VisionResult
from app.services.food_name_normalizer import normalize_food_name
from app.services.nutrition_parser_service import ParsedFoodItem
from app.services.vision_image_preprocessor import prepare_vision_image
from app.services.vision_runtime import vision_inference_slot

logger = logging.getLogger(__name__)


class ImageParseError(ValueError):
    pass


class VisionModelUnavailableError(RuntimeError):
    pass


class ImageParserService:
    SYSTEM_PROMPT = """Identify only visible food and drink items in this image.
Return a JSON object matching the supplied schema. Set image_type to "food" only when food or
drink is visible. For each visible food item provide its name, confidence, and concrete visual
evidence. Put ambiguous possibilities in uncertain_items rather than presenting them as facts.
Group repeated foods of the same kind into one item. Keep names and visual evidence concise.
Do not include calories, nutrients, macros, health claims, commentary, or Markdown.
Do not identify plates, cutlery, packaging, or non-food objects as food.
If no food or drink can be identified, return an empty items array.
"""

    def parse(self, image_bytes: bytes) -> list[ParsedFoodItem]:
        inference_image = prepare_vision_image(image_bytes)
        encoded_image = base64.b64encode(inference_image).decode("ascii")
        with vision_inference_slot():
            return self._parse_with_slot(encoded_image)

    def _parse_with_slot(self, encoded_image: str) -> list[ParsedFoodItem]:
        for attempt in range(2):
            messages = [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Identify the food items in this image.",
                    "images": [encoded_image],
                },
            ]
            if attempt:
                messages.insert(
                    1,
                    {
                        "role": "system",
                        "content": "The previous output was invalid. Return only an object matching the supplied JSON schema.",
                    },
                )
            try:
                request_body: dict[str, Any] = {
                    "model": settings.OLLAMA_VISION_MODEL,
                    "stream": False,
                    "think": False,
                    "keep_alive": settings.OLLAMA_KEEP_ALIVE,
                    "format": VisionResult.model_json_schema(),
                    "options": {
                        "temperature": 0,
                        "num_ctx": 8192,
                        "num_predict": 1024,
                    },
                    "messages": messages,
                }
                response = requests.post(
                    settings.OLLAMA_URL,
                    json=request_body,
                    timeout=settings.OLLAMA_VISION_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            except requests.Timeout as error:
                logger.warning("meal.vision_model_timeout", extra={"attempt": attempt + 1})
                raise VisionModelUnavailableError(
                    "Vision analysis timed out after "
                    f"{settings.OLLAMA_VISION_TIMEOUT_SECONDS} seconds. "
                    "The model may still be finishing another request; please try again."
                ) from error
            except requests.RequestException as error:
                logger.warning("meal.vision_model_unavailable", extra={"attempt": attempt + 1})
                raise VisionModelUnavailableError(
                    "Vision model unavailable. Confirm Ollama is running and the configured "
                    "vision model is installed."
                ) from error

            try:
                message = response.json()["message"]
                content = message.get("content") or message.get("thinking")
                result = VisionResult.model_validate_json(content)
                if result.image_type != "food" or not result.items:
                    raise ValueError("No food identified")
                return [
                    ParsedFoodItem(
                        food_name=normalize_food_name(item.name),
                        quantity=1,
                        unit="serving",
                    )
                    for item in result.items
                ]
            except (KeyError, TypeError, ValueError, ValidationError):
                logger.warning("meal.image_parse_attempt_failed", extra={"attempt": attempt + 1})

        raise ImageParseError(
            "Could not identify food in image. Try a clearer photo with visible portions."
        )
