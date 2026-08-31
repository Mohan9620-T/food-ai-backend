from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.meal_log import MealLog, MealLogItem
from app.services.usda_nutrition_service import NutritionResult


class MealRepository:
    def create(
        self,
        db: Session,
        user_id: int,
        raw_description: str,
        logged_at: datetime,
        items: list[NutritionResult],
        source: str = "text",
    ) -> MealLog:
        meal = MealLog(
            user_id=user_id,
            raw_description=raw_description,
            source=source,
            logged_at=logged_at,
            items=[
                MealLogItem(
                    food_name=item.food_name,
                    quantity=item.quantity,
                    unit=item.unit,
                    fdc_id=item.fdc_id,
                    calories=item.calories,
                    protein_g=item.protein_g,
                    carbs_g=item.carbs_g,
                    fat_g=item.fat_g,
                )
                for item in items
            ],
        )
        db.add(meal)
        db.commit()
        db.refresh(meal)
        return meal

    def list_for_user(
        self,
        db: Session,
        user_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[MealLog]:
        query = db.query(MealLog).filter(MealLog.user_id == user_id)
        if start_date:
            query = query.filter(MealLog.logged_at >= self._day_start(start_date))
        if end_date:
            query = query.filter(MealLog.logged_at < self._day_start(end_date) + timedelta(days=1))
        return query.order_by(MealLog.logged_at.desc()).all()

    def get_daily_totals(self, db: Session, user_id: int, target_date: date) -> dict:
        meals = self.list_for_user(db, user_id, target_date, target_date)
        matched = [item for meal in meals for item in meal.items if item.fdc_id is not None]
        unmatched_count = sum(
            1 for meal in meals for item in meal.items if item.fdc_id is None
        )
        return {
            "date": target_date,
            "calories": round(sum(item.calories or 0 for item in matched), 2),
            "protein_g": round(sum(item.protein_g or 0 for item in matched), 2),
            "carbs_g": round(sum(item.carbs_g or 0 for item in matched), 2),
            "fat_g": round(sum(item.fat_g or 0 for item in matched), 2),
            "matched_items": len(matched),
            "unmatched_items": unmatched_count,
        }

    def delete(self, db: Session, meal_id: int, user_id: int) -> bool:
        meal = db.query(MealLog).filter(
            MealLog.id == meal_id,
            MealLog.user_id == user_id,
        ).first()
        if not meal:
            return False
        db.delete(meal)
        db.commit()
        return True

    @staticmethod
    def _day_start(value: date) -> datetime:
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
