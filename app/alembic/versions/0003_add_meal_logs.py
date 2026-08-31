"""Add meal logs and USDA-grounded meal items.

Revision ID: 0003_add_meal_logs
Revises: 0002_add_refresh_tokens
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_add_meal_logs"
down_revision = "0002_add_refresh_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meal_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("raw_description", sa.Text(), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meal_logs_id"), "meal_logs", ["id"], unique=False)
    op.create_index(op.f("ix_meal_logs_user_id"), "meal_logs", ["user_id"], unique=False)
    op.create_index("ix_meal_logs_user_logged_at", "meal_logs", ["user_id", "logged_at"])

    op.create_table(
        "meal_log_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("meal_log_id", sa.Integer(), nullable=False),
        sa.Column("food_name", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=False),
        sa.Column("fdc_id", sa.Integer(), nullable=True),
        sa.Column("calories", sa.Float(), nullable=True),
        sa.Column("protein_g", sa.Float(), nullable=True),
        sa.Column("carbs_g", sa.Float(), nullable=True),
        sa.Column("fat_g", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["meal_log_id"], ["meal_logs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meal_log_items_id"), "meal_log_items", ["id"], unique=False)
    op.create_index(op.f("ix_meal_log_items_meal_log_id"), "meal_log_items", ["meal_log_id"], unique=False)
    op.create_index(op.f("ix_meal_log_items_fdc_id"), "meal_log_items", ["fdc_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_meal_log_items_fdc_id"), table_name="meal_log_items")
    op.drop_index(op.f("ix_meal_log_items_meal_log_id"), table_name="meal_log_items")
    op.drop_index(op.f("ix_meal_log_items_id"), table_name="meal_log_items")
    op.drop_table("meal_log_items")
    op.drop_index("ix_meal_logs_user_logged_at", table_name="meal_logs")
    op.drop_index(op.f("ix_meal_logs_user_id"), table_name="meal_logs")
    op.drop_index(op.f("ix_meal_logs_id"), table_name="meal_logs")
    op.drop_table("meal_logs")
