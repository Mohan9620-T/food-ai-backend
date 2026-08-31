"""Add diet plans. Revision ID: 0006_add_diet_plans"""
from alembic import op
import sqlalchemy as sa
revision="0006_add_diet_plans"; down_revision="0005_add_user_profile"; branch_labels=None; depends_on=None
def upgrade():
    op.create_table("diet_plans",sa.Column("id",sa.Integer(),nullable=False),sa.Column("user_id",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("target_calories",sa.Float()),sa.Column("target_protein_g",sa.Float()),sa.Column("target_carbs_g",sa.Float()),sa.Column("target_fat_g",sa.Float()),sa.ForeignKeyConstraint(["user_id"],["users.id"]),sa.PrimaryKeyConstraint("id"))
    op.create_index(op.f("ix_diet_plans_id"),"diet_plans",["id"]);op.create_index(op.f("ix_diet_plans_user_id"),"diet_plans",["user_id"])
    op.create_table("diet_plan_meals",sa.Column("id",sa.Integer(),nullable=False),sa.Column("diet_plan_id",sa.Integer(),nullable=False),sa.Column("day_of_week",sa.Integer(),nullable=False),sa.Column("meal_slot",sa.String(20),nullable=False),sa.Column("description",sa.String(255),nullable=False),sa.ForeignKeyConstraint(["diet_plan_id"],["diet_plans.id"]),sa.PrimaryKeyConstraint("id"))
    op.create_index(op.f("ix_diet_plan_meals_id"),"diet_plan_meals",["id"]);op.create_index(op.f("ix_diet_plan_meals_diet_plan_id"),"diet_plan_meals",["diet_plan_id"])
    op.create_table("diet_plan_meal_items",sa.Column("id",sa.Integer(),nullable=False),sa.Column("diet_plan_meal_id",sa.Integer(),nullable=False),sa.Column("food_name",sa.String(255),nullable=False),sa.Column("quantity",sa.Float(),nullable=False),sa.Column("unit",sa.String(50),nullable=False),sa.Column("fdc_id",sa.Integer()),sa.Column("calories",sa.Float()),sa.Column("protein_g",sa.Float()),sa.Column("carbs_g",sa.Float()),sa.Column("fat_g",sa.Float()),sa.ForeignKeyConstraint(["diet_plan_meal_id"],["diet_plan_meals.id"]),sa.PrimaryKeyConstraint("id"))
    op.create_index(op.f("ix_diet_plan_meal_items_id"),"diet_plan_meal_items",["id"]);op.create_index(op.f("ix_diet_plan_meal_items_diet_plan_meal_id"),"diet_plan_meal_items",["diet_plan_meal_id"]);op.create_index(op.f("ix_diet_plan_meal_items_fdc_id"),"diet_plan_meal_items",["fdc_id"])
def downgrade():
    op.drop_table("diet_plan_meal_items");op.drop_table("diet_plan_meals");op.drop_table("diet_plans")
