"""hide internal document pipeline messages

Revision ID: 0010_internal_chat_messages
Revises: 0009_latest_chat_document
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_internal_chat_messages"
down_revision = "0009_latest_chat_document"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("is_internal", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "is_internal")
