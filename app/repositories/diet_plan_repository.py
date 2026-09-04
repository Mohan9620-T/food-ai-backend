from typing import cast

from sqlalchemy.orm import Session, selectinload

from app.models.diet_plan import DietPlan, DietPlanMeal, DietPlanMealItem
from app.models.user_profile import UserProfile


class DietPlanRepository:
    def create(self, db: Session, user_id: int, profile: UserProfile, days: list[dict]) -> DietPlan:
        plan = DietPlan(
            user_id=user_id,
            target_calories=profile.target_calories,
            target_protein_g=profile.target_protein_g,
            target_carbs_g=profile.target_carbs_g,
            target_fat_g=profile.target_fat_g,
        )
        for day in days:
            for proposed_meal in day["meals"]:
                meal = DietPlanMeal(
                    day_of_week=day["day_of_week"],
                    meal_slot=proposed_meal["meal_slot"],
                    description=proposed_meal["description"],
                )
                meal.items = [DietPlanMealItem(**item.__dict__) for item in proposed_meal["items"]]
                plan.meals.append(meal)
        db.add(plan)
        db.commit()
        db.refresh(plan)
        created = self.get(db, cast(int, plan.id), user_id)
        if created is None:
            raise RuntimeError("Created diet plan could not be reloaded")
        return created

    def list_for_user(self, db: Session, user_id: int) -> list[DietPlan]:
        return (
            self._query(db)
            .filter(DietPlan.user_id == user_id)
            .order_by(DietPlan.created_at.desc())
            .all()
        )

    def get(self, db: Session, plan_id: int, user_id: int) -> DietPlan | None:
        return self._query(db).filter(DietPlan.id == plan_id, DietPlan.user_id == user_id).first()

    def delete(self, db: Session, plan_id: int, user_id: int) -> bool:
        plan = self.get(db, plan_id, user_id)
        if plan is None:
            return False
        db.delete(plan)
        db.commit()
        return True

    @staticmethod
    def _query(db: Session):
        return db.query(DietPlan).options(
            selectinload(DietPlan.meals).selectinload(DietPlanMeal.items)
        )
