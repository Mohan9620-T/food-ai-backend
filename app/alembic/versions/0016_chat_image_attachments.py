"""Persist additional images in a chat turn."""

import sqlalchemy as sa
from alembic import op

revision = "0016_chat_image_attachments"
down_revision = "0015_account_password_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_image_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_data", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(50), nullable=False),
    )
    op.create_index(
        "ix_chat_image_attachments_message_id", "chat_image_attachments", ["message_id"]
    )


def downgrade() -> None:
    op.drop_table("chat_image_attachments")
