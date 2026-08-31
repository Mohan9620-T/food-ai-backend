"""Add the meal input source.

Revision ID: 0004_add_meal_source
Revises: 0003_add_meal_logs
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_add_meal_source"
down_revision = "0003_add_meal_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meal_logs",
        sa.Column("source", sa.String(length=10), nullable=False, server_default="text"),
    )


def downgrade() -> None:
    op.drop_column("meal_logs", "source")
