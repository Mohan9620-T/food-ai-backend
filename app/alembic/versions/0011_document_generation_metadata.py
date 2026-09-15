"""Persist document provenance and disclosed assumptions."""

import sqlalchemy as sa
from alembic import op

revision = "0011_document_metadata"
down_revision = "0010_internal_chat_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_document_attachments", sa.Column("generation_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_document_attachments", "generation_metadata")
