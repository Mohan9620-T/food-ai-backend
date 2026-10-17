"""Account roles and hashed personal API keys."""
import sqlalchemy as sa
from alembic import op

revision = "0023_accounts_platform"
down_revision = "0022_workspaces"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("role", sa.String(10), server_default="member", nullable=False))
    op.create_table("api_keys", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("name", sa.String(100), nullable=False), sa.Column("prefix", sa.String(16), nullable=False),
                    sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
                    sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("last_used_at", sa.DateTime()), sa.Column("revoked_at", sa.DateTime()))
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])


def downgrade():
    op.drop_table("api_keys")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("role")
