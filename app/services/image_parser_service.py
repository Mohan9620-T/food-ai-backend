import base64
import json
import logging

import requests
from pydantic import TypeAdapter, ValidationError

from app.config import settings
from app.services.nutrition_parser_service import ParsedFoodItem
from app.services.vision_runtime import vision_inference_slot

logger = logging.getLogger(__name__)


class ImageParseError(ValueError):
    pass


class VisionModelUnavailableError(RuntimeError):
    pass


class ImageParserService:
    SYSTEM_PROMPT = """Identify only visible food and drink items in this image.
Return JSON only: a JSON array of objects with exactly these keys:
food_name, quantity, unit.
Do not include calories, nutrients, macros, health claims, estimates, commentary, or Markdown.
Use a numeric positive quantity. Report portions only when visually identifiable.
If a quantity is not distinguishable, use quantity 1 and unit "serving".
Do not identify plates, cutlery, packaging, or non-food objects as food.
If no food or drink can be identified, return an empty JSON array.
"""
    _adapter = TypeAdapter(list[ParsedFoodItem])

    def parse(self, image_bytes: bytes) -> list[ParsedFoodItem]:
        encoded_image = base64.b64encode(image_bytes).decode("ascii")
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
                messages.insert(1, {
                    "role": "system",
                    "content": "The previous output was invalid. Return only the required JSON array.",
                })
            try:
                response = requests.post(
                    settings.OLLAMA_URL,
                    json={
                        "model": settings.OLLAMA_VISION_MODEL,
                        "stream": False,
                        "keep_alive": settings.OLLAMA_KEEP_ALIVE,
                        "format": "json",
                        "options": {"temperature": 0},
                        "messages": messages,
                    },
                    timeout=settings.OLLAMA_VISION_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            except requests.RequestException as error:
                logger.warning("meal.vision_model_unavailable", extra={"attempt": attempt + 1})
                raise VisionModelUnavailableError(
                    "Vision model unavailable. Pull and start the configured Ollama vision model."
                ) from error

            try:
                content = response.json()["message"]["content"]
                parsed = self._adapter.validate_python(json.loads(content))
                if not parsed:
                    raise ValueError("No food identified")
                return parsed
            except (KeyError, TypeError, ValueError, ValidationError):
                logger.warning("meal.image_parse_attempt_failed", extra={"attempt": attempt + 1})

        raise ImageParseError(
            "Could not identify food in image. Try a clearer photo with visible portions."
        )
