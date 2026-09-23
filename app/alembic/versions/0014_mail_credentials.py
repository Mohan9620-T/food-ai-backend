"""Persist encrypted, rotated email provider refresh tokens."""

import sqlalchemy as sa
from alembic import op

revision = "0014_mail_credentials"
down_revision = "0013_chat_session_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_credentials",
        sa.Column("configuration_id", sa.String(64), primary_key=True),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mail_credentials")
