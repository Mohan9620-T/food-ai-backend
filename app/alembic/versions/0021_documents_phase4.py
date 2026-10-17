"""Phase 4 document provenance, processing and external blob storage."""
import sqlalchemy as sa
from alembic import op

revision = "0021_documents_phase4"
down_revision = "0020_chat_message_sources"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("chat_messages", "chat_image_attachments", "chat_document_attachments"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("storage_key", sa.String(512), nullable=True))
            if table != "chat_messages":
                column = "file_data" if table == "chat_document_attachments" else "image_data"
                batch.alter_column(column, existing_type=sa.LargeBinary(), nullable=True)
    with op.batch_alter_table("chat_messages") as batch:
        batch.add_column(sa.Column("citations", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("file_clarification", sa.JSON(), nullable=True))
    with op.batch_alter_table("chat_document_attachments") as batch:
        batch.add_column(sa.Column("processing_status", sa.String(20), server_default="ready", nullable=False))
        batch.add_column(sa.Column("processing_stage", sa.String(50), nullable=True))
        batch.add_column(sa.Column("processing_error", sa.Text(), nullable=True))


def downgrade():
    # Do not silently destroy external-only files when returning to an older schema.
    from app.storage import read_blob
    connection = op.get_bind()
    for table in ("chat_messages", "chat_image_attachments", "chat_document_attachments"):
        column = "file_data" if table == "chat_document_attachments" else "image_data"
        rows = connection.execute(sa.text(f"SELECT id, storage_key FROM {table} WHERE storage_key IS NOT NULL")).all()
        for row in rows:
            data = read_blob(row.storage_key, None)
            connection.execute(sa.text(f"UPDATE {table} SET {column}=:data WHERE id=:id"), {"data": data, "id": row.id})
        with op.batch_alter_table(table) as batch:
            batch.drop_column("storage_key")
            if table != "chat_messages":
                batch.alter_column(column, existing_type=sa.LargeBinary(), nullable=False)
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_column("citations")
        batch.drop_column("file_clarification")
    with op.batch_alter_table("chat_document_attachments") as batch:
        for column in ("processing_status", "processing_stage", "processing_error"):
            batch.drop_column(column)
