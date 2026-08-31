from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database.database import Base


class DietPlan(Base):
    __tablename__ = "diet_plans"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    target_calories = Column(Float, nullable=True)
    target_protein_g = Column(Float, nullable=True)
    target_carbs_g = Column(Float, nullable=True)
    target_fat_g = Column(Float, nullable=True)
    meals = relationship("DietPlanMeal", back_populates="plan", cascade="all, delete-orphan", order_by="DietPlanMeal.day_of_week, DietPlanMeal.id")


class DietPlanMeal(Base):
    __tablename__ = "diet_plan_meals"
    id = Column(Integer, primary_key=True, index=True)
    diet_plan_id = Column(Integer, ForeignKey("diet_plans.id"), nullable=False, index=True)
    day_of_week = Column(Integer, nullable=False)
    meal_slot = Column(String(20), nullable=False)
    description = Column(String(255), nullable=False)
    plan = relationship("DietPlan", back_populates="meals")
    items = relationship("DietPlanMealItem", back_populates="meal", cascade="all, delete-orphan", order_by="DietPlanMealItem.id")


class DietPlanMealItem(Base):
    __tablename__ = "diet_plan_meal_items"
    id = Column(Integer, primary_key=True, index=True)
    diet_plan_meal_id = Column(Integer, ForeignKey("diet_plan_meals.id"), nullable=False, index=True)
    food_name = Column(String(255), nullable=False)
    quantity = Column(Float, nullable=False)
    unit = Column(String(50), nullable=False)
    fdc_id = Column(Integer, nullable=True, index=True)
    calories = Column(Float, nullable=True)
    protein_g = Column(Float, nullable=True)
    carbs_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    meal = relationship("DietPlanMeal", back_populates="items")
