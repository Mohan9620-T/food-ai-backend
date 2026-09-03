from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VisionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
    visual_evidence: str = Field(min_length=1)


class VisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_type: Literal["food", "text", "other"]
    answer: str | None = None
    items: list[VisionItem] = Field(default_factory=list)
    uncertain_items: list[str] = Field(default_factory=list)
