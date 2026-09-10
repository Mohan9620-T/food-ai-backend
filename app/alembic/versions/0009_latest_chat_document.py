"""track the latest chat document

Revision ID: 0009_latest_chat_document
Revises: 0008_chat_documents
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_latest_chat_document"
down_revision = "0008_chat_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("latest_document_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_chat_sessions_latest_document_id",
        "chat_sessions",
        "chat_document_attachments",
        ["latest_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_chat_sessions_latest_document_id"),
        "chat_sessions",
        ["latest_document_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_sessions_latest_document_id"), table_name="chat_sessions")
    op.drop_constraint(
        "fk_chat_sessions_latest_document_id", "chat_sessions", type_="foreignkey"
    )
    op.drop_column("chat_sessions", "latest_document_id")
