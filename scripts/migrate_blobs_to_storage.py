"""Idempotent blob migration. Run with python -m scripts.migrate_blobs_to_storage."""

import argparse

from app.database.database import SessionLocal
from app.models.chat import ChatDocumentAttachment, ChatImageAttachment, ChatMessageRecord
from app.storage import store_blob


def migrate(dry_run=False):
    count = 0
    with SessionLocal() as db:
        for model, field, content in (
            (ChatMessageRecord, "_image_data", "image_content_type"),
            (ChatImageAttachment, "_image_data", "content_type"),
            (ChatDocumentAttachment, "_file_data", "content_type"),
        ):
            for row in db.query(model).yield_per(100):
                data = getattr(row, field)
                if not data or row.storage_key:
                    continue
                count += 1
                if not dry_run:
                    key = store_blob(data, getattr(row, content) or "application/octet-stream")
                    if key is None:
                        raise RuntimeError("Select an available non-database storage backend first")
                    row.storage_key = key
                    setattr(row, field, None)
                    db.commit()
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    print(f"Blobs selected: {migrate(parser.parse_args().dry_run)}")
