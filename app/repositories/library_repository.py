"""A read-only, owner-scoped view across existing attachments; no copied rows."""

from sqlalchemy import String, cast, func, literal, select, union_all
from sqlalchemy.orm import Session

from app.models.chat import (
    ChatDocumentAttachment,
    ChatImageAttachment,
    ChatMessageRecord,
    ChatSession,
)


class LibraryRepository:
    def list(
        self, db: Session, user_id: int, kind: str | None, query: str, limit: int, offset: int
    ):
        documents = (
            select(
                cast(ChatDocumentAttachment.id, String).label("id"),
                literal("document").label("source"),
                ChatDocumentAttachment.kind.label("kind"),
                ChatDocumentAttachment.filename.label("filename"),
                ChatDocumentAttachment.content_type.label("content_type"),
                ChatDocumentAttachment.file_size.label("size"),
                ChatDocumentAttachment.created_at.label("created_at"),
                ChatSession.id.label("session_id"),
                ChatSession.title.label("session_title"),
            )
            .join(ChatSession, ChatSession.id == ChatDocumentAttachment.session_id)
            .where(ChatSession.user_id == user_id)
        )
        primary = (
            select(
                cast(ChatMessageRecord.id, String).label("id"),
                literal("message").label("source"),
                literal("image").label("kind"),
                (literal("image-") + cast(ChatMessageRecord.id, String)).label("filename"),
                ChatMessageRecord.image_content_type.label("content_type"),
                func.coalesce(func.length(ChatMessageRecord._image_data), 0).label("size"),
                ChatMessageRecord.created_at.label("created_at"),
                ChatSession.id.label("session_id"),
                ChatSession.title.label("session_title"),
            )
            .join(ChatSession, ChatSession.id == ChatMessageRecord.session_id)
            .where(
                ChatSession.user_id == user_id,
                ChatMessageRecord.image_content_type.isnot(None),
                ~select(ChatDocumentAttachment.id)
                .where(ChatDocumentAttachment.message_id == ChatMessageRecord.id)
                .exists(),
            )
        )
        extra = (
            select(
                cast(ChatImageAttachment.id, String).label("id"),
                literal("extra").label("source"),
                literal("image").label("kind"),
                (literal("image-") + cast(ChatImageAttachment.id, String)).label("filename"),
                ChatImageAttachment.content_type.label("content_type"),
                func.coalesce(func.length(ChatImageAttachment._image_data), 0).label("size"),
                ChatMessageRecord.created_at.label("created_at"),
                ChatSession.id.label("session_id"),
                ChatSession.title.label("session_title"),
            )
            .join(ChatMessageRecord, ChatMessageRecord.id == ChatImageAttachment.message_id)
            .join(ChatSession, ChatSession.id == ChatMessageRecord.session_id)
            .where(ChatSession.user_id == user_id)
        )
        all_rows = union_all(documents, primary, extra).subquery()
        effective_kind = func.coalesce(func.nullif(all_rows.c.kind, "uploaded"), "document")
        statement = select(all_rows)
        if kind == "image":
            statement = statement.where(
                all_rows.c.content_type.like("image/%"), all_rows.c.kind != "generated"
            )
        elif kind == "document":
            statement = statement.where(
                effective_kind == "document", ~all_rows.c.content_type.like("image/%")
            )
        elif kind == "generated":
            statement = statement.where(all_rows.c.kind == "generated")
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(all_rows.c.filename.ilike(f"%{escaped}%", escape="\\"))
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            db.execute(
                statement.order_by(all_rows.c.created_at.desc(), all_rows.c.source, all_rows.c.id)
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
        items = []
        for row in rows:
            item = dict(row)
            item["id"] = {"message": "m", "extra": "i", "document": ""}[item.pop("source")] + item[
                "id"
            ]
            item["kind"] = (
                "generated"
                if item["kind"] == "generated"
                else "image"
                if item["content_type"].startswith("image/")
                else "document"
            )
            item["thumbnail"] = (
                f"/library/{item['kind']}/{item['id']}/preview" if item["kind"] == "image" else None
            )
            if item["size"] == 0 and item["kind"] == "image":
                owned = self.get(db, user_id, item["kind"], item["id"])
                if owned:
                    item["size"] = len(owned[1])
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get(self, db: Session, user_id: int, kind: str, item_id: str, *, read_data: bool = True):
        if item_id.startswith(("m", "i")):
            if kind != "image" or not item_id[1:].isdigit():
                return None
            model = ChatMessageRecord if item_id.startswith("m") else ChatImageAttachment
            statement = db.query(model)
            if model is ChatImageAttachment:
                statement = statement.join(
                    ChatMessageRecord, ChatMessageRecord.id == ChatImageAttachment.message_id
                )
            row = (
                statement.join(ChatSession, ChatSession.id == ChatMessageRecord.session_id)
                .filter(ChatSession.user_id == user_id, model.id == int(item_id[1:]))
                .first()
            )
            if row is None:
                return None
            content_type = (
                getattr(row, "image_content_type")
                if model is ChatMessageRecord
                else getattr(row, "content_type")
            )
            return (
                row,
                bytes(getattr(row, "image_data")) if read_data else b"",
                content_type,
                "image-" + item_id,
            )
        if not item_id.isdigit():
            return None
        row = (
            db.query(ChatDocumentAttachment)
            .join(ChatSession, ChatSession.id == ChatDocumentAttachment.session_id)
            .filter(ChatSession.user_id == user_id, ChatDocumentAttachment.id == int(item_id))
            .first()
        )
        if row is None:
            return None
        expected = (
            "generated"
            if row.kind == "generated"
            else "image"
            if row.content_type.startswith("image/")
            else "document"
        )
        if expected != kind:
            return None
        return row, bytes(row.file_data) if read_data else b"", row.content_type, row.filename
