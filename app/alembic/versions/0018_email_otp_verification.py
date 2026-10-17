"""Add email OTP verification for first login and OTP-based password reset."""

import sqlalchemy as sa
from alembic import op

revision = "0018_email_otp_verification"
down_revision = "0017_user_memory_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "email_otps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("otp_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_email_otps_id"), "email_otps", ["id"])
    op.create_index("ix_email_otps_user_purpose", "email_otps", ["user_id", "purpose"])


def downgrade() -> None:
    op.drop_index("ix_email_otps_user_purpose", table_name="email_otps")
    op.drop_index(op.f("ix_email_otps_id"), table_name="email_otps")
    op.drop_table("email_otps")
    op.drop_column("users", "is_email_verified")
