"""Workspace lifecycle, isolation and live retrieval scope; all providers mocked."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.main import app
from app.models.chat import ChatDocumentAttachment, ChatMessageRecord, ChatSession
from app.models.user import User
from app.repositories.chat_repository import ChatRepository
from app.repositories.workspace_repository import scoped_documents
from app.services import workspace_service
from app.services.chat_document_service import ChatDocumentService
from app.services.document.phase4_turn import prepare_turn
from app.utils.auth_dependency import get_current_user


@pytest.fixture
def workspace_client(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WORKSPACES", True)
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", False)
    monkeypatch.setattr(settings, "ENABLE_BACKGROUND_DOCUMENT_PROCESSING", False)
    monkeypatch.setattr(settings, "ENABLE_FILE_CLARIFY", False)
    db_session.add_all(
        [
            User(id=101, fullname="One", email="one@example.com", password="unused"),
            User(id=102, fullname="Two", email="two@example.com", password="unused"),
        ]
    )
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: {"sub": "101"}
    monkeypatch.setattr(
        ChatDocumentService, "_complete", AsyncMock(return_value="Workspace summary.")
    )
    return client


def create(client, name="Research"):
    response = client.post("/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, wid, text=b"Apples contain fibre. Bananas contain potassium.", name="notes.txt"):
    response = client.post(f"/workspaces/{wid}/upload", files={"file": (name, text, "text/plain")})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def start(client, wid):
    response = client.post(f"/workspaces/{wid}/conversations", json={"title": "Nutrition"})
    assert response.status_code == 201
    return response.json()["id"]


def test_default_and_crud(workspace_client):
    c = workspace_client
    rows = c.get("/workspaces").json()
    assert len(rows) == 1 and rows[0]["name"] == "My Workspace"
    assert c.delete(f"/workspaces/{rows[0]['id']}").status_code == 409
    wid = create(c)
    assert c.get(f"/workspaces/{wid}").json()["name"] == "Research"
    assert (
        c.patch(
            f"/workspaces/{wid}", json={"name": "Updated", "description": "Details"}
        ).status_code
        == 200
    )
    assert c.get(f"/workspaces/{wid}/dashboard").json()["suggestions"]
    assert c.delete(f"/workspaces/{wid}").status_code == 200
    assert c.get(f"/workspaces/{wid}").status_code == 404
    assert c.post("/workspaces", json={"name": "  "}).status_code == 422


def test_documents_duplicates_preview_and_scope(workspace_client, db_session):
    c = workspace_client
    wid = create(c)
    other = create(c, "Other")
    document = upload(c, wid)
    assert (
        c.post(
            f"/workspaces/{wid}/upload",
            files={
                "file": (
                    "copy.txt",
                    b"Apples contain fibre. Bananas contain potassium.",
                    "text/plain",
                )
            },
        ).status_code
        == 409
    )
    upload(c, other)
    assert c.get(f"/workspaces/{wid}/documents").json()[0]["id"] == document
    assert "Apples" in c.get(f"/workspaces/{wid}/documents/{document}/content").json()["text"]
    sid = start(c, wid)
    repo = ChatRepository()
    assert [r.id for r in scoped_documents(db_session, sid)] == [document]
    assert repo.get_documents_for_user(db_session, sid, 102) == []
    assert repo.get_document_contexts(db_session, sid)[0][0] == document
    assert repo.get_document_summaries(db_session, sid)
    assert (
        repo.get_document_by_filename_for_user(
            db_session, session_id=sid, filename="NOTES.TXT", user_id=101
        ).id
        == document
    )
    assert (
        repo.get_document_by_filename_for_user(
            db_session, session_id=sid, filename="NOTES.TXT", user_id=102
        )
        is None
    )
    assert repo.get_latest_document_for_user(db_session, sid, 101).id == document
    path = f"/workspaces/{wid}/conversations/{sid}"
    assert c.patch(path + "/mode", json={"mode": "selected"}).status_code == 200
    assert scoped_documents(db_session, sid) == []
    assert repo.get_latest_document_for_user(db_session, sid, 101) is None
    assert c.post(path + f"/documents/{document}").status_code == 200
    assert c.post(path + f"/documents/{document}").status_code == 200
    assert c.get(path).json()["document_ids"] == [document]
    assert repo.get_document_contexts(db_session, sid)[0][0] == document
    assert c.delete(path + f"/documents/{document}").status_code == 200
    assert scoped_documents(db_session, sid) == []
    assert c.post(path + f"/documents/{document}").status_code == 200
    assert c.delete(f"/workspaces/{wid}/documents/{document}").status_code == 200
    assert c.get(path).json()["document_ids"] == []
    assert c.get(f"/workspaces/{wid}/documents/{document}/content").status_code == 404


def test_conversation_bookmarks_search_knowledge_and_export(workspace_client, db_session):
    c = workspace_client
    wid = create(c)
    did = upload(c, wid)
    sid = start(c, wid)
    user, answer = ChatRepository().add_turn(
        db_session,
        sid,
        "**Apples** < 3 & bananas",
        "# Fibre\nApples contain fibre. [Info](https://example.org)",
    )
    path = f"/workspaces/{wid}/conversations/{sid}"
    for target_type, target in [
        ("answer", answer.id),
        ("document", did),
        ("artifact", did),
        ("conversation", sid),
    ]:
        response = c.post(
            f"/workspaces/{wid}/bookmarks",
            json={"target_type": target_type, "target_id": target, "note": "Useful"},
        )
        assert response.status_code == 201, response.text
    assert len(c.get(f"/workspaces/{wid}/bookmarks").json()) == 4
    bookmark = c.get(f"/workspaces/{wid}/bookmarks").json()[0]["id"]
    assert c.delete(f"/workspaces/{wid}/bookmarks/{bookmark}").status_code == 200
    assert c.delete(f"/workspaces/{wid}/bookmarks/{bookmark}").status_code == 404
    results = c.get(f"/workspaces/{wid}/search?q=Apples").json()["results"]
    assert results["documents"] and results["messages"]
    assert c.get(f"/workspaces/{wid}/search?q=Nutrition").json()["results"]["conversations"]
    assert c.get(f"/workspaces/{wid}/related").json()["documents"]
    assert c.get(f"/workspaces/{wid}/related?q=Apples").json()["documents"]
    for fmt, magic in [("markdown", b"#"), ("pdf", b"%PDF"), ("word", b"PK")]:
        response = c.get(path + f"/export?format={fmt}")
        assert response.status_code == 200, response.text
        assert response.content.startswith(magic)
    assert c.get(path + "/export?format=bad").status_code == 422
    assert c.post(path + "/save-as-knowledge").status_code == 201
    assert c.get(f"/workspaces/{wid}/search?q=Fibre").json()["results"]["documents"]
    assert c.patch(path, json={"title": "Renamed"}).status_code == 200
    assert c.get(f"/workspaces/{wid}/conversations").json()[0]["title"] == "Renamed"
    assert c.get(f"/workspaces/{wid}/timeline").json()
    assert c.delete(path).status_code == 200
    assert c.get(path).status_code == 404
    assert db_session.get(ChatSession, sid) is not None


@pytest.mark.parametrize(
    "path,method,body",
    [
        ("", "get", None),
        ("/dashboard", "get", None),
        ("/documents", "get", None),
        ("/timeline", "get", None),
        ("/search?q=a", "get", None),
        ("/related", "get", None),
        ("/summary/refresh", "post", {}),
        ("/conversations", "post", {"title": "private"}),
        ("/bookmarks", "get", None),
    ],
)
def test_owner_isolation(workspace_client, path, method, body):
    c = workspace_client
    wid = create(c)
    app.dependency_overrides[get_current_user] = lambda: {"sub": "102"}
    assert (
        getattr(c, method)(
            f"/workspaces/{wid}{path}", **({"json": body} if body is not None else {})
        ).status_code
        == 404
    )


def test_foreign_resource_membership(workspace_client, db_session):
    c = workspace_client
    wid = create(c)
    other = create(c, "Elsewhere")
    did = upload(c, other)
    sid = start(c, wid)
    assert c.post(f"/workspaces/{wid}/conversations/{sid}/documents/{did}").status_code == 404
    assert c.get(f"/workspaces/{wid}/conversations/999").status_code == 404
    for typ, target in [("document", did), ("answer", 999), ("conversation", 999)]:
        assert (
            c.post(
                f"/workspaces/{wid}/bookmarks", json={"target_type": typ, "target_id": target}
            ).status_code
            == 404
        )


def test_summary_failure_preserves_cache(workspace_client, monkeypatch):
    c = workspace_client
    wid = create(c)
    assert c.post(f"/workspaces/{wid}/summary/refresh").json()["summary_available"]
    assert c.get(f"/workspaces/{wid}/dashboard").json()["summary"] == "Workspace summary."
    monkeypatch.setattr(
        ChatDocumentService, "_complete", AsyncMock(side_effect=RuntimeError("offline"))
    )
    response = c.post(f"/workspaces/{wid}/summary/refresh").json()
    assert response["summary"] == "Workspace summary." and not response["summary_available"]


def test_disabled_routes_and_scope(workspace_client, db_session, monkeypatch):
    c = workspace_client
    wid = create(c)
    sid = start(c, wid)
    monkeypatch.setattr(settings, "ENABLE_WORKSPACES", False)
    assert c.get("/workspaces").status_code == 404
    assert c.get(f"/workspaces/{wid}/dashboard").status_code == 404
    assert scoped_documents(db_session, sid) is None
    assert not c.get("/library/capabilities").json()["workspaces"]


@pytest.mark.parametrize(
    "filename,data,mime,code",
    [
        ("bad.exe", b"bad", "text/plain", 415),
        ("empty.txt", b"", "text/plain", 422),
        ("big.txt", b"x" * (15 * 1024 * 1024 + 1), "text/plain", 413),
    ],
    ids=["unsupported", "empty", "too-large"],
)
def test_upload_validation(workspace_client, filename, data, mime, code):
    wid = create(workspace_client)
    assert (
        workspace_client.post(
            f"/workspaces/{wid}/upload", files={"file": (filename, data, mime)}
        ).status_code
        == code
    )


def test_empty_scope_next_question_keeps_history(workspace_client, db_session):
    c = workspace_client
    wid = create(c)
    sid = start(c, wid)
    ChatRepository().add_turn(db_session, sid, "Previous", "Earlier answer")
    c.patch(f"/workspaces/{wid}/conversations/{sid}/mode", json={"mode": "selected"})
    response = asyncio.run(prepare_turn(db_session, 101, sid, "Summarize this document"))
    assert "No documents" in response["response"]
    assert db_session.query(ChatMessageRecord).filter_by(session_id=sid).count() == 4


def test_search_semantic_generated(workspace_client, db_session, monkeypatch):
    from types import SimpleNamespace

    c = workspace_client
    wid = create(c)
    did = upload(c, wid)
    document = db_session.get(ChatDocumentAttachment, did)
    document.kind = "generated"
    db_session.commit()
    fake = SimpleNamespace(document_id=did, filename="notes.txt", text="semantic apples")
    monkeypatch.setattr(
        workspace_service.semantic_retrieval, "retrieve_relevant_chunks", lambda **kwargs: [fake]
    )
    results = c.get(f"/workspaces/{wid}/search?q=Apples").json()["results"]
    assert results["generated"] and results["chunks"]


def test_plain_export_text():
    assert (
        workspace_service.plain("# Title\n**bold** [link](url) `code`") == "Title\nbold link code"
    )


@pytest.mark.parametrize("endpoint", ["/chat/", "/chat/stream"])
def test_scope_change_applies_to_both_transports(workspace_client, db_session, endpoint):
    c = workspace_client
    wid = create(c)
    sid = start(c, wid)
    upload(c, wid)
    ChatRepository().add_turn(db_session, sid, "Earlier question", "Earlier answer")
    c.patch(f"/workspaces/{wid}/conversations/{sid}/mode", json={"mode": "selected"})
    response = c.post(endpoint + f"?session_id={sid}", json={"message": "Summarize the document"})
    assert response.status_code == 200
    assert "No documents" in response.text
    assert db_session.query(ChatMessageRecord).filter_by(session_id=sid).count() == 4


def test_knowledge_is_idempotent_and_preserves_history(workspace_client, db_session):
    c = workspace_client
    wid = create(c)
    sid = start(c, wid)
    ChatRepository().add_turn(db_session, sid, "Question", "Answer")
    path = f"/workspaces/{wid}/conversations/{sid}/save-as-knowledge"
    assert c.post(path).status_code == 201
    assert c.post(path).status_code == 409
    assert db_session.query(ChatMessageRecord).filter_by(session_id=sid).count() == 2


def test_generated_documents_join_workspace(workspace_client, db_session):
    c = workspace_client
    wid = create(c)
    sid = start(c, wid)
    _, attachment = ChatRepository().save_generated_document(
        db_session,
        session_id=sid,
        bot_content="Ready",
        filename="output.txt",
        content_type="text/plain",
        file_data=b"Generated content",
        raw_text="Generated content",
    )
    assert c.get(f"/workspaces/{wid}/documents").json()[0]["id"] == attachment.id
    assert c.get(f"/workspaces/{wid}/search?q=Generated").json()["results"]["generated"]


def test_cross_conversation_rag_uses_original_metadata(workspace_client, db_session, monkeypatch):
    from app.api.chat import _document_reference_history
    from app.services.document import citations, semantic_retrieval

    c = workspace_client
    wid = create(c)
    did = upload(c, wid)
    sid = start(c, wid)
    source = db_session.get(ChatDocumentAttachment, did)
    calls = []

    def retrieve(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", True)
    monkeypatch.setattr(semantic_retrieval, "retrieve_relevant_chunks", retrieve)
    monkeypatch.setattr(citations, "retrieve_relevant_chunks", retrieve)
    assert _document_reference_history(db_session, 101, sid, "Summarize the document")
    assert citations.excerpts([source], 101, sid, "Apples")
    assert all(
        call["session_id"] == source.session_id and call["document_ids"] == [did] for call in calls
    )
