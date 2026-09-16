from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import chat_documents
from app.models.chat import ChatDocumentAttachment, ChatSession
from app.repositories.chat_repository import ChatRepository
from app.schemas.vision_result import VisionResult
from app.services.chat_service import ChatModelUnavailableError


@pytest.fixture
def history_headers(client):
    credentials = {"email": "document-history@example.com", "password": "test-password"}
    client.post("/users/", json={**credentials, "fullname": "History Test"})
    token = client.post("/users/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("output_format", ["pdf", "docx", "xlsx", "csv", "pptx", "txt", "markdown"])
def test_generated_formats_persist_complete_data_and_owned_downloads(
    client, history_headers, db_session, output_format
):
    response = client.post(
        "/chat/documents/generate",
        headers=history_headers,
        json={
            "mode": "export",
            "instruction": "# Inventory\n\nApples: 12\nOranges: 7",
            "output_format": output_format,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    doc_id = result["attachment"]["id"]
    db_session.expire_all()
    artifact = db_session.get(ChatDocumentAttachment, doc_id)
    assert "Apples" in artifact.raw_text
    assert "Apples" in artifact.extracted_data["text"]
    assert artifact.generation_metadata["instruction"].startswith("# Inventory")
    history = client.get(f"/chat/sessions/{result['session_id']}", headers=history_headers).json()
    assert history["messages"][-1]["attachments"][0]["id"] == doc_id
    assert (
        client.get(f"/chat/documents/{doc_id}/download", headers=history_headers).content
        == artifact.file_data
    )
    assert (
        client.get(f"/chat/documents/{doc_id}/data", headers=history_headers).json()["raw_text"]
        == artifact.raw_text
    )
    stranger = {"email": f"other-{output_format}@example.com", "password": "test-password"}
    client.post("/users/", json={**stranger, "fullname": "Other User"})
    token = client.post("/users/login", json=stranger).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/chat/documents/{doc_id}/data", headers=other_headers).status_code == 404
    assert (
        client.get(f"/chat/sessions/{result['session_id']}", headers=other_headers).status_code
        == 404
    )


def test_image_extraction_survives_generation_failure(
    client, history_headers, valid_png_bytes, monkeypatch, db_session
):
    provider = MagicMock()
    provider.infer.return_value = VisionResult(
        image_type="text", answer="Visible inventory: Apples 12, Oranges 7"
    )
    monkeypatch.setattr(
        "app.services.document.image_document_reader.get_vision_provider", lambda: provider
    )
    monkeypatch.setattr(
        type(chat_documents.service),
        "generate_content",
        AsyncMock(side_effect=ChatModelUnavailableError("Provider timed out")),
    )
    uploaded = client.post(
        "/chat/documents",
        headers=history_headers,
        data={"analyze": "false"},
        files={"file": ("inventory.png", valid_png_bytes, "image/png")},
    ).json()
    result = client.post(
        "/chat/documents/automate",
        headers=history_headers,
        json={
            "session_id": uploaded["session_id"],
            "source_document_id": uploaded["attachment"]["id"],
            "instruction": "Create an Excel file from this image",
            "confirm": True,
        },
    ).json()
    assert result["status"] == "failed"
    assert result["attachments"] == []
    db_session.expire_all()
    source = db_session.get(ChatDocumentAttachment, uploaded["attachment"]["id"])
    assert "Apples 12" in source.raw_text
    assert source.extracted_data["text"] == source.raw_text
    history = client.get(f"/chat/sessions/{uploaded['session_id']}", headers=history_headers).json()
    assert history["messages"][-1]["automation"]["response"] == result


def test_chat_activity_updates_recent_history_order(client, history_headers, db_session):
    sid = client.post("/chat/sessions", headers=history_headers, json={"title": "Older"}).json()[
        "id"
    ]
    newer_id = client.post(
        "/chat/sessions", headers=history_headers, json={"title": "Newer"}
    ).json()["id"]
    older = db_session.get(ChatSession, sid)
    older.updated_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()
    ChatRepository().add_turn(db_session, sid, "Continue this chat", "Saved reply")
    sessions = client.get("/chat/sessions", headers=history_headers).json()
    assert [session["id"] for session in sessions] == [sid, newer_id]
