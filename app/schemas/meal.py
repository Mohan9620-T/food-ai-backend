from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MealCreate(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    logged_at: datetime | None = None


class MealLogItemOut(BaseModel):
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


class MealLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_description: str
    source: Literal["text", "image"]
    logged_at: datetime
    created_at: datetime
    items: list[MealLogItemOut]


class DailyTotalsOut(BaseModel):
    date: date
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    matched_items: int
    unmatched_items: int
