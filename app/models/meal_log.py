from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database.database import Base


class MealLog(Base):
    __tablename__ = "meal_logs"
    __table_args__ = (Index("ix_meal_logs_user_logged_at", "user_id", "logged_at"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    raw_description = Column(Text, nullable=False)
    source = Column(String(10), nullable=False, default="text", server_default="text")
    logged_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    items = relationship(
        "MealLogItem",
        back_populates="meal_log",
        cascade="all, delete-orphan",
        order_by="MealLogItem.id",
    )


class MealLogItem(Base):
    __tablename__ = "meal_log_items"

    id = Column(Integer, primary_key=True, index=True)
    meal_log_id = Column(Integer, ForeignKey("meal_logs.id"), nullable=False, index=True)
    food_name = Column(String(255), nullable=False)
    quantity = Column(Float, nullable=False)
    unit = Column(String(50), nullable=False)
    fdc_id = Column(Integer, nullable=True, index=True)
    calories = Column(Float, nullable=True)
    protein_g = Column(Float, nullable=True)
    carbs_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)

    meal_log = relationship("MealLog", back_populates="items")
