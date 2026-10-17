"""Synchronous, owner-scoped workspace queries shared by HTTP and chat."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models.chat import ChatDocumentAttachment, ChatSession
from app.models.workspace import (
    Workspace,
    WorkspaceDocument,
    WorkspaceSession,
    WorkspaceSessionDocument,
    WorkspaceTimeline,
)


class WorkspaceRepository:
    def require(self, db: Session, workspace_id: int, user_id: int) -> Workspace:
        row = (
            db.query(Workspace).filter_by(id=workspace_id, user_id=user_id, archived=False).first()
        )
        if row is None:
            raise HTTPException(404, "Workspace not found")
        return row

    def default(self, db: Session, user_id: int) -> Workspace:
        row = db.query(Workspace).filter_by(user_id=user_id, is_default=True).first()
        if row is None:
            row = Workspace(user_id=user_id, name="My Workspace", is_default=True)
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    def conversation(
        self, db: Session, workspace_id: int, session_id: int
    ) -> tuple[WorkspaceSession, ChatSession]:
        result = (
            db.query(WorkspaceSession, ChatSession)
            .join(ChatSession, ChatSession.id == WorkspaceSession.session_id)
            .filter(
                WorkspaceSession.workspace_id == workspace_id,
                WorkspaceSession.session_id == session_id,
            )
            .first()
        )
        if result is None:
            raise HTTPException(404, "Conversation not found")
        return result[0], result[1]

    def documents(self, db: Session, workspace_id: int) -> list[ChatDocumentAttachment]:
        return (
            db.query(ChatDocumentAttachment)
            .join(WorkspaceDocument, WorkspaceDocument.document_id == ChatDocumentAttachment.id)
            .filter(WorkspaceDocument.workspace_id == workspace_id)
            .order_by(ChatDocumentAttachment.id.desc())
            .all()
        )

    def document(self, db: Session, workspace_id: int, document_id: int) -> ChatDocumentAttachment:
        row = (
            db.query(ChatDocumentAttachment)
            .join(WorkspaceDocument, WorkspaceDocument.document_id == ChatDocumentAttachment.id)
            .filter(
                WorkspaceDocument.workspace_id == workspace_id,
                WorkspaceDocument.document_id == document_id,
            )
            .first()
        )
        if row is None:
            raise HTTPException(404, "Document not found")
        return row

    def event(self, db: Session, workspace_id: int, kind: str, title: str) -> None:
        db.add(WorkspaceTimeline(workspace_id=workspace_id, event_type=kind, title=title[:300]))


def scoped_documents(db: Session, session_id: int) -> list[ChatDocumentAttachment] | None:
    """None preserves legacy chat; [] is an explicitly empty workspace scope."""
    if not settings.ENABLE_WORKSPACES:
        return None
    link = db.query(WorkspaceSession).filter_by(session_id=session_id).first()
    if link is None:
        return None
    documents = WorkspaceRepository().documents(db, int(link.workspace_id))
    if link.retrieval_mode == "selected":
        selected = {
            row.document_id
            for row in db.query(WorkspaceSessionDocument).filter_by(workspace_session_id=link.id)
        }
        documents = [row for row in documents if row.id in selected]
    return documents


def link_document(db: Session, document: ChatDocumentAttachment) -> None:
    if not settings.ENABLE_WORKSPACES:
        return
    link = db.query(WorkspaceSession).filter_by(session_id=document.session_id).first()
    if (
        link is not None
        and not db.query(WorkspaceDocument)
        .filter_by(workspace_id=link.workspace_id, document_id=document.id)
        .first()
    ):
        db.add(WorkspaceDocument(workspace_id=link.workspace_id, document_id=document.id))
        WorkspaceRepository().event(
            db, int(link.workspace_id), "document_created", str(document.filename)
        )
