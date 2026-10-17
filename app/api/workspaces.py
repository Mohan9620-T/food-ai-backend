"""Flag-gated workspaces. Require membership before reading any resource."""

import hashlib
from io import BytesIO
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.models.chat import ChatMessageRecord, ChatSession
from app.models.workspace import (
    Workspace,
    WorkspaceBookmark,
    WorkspaceDocument,
    WorkspaceSession,
    WorkspaceSessionDocument,
    WorkspaceTimeline,
)
from app.repositories.chat_repository import ChatRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.schemas.chat_session import ChatSessionDetailOut
from app.schemas.workspace import BookmarkWrite, ConversationWrite, ScopeWrite, WorkspaceWrite
from app.services import workspace_service as service
from app.utils.auth_dependency import get_current_user


def enabled():
    if not settings.ENABLE_WORKSPACES:
        raise HTTPException(404, "Workspaces are not enabled")


router = APIRouter(prefix="/workspaces", tags=["Workspaces"], dependencies=[Depends(enabled)])
repository = WorkspaceRepository()


def owned(workspace_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return repository.require(db, workspace_id, int(user["sub"]))


@router.get("")
def list_workspaces(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    repository.default(db, int(user["sub"]))
    return (
        db.query(Workspace)
        .filter_by(user_id=int(user["sub"]), archived=False)
        .order_by(Workspace.id)
        .all()
    )


@router.post("", status_code=201)
def create_workspace(
    payload: WorkspaceWrite, db: Session = Depends(get_db), user: dict = Depends(get_current_user)
):
    repository.default(db, int(user["sub"]))
    row = Workspace(user_id=int(user["sub"]), **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{workspace_id}")
def get_workspace(workspace=Depends(owned)):
    return workspace


@router.patch("/{workspace_id}")
def update_workspace(
    payload: WorkspaceWrite, workspace=Depends(owned), db: Session = Depends(get_db)
):
    for key, value in payload.model_dump().items():
        setattr(workspace, key, value)
    repository.event(db, workspace.id, "updated", "Workspace updated")
    db.commit()
    db.refresh(workspace)
    return workspace


@router.delete("/{workspace_id}")
def archive_workspace(workspace=Depends(owned), db: Session = Depends(get_db)):
    if workspace.is_default:
        raise HTTPException(409, "The default workspace cannot be archived")
    workspace.archived = True
    db.commit()
    return {"archived": True}


@router.get("/{workspace_id}/dashboard")
def dashboard(workspace=Depends(owned), db: Session = Depends(get_db)):
    data = service.dashboard(db, workspace.id)
    data["recent_documents"] = [document_payload(row) for row in data["recent_documents"]]
    return {"workspace": workspace, **data}


@router.post("/{workspace_id}/summary/refresh")
async def refresh_summary(workspace=Depends(owned), db: Session = Depends(get_db)):
    data = await service.refresh_summary(db, workspace.id, workspace.name)
    data["recent_documents"] = [document_payload(row) for row in data["recent_documents"]]
    return data


@router.get("/{workspace_id}/documents")
def documents(workspace=Depends(owned), db: Session = Depends(get_db)):
    return [document_payload(row) for row in repository.documents(db, workspace.id)]


def document_payload(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "session_id",
            "filename",
            "content_type",
            "file_size",
            "kind",
            "processing_status",
            "processing_stage",
            "processing_error",
            "created_at",
        )
    }


@router.post("/{workspace_id}/upload", status_code=201)
@router.post("/{workspace_id}/documents", status_code=201)
async def upload(
    file: UploadFile = File(...), workspace=Depends(owned), db: Session = Depends(get_db)
):
    from app.api.chat_documents import MAX_CHAT_DOCUMENT_BYTES, upload_document

    data = await file.read(MAX_CHAT_DOCUMENT_BYTES + 1)
    if len(data) > MAX_CHAT_DOCUMENT_BYTES:
        await file.close()
        raise HTTPException(413, "Document is too large. Maximum size is 15 MB.")
    checksum = hashlib.sha256(data).hexdigest()
    duplicate = (
        db.query(WorkspaceDocument).filter_by(workspace_id=workspace.id, checksum=checksum).first()
    )
    if duplicate is None:
        # Migrated external blobs and repeated legacy uploads have nullable
        # checksums. Check those bytes too, without copying or changing them.
        unchecked = (
            db.query(WorkspaceDocument).filter_by(workspace_id=workspace.id, checksum=None).all()
        )
        for membership in unchecked:
            existing = repository.document(db, workspace.id, int(membership.document_id))
            if hashlib.sha256(bytes(existing.file_data or b"")).hexdigest() == checksum:
                duplicate = membership
                break
    if duplicate is not None:
        await file.close()
        raise HTTPException(409, "This document is already in the workspace")
    # Reuse the existing upload's type/container/size validation, extraction,
    # optional background queue and blob storage. No provider analysis needed.
    replay = UploadFile(BytesIO(data), filename=file.filename, headers=file.headers)
    await file.close()
    result = await upload_document(
        file=replay,
        message=None,
        session_id=None,
        analyze=False,
        db=db,
        current_user={"sub": str(workspace.user_id)},
    )
    document_id = result.attachment.id
    db.add(WorkspaceDocument(workspace_id=workspace.id, document_id=document_id, checksum=checksum))
    repository.event(db, workspace.id, "document_uploaded", str(result.attachment.filename))
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "This document is already in the workspace") from error
    return document_payload(repository.document(db, workspace.id, document_id))


@router.get("/{workspace_id}/documents/{document_id}/content")
def content(document_id: int, workspace=Depends(owned), db: Session = Depends(get_db)):
    document = repository.document(db, workspace.id, document_id)
    return {
        "id": document.id,
        "filename": document.filename,
        "text": str(document.raw_text or "")[:100000],
        "processing_status": document.processing_status,
    }


@router.delete("/{workspace_id}/documents/{document_id}")
def delete_document(document_id: int, workspace=Depends(owned), db: Session = Depends(get_db)):
    document = repository.document(db, workspace.id, document_id)
    links = db.query(WorkspaceSession.id).filter_by(workspace_id=workspace.id)
    db.query(WorkspaceSessionDocument).filter(
        WorkspaceSessionDocument.workspace_session_id.in_(links),
        WorkspaceSessionDocument.document_id == document_id,
    ).delete(synchronize_session=False)
    db.query(WorkspaceDocument).filter_by(
        workspace_id=workspace.id, document_id=document_id
    ).delete()
    db.query(WorkspaceBookmark).filter(
        WorkspaceBookmark.workspace_id == workspace.id,
        WorkspaceBookmark.target_id == document_id,
        WorkspaceBookmark.target_type.in_(["document", "artifact"]),
    ).delete(synchronize_session=False)
    repository.event(db, workspace.id, "document_removed", str(document.filename))
    db.commit()
    return {"deleted": True}


@router.get("/{workspace_id}/conversations")
def conversations(workspace=Depends(owned), db: Session = Depends(get_db)):
    return (
        db.query(ChatSession)
        .join(WorkspaceSession, WorkspaceSession.session_id == ChatSession.id)
        .filter(WorkspaceSession.workspace_id == workspace.id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )


@router.post("/{workspace_id}/conversations", status_code=201)
def start_conversation(
    payload: ConversationWrite, workspace=Depends(owned), db: Session = Depends(get_db)
):
    session = ChatRepository().create_session(db, workspace.user_id, payload.title)
    db.add(WorkspaceSession(workspace_id=workspace.id, session_id=session.id))
    repository.event(db, workspace.id, "conversation_started", payload.title)
    db.commit()
    return {"id": session.id, "title": session.title, "retrieval_mode": "all", "document_ids": []}


@router.get("/{workspace_id}/conversations/{session_id}")
def restore(session_id: int, workspace=Depends(owned), db: Session = Depends(get_db)):
    link, session = repository.conversation(db, workspace.id, session_id)
    detail = ChatSessionDetailOut.model_validate(session).model_dump()
    return {
        **detail,
        "retrieval_mode": link.retrieval_mode,
        "document_ids": [
            r.document_id
            for r in db.query(WorkspaceSessionDocument).filter_by(workspace_session_id=link.id)
        ],
    }


@router.patch("/{workspace_id}/conversations/{session_id}")
def rename(
    session_id: int,
    payload: ConversationWrite,
    workspace=Depends(owned),
    db: Session = Depends(get_db),
):
    _, session = repository.conversation(db, workspace.id, session_id)
    setattr(session, "title", payload.title)
    repository.event(db, workspace.id, "conversation_renamed", payload.title)
    db.commit()
    return {"id": session_id, "title": payload.title}


@router.delete("/{workspace_id}/conversations/{session_id}")
def delete_conversation(session_id: int, workspace=Depends(owned), db: Session = Depends(get_db)):
    link, session = repository.conversation(db, workspace.id, session_id)
    db.query(WorkspaceSessionDocument).filter_by(workspace_session_id=link.id).delete()
    # Remove workspace membership; preserve source files/history referenced by
    # library or another workspace. They remain accessible in ordinary chat.
    db.delete(link)
    db.query(WorkspaceBookmark).filter_by(
        workspace_id=workspace.id, target_type="conversation", target_id=session_id
    ).delete()
    answer_ids = db.query(ChatMessageRecord.id).filter_by(session_id=session_id)
    db.query(WorkspaceBookmark).filter(
        WorkspaceBookmark.workspace_id == workspace.id,
        WorkspaceBookmark.target_type == "answer",
        WorkspaceBookmark.target_id.in_(answer_ids),
    ).delete(synchronize_session=False)
    repository.event(db, workspace.id, "conversation_removed", str(session.title))
    db.commit()
    return {"deleted": True}


@router.patch("/{workspace_id}/conversations/{session_id}/mode")
def mode(
    session_id: int, payload: ScopeWrite, workspace=Depends(owned), db: Session = Depends(get_db)
):
    link, _ = repository.conversation(db, workspace.id, session_id)
    setattr(link, "retrieval_mode", payload.mode)
    repository.event(db, workspace.id, "scope_changed", "Document scope: " + payload.mode)
    db.commit()
    return {"retrieval_mode": payload.mode}


@router.post("/{workspace_id}/conversations/{session_id}/documents/{document_id}")
def attach(
    session_id: int, document_id: int, workspace=Depends(owned), db: Session = Depends(get_db)
):
    link, _ = repository.conversation(db, workspace.id, session_id)
    repository.document(db, workspace.id, document_id)
    if (
        not db.query(WorkspaceSessionDocument)
        .filter_by(workspace_session_id=link.id, document_id=document_id)
        .first()
    ):
        db.add(WorkspaceSessionDocument(workspace_session_id=link.id, document_id=document_id))
        repository.event(
            db, workspace.id, "document_attached", "Document selected for conversation"
        )
        db.commit()
    return {"attached": True}


@router.delete("/{workspace_id}/conversations/{session_id}/documents/{document_id}")
def detach(
    session_id: int, document_id: int, workspace=Depends(owned), db: Session = Depends(get_db)
):
    link, _ = repository.conversation(db, workspace.id, session_id)
    repository.document(db, workspace.id, document_id)
    db.query(WorkspaceSessionDocument).filter_by(
        workspace_session_id=link.id, document_id=document_id
    ).delete()
    db.commit()
    return {"detached": True}


@router.get("/{workspace_id}/bookmarks")
def bookmarks(workspace=Depends(owned), db: Session = Depends(get_db)):
    return (
        db.query(WorkspaceBookmark)
        .filter_by(workspace_id=workspace.id)
        .order_by(WorkspaceBookmark.id.desc())
        .all()
    )


@router.post("/{workspace_id}/bookmarks", status_code=201)
def bookmark(payload: BookmarkWrite, workspace=Depends(owned), db: Session = Depends(get_db)):
    if payload.target_type in {"document", "artifact"}:
        repository.document(db, workspace.id, payload.target_id)
    elif payload.target_type == "conversation":
        repository.conversation(db, workspace.id, payload.target_id)
    else:
        message = (
            db.query(ChatMessageRecord)
            .filter_by(id=payload.target_id, sender="bot", is_internal=False)
            .first()
        )
        if message is None:
            raise HTTPException(404, "Answer not found")
        repository.conversation(db, workspace.id, int(message.session_id))
    row = WorkspaceBookmark(workspace_id=workspace.id, **payload.model_dump())
    db.add(row)
    repository.event(db, workspace.id, "bookmarked", payload.note or payload.target_type)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{workspace_id}/bookmarks/{bookmark_id}")
def delete_bookmark(bookmark_id: int, workspace=Depends(owned), db: Session = Depends(get_db)):
    row = db.query(WorkspaceBookmark).filter_by(id=bookmark_id, workspace_id=workspace.id).first()
    if row is None:
        raise HTTPException(404, "Bookmark not found")
    db.delete(row)
    db.commit()
    return {"deleted": True}


@router.get("/{workspace_id}/timeline")
def timeline(workspace=Depends(owned), db: Session = Depends(get_db)):
    return (
        db.query(WorkspaceTimeline)
        .filter_by(workspace_id=workspace.id)
        .order_by(WorkspaceTimeline.id.desc())
        .limit(100)
        .all()
    )


@router.get("/{workspace_id}/search")
def search(
    q: str = Query(min_length=1, max_length=200),
    workspace=Depends(owned),
    db: Session = Depends(get_db),
):
    return {"query": q, "results": service.search(db, workspace.id, workspace.user_id, q)}


@router.get("/{workspace_id}/related")
def related(
    q: str = Query(default="", max_length=200),
    workspace=Depends(owned),
    db: Session = Depends(get_db),
):
    if q:
        return service.search(db, workspace.id, workspace.user_id, q)
    data = service.dashboard(db, workspace.id)
    return {
        "documents": [document_payload(row) for row in data["recent_documents"]],
        "conversations": data["recent_conversations"],
    }


@router.get("/{workspace_id}/conversations/{session_id}/export")
def export(
    session_id: int,
    format: Literal["markdown", "pdf", "word"] = "markdown",
    workspace=Depends(owned),
    db: Session = Depends(get_db),
):
    _, session = repository.conversation(db, workspace.id, session_id)
    result = service.export(db, session, format)
    repository.event(db, workspace.id, "conversation_exported", str(session.title))
    db.commit()
    return Response(
        result.file_data,
        media_type=result.content_type,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(result.filename, safe=""),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{workspace_id}/conversations/{session_id}/save-as-knowledge", status_code=201)
def save_knowledge(session_id: int, workspace=Depends(owned), db: Session = Depends(get_db)):
    _, session = repository.conversation(db, workspace.id, session_id)
    text = service.transcript(db, session)
    checksum = hashlib.sha256(text.encode()).hexdigest()
    duplicate = (
        db.query(WorkspaceDocument).filter_by(workspace_id=workspace.id, checksum=checksum).first()
    )
    if duplicate:
        raise HTTPException(409, "This conversation is already saved as knowledge")
    knowledge_session = ChatRepository().create_session(
        db, workspace.user_id, "Knowledge: " + str(session.title)[:240]
    )
    _, attachment = ChatRepository().save_document_upload(
        db,
        session_id=int(knowledge_session.id),
        user_content="Saved conversation as knowledge",
        bot_content="Conversation saved as searchable knowledge.",
        filename=f"conversation-{session_id}.md",
        content_type="text/markdown",
        file_data=text.encode(),
        raw_text=text,
    )
    membership = (
        db.query(WorkspaceDocument)
        .filter_by(workspace_id=workspace.id, document_id=attachment.id)
        .first()
    )
    if membership is None:
        membership = WorkspaceDocument(workspace_id=workspace.id, document_id=attachment.id)
        db.add(membership)
    setattr(membership, "checksum", checksum)
    repository.event(db, workspace.id, "knowledge_saved", str(session.title))
    db.commit()
    from app.services.document.document_operation_registry import DocumentType
    from app.services.document.extraction_models import ExtractedDocument
    from app.services.document.semantic_retrieval import index_document

    index_document(
        user_id=workspace.user_id,
        session_id=int(knowledge_session.id),
        document_id=int(attachment.id),
        filename=str(attachment.filename),
        extracted=ExtractedDocument(document_type=DocumentType.MARKDOWN, text=text),
    )
    return {"document_id": attachment.id}
