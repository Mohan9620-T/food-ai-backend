"""Add persistent per-user memory facts (allergies, dislikes, goals, constraints)."""

import sqlalchemy as sa
from alembic import op

revision = "0017_user_memory_facts"
down_revision = "0016_chat_image_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_memory_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("fact_text", sa.String(300), nullable=False),
        sa.Column("source_session_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_session_id"], ["chat_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "category", "fact_text", name="uq_user_memory_fact"),
    )
    op.create_index(op.f("ix_user_memory_facts_id"), "user_memory_facts", ["id"])
    op.create_index(op.f("ix_user_memory_facts_user_id"), "user_memory_facts", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_user_memory_facts_user_id"), table_name="user_memory_facts")
    op.drop_index(op.f("ix_user_memory_facts_id"), table_name="user_memory_facts")
    op.drop_table("user_memory_facts")
