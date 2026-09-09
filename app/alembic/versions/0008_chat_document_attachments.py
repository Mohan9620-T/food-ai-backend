"""persist chat document attachments

Revision ID: 0008_chat_documents
Revises: 0007_persist_chat_images
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_chat_documents"
down_revision = "0007_persist_chat_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_document_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("file_data", sa.LargeBinary(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("structured_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(op.f("ix_chat_document_attachments_id"), "chat_document_attachments", ["id"])
    op.create_index(op.f("ix_chat_document_attachments_message_id"), "chat_document_attachments", ["message_id"], unique=True)
    op.create_index(op.f("ix_chat_document_attachments_session_id"), "chat_document_attachments", ["session_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_document_attachments_session_id"), table_name="chat_document_attachments")
    op.drop_index(op.f("ix_chat_document_attachments_message_id"), table_name="chat_document_attachments")
    op.drop_index(op.f("ix_chat_document_attachments_id"), table_name="chat_document_attachments")
    op.drop_table("chat_document_attachments")
