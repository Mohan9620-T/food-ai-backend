import json
from typing import cast

from sqlalchemy.orm import Session

from app.models.user_profile import UserProfile
from app.repositories.profile_repository import ProfileRepository
from app.schemas.profile import UserProfileUpsert


class ProfileService:
    LIST_FIELDS = ("allergies", "dietary_restrictions", "disliked_foods")

    def __init__(self) -> None:
        self.repository = ProfileRepository()

    def get(self, db: Session, user_id: int) -> UserProfile | None:
        return self.repository.get(db, user_id)

    def upsert(self, db: Session, user_id: int, payload: UserProfileUpsert) -> UserProfile:
        values = payload.model_dump()
        for field in self.LIST_FIELDS:
            values[field] = json.dumps(self._clean(values[field]))
        return self.repository.upsert(db, user_id, values)

    def serialize(self, profile: UserProfile) -> dict:
        return {
            "id": profile.id,
            "user_id": profile.user_id,
            "goal": profile.goal,
            "target_calories": profile.target_calories,
            "target_protein_g": profile.target_protein_g,
            "target_carbs_g": profile.target_carbs_g,
            "target_fat_g": profile.target_fat_g,
            "allergies": self._decode(cast(str, profile.allergies)),
            "dietary_restrictions": self._decode(cast(str, profile.dietary_restrictions)),
            "disliked_foods": self._decode(cast(str, profile.disliked_foods)),
            "updated_at": profile.updated_at,
        }

    @staticmethod
    def _clean(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @staticmethod
    def _decode(value: str) -> list[str]:
        try:
            parsed = json.loads(value)
            return [str(item) for item in parsed] if isinstance(parsed, list) else []
        except (TypeError, json.JSONDecodeError):
            return [item.strip() for item in (value or "").split(",") if item.strip()]
