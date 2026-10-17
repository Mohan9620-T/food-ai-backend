"""Persist structured web-search sources for a chat message."""

import sqlalchemy as sa
from alembic import op

revision = "0020_chat_message_sources"
down_revision = "0019_chat_session_pins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_message_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(2048), nullable=True),
        sa.Column("query", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_chat_message_sources_message_id", "chat_message_sources", ["message_id"]
    )


def downgrade() -> None:
    op.drop_table("chat_message_sources")
