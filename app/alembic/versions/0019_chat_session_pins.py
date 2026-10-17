"""Add pin state to chat sessions for pinned-first, paginated session lists."""

import sqlalchemy as sa
from alembic import op

revision = "0019_chat_session_pins"
down_revision = "0018_email_otp_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("chat_sessions", sa.Column("pin_order", sa.Integer(), nullable=True))
    op.create_index(
        "ix_chat_sessions_user_pin_order",
        "chat_sessions",
        ["user_id", "is_pinned", "pin_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_user_pin_order", table_name="chat_sessions")
    op.drop_column("chat_sessions", "pin_order")
    op.drop_column("chat_sessions", "is_pinned")
