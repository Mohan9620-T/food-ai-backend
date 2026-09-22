"""Add rolling conversation memory columns to chat sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0013_chat_session_memory"
down_revision = "0012_document_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("rolling_summary", sa.Text(), nullable=True))
    op.add_column(
        "chat_sessions",
        sa.Column("summary_covers_through_message_id", sa.Integer(), nullable=True),
    )
    op.add_column("chat_sessions", sa.Column("memory_facts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_sessions", "memory_facts")
    op.drop_column("chat_sessions", "summary_covers_through_message_id")
    op.drop_column("chat_sessions", "rolling_summary")
