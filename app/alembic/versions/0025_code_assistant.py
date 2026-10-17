"""Owner-scoped code repository snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0025_code_assistant"
down_revision = "0024_invoice_extraction"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("code_repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("indexed_at", sa.String(40), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "path"))
    op.create_index("ix_code_repositories_user_id", "code_repositories", ["user_id"])


def downgrade():
    op.drop_table("code_repositories")
