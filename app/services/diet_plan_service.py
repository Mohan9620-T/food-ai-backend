import json
import logging

import requests
from pydantic import TypeAdapter, ValidationError

from app.config import settings
from app.models.user_profile import UserProfile
from app.schemas.diet_plan import ProposedDietPlan
from app.services.profile_service import ProfileService
from app.services.usda_nutrition_service import UsdaNutritionService

logger = logging.getLogger(__name__)


class DietPlanGenerationError(ValueError):
    pass


class DietPlanService:
    _adapter = TypeAdapter(ProposedDietPlan)

    def __init__(self) -> None:
        self.profile_service = ProfileService()
        self.nutrition = UsdaNutritionService()

    def generate(self, profile: UserProfile) -> list[dict]:
        system_prompt = self.build_system_prompt(profile)
        for attempt in range(2):
            messages = [{"role": "system", "content": system_prompt}]
            if attempt:
                messages.append(
                    {
                        "role": "system",
                        "content": "Your previous response was invalid. Return only the exact required JSON object.",
                    }
                )
            messages.append({"role": "user", "content": "Create my seven-day meal plan."})
            try:
                response = requests.post(
                    settings.OLLAMA_URL,
                    json={
                        "model": settings.OLLAMA_MODEL,
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": 0},
                        "messages": messages,
                    },
                    timeout=settings.OLLAMA_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                proposal = self._adapter.validate_python(
                    json.loads(response.json()["message"]["content"])
                )
                self._validate_week(proposal)
                return [
                    {
                        "day_of_week": day.day_of_week,
                        "meals": [
                            {
                                "meal_slot": meal.meal_slot,
                                "description": meal.description,
                                "items": [self.nutrition.lookup(item) for item in meal.items],
                            }
                            for meal in day.meals
                        ],
                    }
                    for day in proposal.days
                ]
            except (requests.RequestException, KeyError, TypeError, ValueError, ValidationError):
                logger.warning("diet_plan.generate_attempt_failed", extra={"attempt": attempt + 1})
        raise DietPlanGenerationError("Could not generate a valid diet plan. Please try again.")

    def build_system_prompt(self, profile: UserProfile) -> str:
        data = self.profile_service.serialize(profile)
        targets = (
            ", ".join(
                f"{name}={data[name]}"
                for name in (
                    "target_calories",
                    "target_protein_g",
                    "target_carbs_g",
                    "target_fat_g",
                )
                if data[name] is not None
            )
            or "not specified"
        )
        allergies = ", ".join(data["allergies"]) or "none stated"
        restrictions = ", ".join(data["dietary_restrictions"]) or "none stated"
        dislikes = ", ".join(data["disliked_foods"]) or "none stated"
        return f"""You propose meal components only. Goal: {data["goal"]}. Daily targets: {targets}.
HARD EXCLUSIONS - allergies: {allergies}.
HARD EXCLUSIONS - dietary restrictions: {restrictions}.
Never suggest a hard-excluded food or any food that commonly contains one. Disliked foods are soft exclusions: {dislikes}.
Return JSON only with exactly this shape: {{"days":[{{"day_of_week":0,"meals":[{{"meal_slot":"breakfast","description":"...","items":[{{"food_name":"...","quantity":1,"unit":"..."}}]}}]}}]}}.
Return exactly days 0 through 6. Each day must contain exactly breakfast, lunch, dinner, and snack once each.
Food items must contain exactly food_name, quantity, unit. Never include calories, macros, nutrients, estimates, commentary, or Markdown."""

    @staticmethod
    def _validate_week(plan: ProposedDietPlan) -> None:
        if {day.day_of_week for day in plan.days} != set(range(7)):
            raise ValueError("Plan must contain each day exactly once")
        expected_slots = {"breakfast", "lunch", "dinner", "snack"}
        if any({meal.meal_slot for meal in day.meals} != expected_slots for day in plan.days):
            raise ValueError("Each day must contain every meal slot exactly once")
