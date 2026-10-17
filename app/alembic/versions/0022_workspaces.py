"""Workspace membership, scope, bookmarks, activity and cached summaries."""

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0022_workspaces"
down_revision = "0021_documents_phase4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workspaces", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("name", sa.String(120), nullable=False), sa.Column("description", sa.String(500), nullable=False, server_default=""),
                    sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
                    sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
                    sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_workspaces_user_id", "workspaces", ["user_id"])
    op.create_table("workspace_documents", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("document_id", sa.Integer(), sa.ForeignKey("chat_document_attachments.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("checksum", sa.String(64)), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
                    sa.UniqueConstraint("workspace_id", "document_id"), sa.UniqueConstraint("workspace_id", "checksum"))
    op.create_table("workspace_sessions", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
                    sa.Column("retrieval_mode", sa.String(10), nullable=False, server_default="all"))
    op.create_table("workspace_session_documents", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("workspace_session_id", sa.Integer(), sa.ForeignKey("workspace_sessions.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("document_id", sa.Integer(), sa.ForeignKey("chat_document_attachments.id", ondelete="CASCADE"), nullable=False),
                    sa.UniqueConstraint("workspace_session_id", "document_id"))
    op.create_table("workspace_bookmarks", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("target_type", sa.String(20), nullable=False), sa.Column("target_id", sa.Integer(), nullable=False),
                    sa.Column("note", sa.String(500), nullable=False, server_default=""),
                    sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_table("workspace_timeline", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("event_type", sa.String(40), nullable=False), sa.Column("title", sa.String(300), nullable=False),
                    sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_table("workspace_summaries", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True),
                    sa.Column("content", sa.Text(), nullable=False, server_default=""),
                    sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    for table in ("workspace_documents", "workspace_sessions", "workspace_bookmarks", "workspace_timeline"):
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
    connection = op.get_bind()
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, autoload_with=connection)
    documents = sa.Table("workspace_documents", metadata, autoload_with=connection)
    sessions = sa.Table("workspace_sessions", metadata, autoload_with=connection)
    for user_id in connection.execute(sa.text("SELECT id FROM users")).scalars():
        workspace_id = connection.execute(workspaces.insert().values(user_id=user_id, name="My Workspace", is_default=True)).inserted_primary_key[0]
        session_ids = list(connection.execute(sa.text("SELECT id FROM chat_sessions WHERE user_id=:uid"), {"uid": user_id}).scalars())
        if session_ids:
            connection.execute(sessions.insert(), [{"workspace_id": workspace_id, "session_id": sid} for sid in session_ids])
        # Hash inline legacy bytes without loading external storage credentials.
        # External-only rows remain linked, with a nullable checksum until read.
        rows = connection.execute(sa.text("SELECT d.id, d.file_data FROM chat_document_attachments d JOIN chat_sessions s ON s.id=d.session_id WHERE s.user_id=:uid ORDER BY d.id"), {"uid": user_id})
        seen = set()
        for row in rows:
            checksum = hashlib.sha256(row.file_data).hexdigest() if row.file_data else None
            if checksum in seen:
                checksum = None
            if checksum:
                seen.add(checksum)
            connection.execute(documents.insert().values(workspace_id=workspace_id, document_id=row.id, checksum=checksum))


def downgrade():
    for table in ("workspace_session_documents", "workspace_summaries", "workspace_timeline", "workspace_bookmarks", "workspace_sessions", "workspace_documents", "workspaces"):
        op.drop_table(table)
