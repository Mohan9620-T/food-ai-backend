"""Persist images attached to chat messages.

Revision ID: 0007_persist_chat_images
Revises: 0006_add_diet_plans
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_persist_chat_images"
down_revision = "0006_add_diet_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("image_data", sa.LargeBinary(), nullable=True))
    op.add_column(
        "chat_messages",
        sa.Column("image_content_type", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "image_content_type")
    op.drop_column("chat_messages", "image_data")
