from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.nutrition_parser_service import ParsedFoodItem

MealSlot = Literal["breakfast", "lunch", "dinner", "snack"]


class ProposedMeal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meal_slot: MealSlot
    description: str = Field(min_length=1, max_length=255)
    items: list[ParsedFoodItem] = Field(min_length=1)


class ProposedDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day_of_week: int = Field(ge=0, le=6)
    meals: list[ProposedMeal] = Field(min_length=4, max_length=4)


class ProposedDietPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[ProposedDay] = Field(min_length=7, max_length=7)


class DietPlanItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    food_name: str
    quantity: float
    unit: str
    fdc_id: int | None
    calories: float | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None


class DietPlanMealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    day_of_week: int
    meal_slot: MealSlot
    description: str
    items: list[DietPlanItemOut]


class DailyPlanTotals(BaseModel):
    day_of_week: int
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    matched_items: int
    unmatched_items: int


class DietPlanOut(BaseModel):
    id: int
    user_id: int
    created_at: datetime
    target_calories: float | None
    target_protein_g: float | None
    target_carbs_g: float | None
    target_fat_g: float | None
    meals: list[DietPlanMealOut]
    daily_totals: list[DailyPlanTotals]
