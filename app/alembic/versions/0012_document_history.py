"""Persist structured document data and resumable document workflow history."""

import sqlalchemy as sa
from alembic import op

revision = "0012_document_history"
down_revision = "0011_document_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("automation", sa.JSON(), nullable=True))
    op.add_column("chat_document_attachments", sa.Column("extracted_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_document_attachments", "extracted_data")
    op.drop_column("chat_messages", "automation")
