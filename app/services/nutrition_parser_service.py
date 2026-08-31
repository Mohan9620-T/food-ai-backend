import json
import logging

import requests
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)


class ParsedFoodItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    food_name: str = Field(min_length=1, max_length=255)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=50)


class NutritionParseError(ValueError):
    pass


class NutritionParserService:
    SYSTEM_PROMPT = """You parse meal descriptions into structured food items.
Return JSON only: a JSON array of objects with exactly these keys:
food_name, quantity, unit.
Do not include calories, nutrients, macros, estimates, commentary, or Markdown.
Use a numeric positive quantity. Keep preparation details in food_name when relevant.
Choose a concrete unit from the user's text; do not invent an amount.
If an amount is implied as one item, use quantity 1 and the item as the unit.
"""
    _adapter = TypeAdapter(list[ParsedFoodItem])

    def parse(self, description: str) -> list[ParsedFoodItem]:
        for attempt in range(2):
            body = {
                "model": settings.OLLAMA_MODEL,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": description},
                ],
            }
            if attempt:
                body["messages"].insert(1, {
                    "role": "system",
                    "content": "The previous output was invalid. Return only the required JSON array.",
                })
            try:
                response = requests.post(
                    settings.OLLAMA_URL,
                    json=body,
                    timeout=settings.OLLAMA_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                content = response.json()["message"]["content"]
                parsed = self._adapter.validate_python(json.loads(content))
                if not parsed:
                    raise ValueError("No food items")
                return parsed
            except (requests.RequestException, KeyError, TypeError, ValueError, ValidationError):
                logger.warning("meal.parse_attempt_failed", extra={"attempt": attempt + 1})

        raise NutritionParseError("Could not parse the meal description. Please be more specific.")
