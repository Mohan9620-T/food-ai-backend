from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Goal = Literal["lose_weight", "maintain", "gain_weight"]


class UserProfileUpsert(BaseModel):
    goal: Goal
    target_calories: float | None = Field(default=None, gt=0)
    target_protein_g: float | None = Field(default=None, ge=0)
    target_carbs_g: float | None = Field(default=None, ge=0)
    target_fat_g: float | None = Field(default=None, ge=0)
    allergies: list[str] = Field(default_factory=list)
    dietary_restrictions: list[str] = Field(default_factory=list)
    disliked_foods: list[str] = Field(default_factory=list)


class UserProfileOut(UserProfileUpsert):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    updated_at: datetime
