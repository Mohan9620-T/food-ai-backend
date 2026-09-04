import logging
import re
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher

import requests

from app.config import settings
from app.services.nutrition_parser_service import ParsedFoodItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NutritionResult:
    food_name: str
    quantity: float
    unit: str
    fdc_id: int | None = None
    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


class UsdaNutritionService:
    NUTRIENT_IDS = {"calories": 1008, "protein_g": 1003, "carbs_g": 1005, "fat_g": 1004}
    MASS_TO_GRAMS = {
        "g": 1.0,
        "gram": 1.0,
        "grams": 1.0,
        "kg": 1000.0,
        "kilogram": 1000.0,
        "kilograms": 1000.0,
        "oz": 28.3495,
        "ounce": 28.3495,
        "ounces": 28.3495,
        "lb": 453.592,
        "pound": 453.592,
        "pounds": 453.592,
    }

    def __init__(self) -> None:
        self._cache: dict[str, dict | None] = {}
        self._lock = threading.Lock()

    def lookup(self, item: ParsedFoodItem) -> NutritionResult:
        if not settings.USDA_API_KEY:
            return self._unmatched(item)

        key = self._normalize(item.food_name)
        with self._lock:
            cached = self._cache.get(key) if key in self._cache else ...
        if cached is ...:
            cached = self._fetch_best_food(item.food_name)
            with self._lock:
                self._cache[key] = cached
        if not cached:
            return self._unmatched(item)

        grams = self._to_grams(item.quantity, item.unit, cached)
        if grams is None:
            logger.warning("usda.unit_unmatched", extra={"fdc_id": cached.get("fdcId")})
            return self._unmatched(item)

        nutrients = self._nutrients_per_100g(cached)
        if nutrients.get("calories") is None:
            logger.warning("usda.nutrients_missing", extra={"fdc_id": cached.get("fdcId")})
            return self._unmatched(item)
        scale = grams / 100.0
        scaled = {
            name: round(value * scale, 2) if value is not None else None
            for name, value in nutrients.items()
        }
        return NutritionResult(
            food_name=item.food_name,
            quantity=item.quantity,
            unit=item.unit,
            fdc_id=int(cached["fdcId"]),
            **scaled,
        )

    def _fetch_best_food(self, food_name: str) -> dict | None:
        try:
            search_response = requests.post(
                f"{settings.USDA_API_URL}/foods/search",
                params={"api_key": settings.USDA_API_KEY},
                json={"query": food_name, "pageSize": 10},
                timeout=settings.USDA_TIMEOUT_SECONDS,
            )
            search_response.raise_for_status()
            candidates = search_response.json().get("foods", [])
            best = self._best_candidate(food_name, candidates)
            if not best:
                return None
            detail_response = requests.get(
                f"{settings.USDA_API_URL}/food/{best['fdcId']}",
                params={"api_key": settings.USDA_API_KEY},
                timeout=settings.USDA_TIMEOUT_SECONDS,
            )
            detail_response.raise_for_status()
            return detail_response.json()
        except (requests.RequestException, KeyError, TypeError, ValueError):
            logger.warning("usda.lookup_failed", exc_info=True)
            return None

    def _best_candidate(self, query: str, candidates: list[dict]) -> dict | None:
        query_normalized = self._normalize(query)
        query_words = set(query_normalized.split())
        ranked: list[tuple[float, dict]] = []
        for candidate in candidates:
            description = self._normalize(str(candidate.get("description", "")))
            if not description or not candidate.get("fdcId"):
                continue
            description_words = set(description.split())
            overlap = len(query_words & description_words) / max(len(query_words), 1)
            similarity = SequenceMatcher(None, query_normalized, description).ratio()
            data_bonus = 0.1 if candidate.get("dataType") in {"Foundation", "SR Legacy"} else 0
            score = max(overlap, similarity) + data_bonus
            if overlap > 0 or similarity >= 0.45:
                ranked.append((score, candidate))
        return max(ranked, key=lambda pair: pair[0])[1] if ranked else None

    def _to_grams(self, quantity: float, unit: str, food: dict) -> float | None:
        normalized_unit = self._normalize(unit)
        if normalized_unit in self.MASS_TO_GRAMS:
            return quantity * self.MASS_TO_GRAMS[normalized_unit]

        for portion in food.get("foodPortions", []):
            descriptor = " ".join(
                filter(
                    None,
                    [
                        str(portion.get("modifier", "")),
                        str(portion.get("portionDescription", "")),
                        str((portion.get("measureUnit") or {}).get("name", "")),
                        str((portion.get("measureUnit") or {}).get("abbreviation", "")),
                    ],
                )
            )
            descriptor = self._normalize(descriptor)
            if normalized_unit and (normalized_unit in descriptor or descriptor in normalized_unit):
                gram_weight = portion.get("gramWeight")
                amount = float(portion.get("amount") or 1)
                if gram_weight is not None and amount > 0:
                    return quantity * float(gram_weight) / amount

        serving_size = food.get("servingSize")
        serving_unit = self._normalize(str(food.get("servingSizeUnit", "")))
        if serving_size is not None and serving_unit == normalized_unit:
            return quantity * float(serving_size)
        return None

    def _nutrients_per_100g(self, food: dict) -> dict[str, float | None]:
        by_id: dict[int, float] = {}
        for entry in food.get("foodNutrients", []):
            nutrient = entry.get("nutrient") or {}
            nutrient_id = nutrient.get("id") or entry.get("nutrientId")
            amount = entry.get("amount") if "amount" in entry else entry.get("value")
            if nutrient_id is not None and amount is not None:
                by_id[int(nutrient_id)] = float(amount)
        return {name: by_id.get(nutrient_id) for name, nutrient_id in self.NUTRIENT_IDS.items()}

    @staticmethod
    def _normalize(value: str) -> str:
        words = re.findall(r"[a-z0-9]+", value.lower())
        return " ".join(
            word[:-1] if word.endswith("s") and len(word) > 3 else word for word in words
        )

    @staticmethod
    def _unmatched(item: ParsedFoodItem) -> NutritionResult:
        return NutritionResult(food_name=item.food_name, quantity=item.quantity, unit=item.unit)
