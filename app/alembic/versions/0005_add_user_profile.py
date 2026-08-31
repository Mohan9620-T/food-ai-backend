"""Add user profile. Revision ID: 0005_add_user_profile"""
from alembic import op
import sqlalchemy as sa
revision="0005_add_user_profile"; down_revision="0004_add_meal_source"; branch_labels=None; depends_on=None
def upgrade():
    op.create_table("user_profiles", sa.Column("id",sa.Integer(),nullable=False), sa.Column("user_id",sa.Integer(),nullable=False), sa.Column("goal",sa.String(20),nullable=False), sa.Column("target_calories",sa.Float()), sa.Column("target_protein_g",sa.Float()), sa.Column("target_carbs_g",sa.Float()), sa.Column("target_fat_g",sa.Float()), sa.Column("allergies",sa.Text(),nullable=False), sa.Column("dietary_restrictions",sa.Text(),nullable=False), sa.Column("disliked_foods",sa.Text(),nullable=False), sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False), sa.ForeignKeyConstraint(["user_id"],["users.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("user_id"))
    op.create_index(op.f("ix_user_profiles_id"),"user_profiles",["id"]); op.create_index(op.f("ix_user_profiles_user_id"),"user_profiles",["user_id"], unique=True)
def downgrade():
    op.drop_index(op.f("ix_user_profiles_user_id"),table_name="user_profiles"); op.drop_index(op.f("ix_user_profiles_id"),table_name="user_profiles"); op.drop_table("user_profiles")
