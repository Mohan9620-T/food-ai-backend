from io import BytesIO

from docx import Document

from app.services.chat_document_service import ChatDocumentService


def _login(client, email: str) -> str:
    client.post("/users/", json={"fullname": "Doc User", "email": email, "password": "secret123"})
    return client.post("/users/login", json={"email": email, "password": "secret123"}).json()["access_token"]


def test_extracts_txt_docx_csv_and_xlsx():
    service = ChatDocumentService()
    assert service.extract(b"Calories: 1800", "notes.txt") == "Calories: 1800"
    assert "protein" in service.extract(b"name,value\nprotein,90", "data.csv")
    document = Document()
    document.add_paragraph("Allergy: peanuts")
    buffer = BytesIO()
    document.save(buffer)
    assert "Allergy: peanuts" in service.extract(buffer.getvalue(), "report.docx")


def test_upload_document_persists_attachment(client, monkeypatch):
    token = _login(client, "document-upload@example.com")
    monkeypatch.setattr(ChatDocumentService, "summarize", lambda self, raw, note: "Structured nutrition summary")
    response = client.post(
        "/chat/documents",
        files={"file": ("notes.txt", b"Calories: 1800", "text/plain")},
        data={"message": "Read my notes"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["attachment"]["filename"] == "notes.txt"
    assert body["attachment"]["kind"] == "uploaded"
    history = client.get(f"/chat/sessions/{body['session_id']}", headers={"Authorization": f"Bearer {token}"}).json()
    assert history["messages"][0]["document_attachment"]["filename"] == "notes.txt"


def test_document_upload_rejects_invalid_and_empty_files(client):
    token = _login(client, "document-validation@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    invalid = client.post("/chat/documents", files={"file": ("bad.exe", b"x", "application/octet-stream")}, headers=headers)
    empty = client.post("/chat/documents", files={"file": ("empty.txt", b"", "text/plain")}, headers=headers)
    assert invalid.status_code == 415
    assert empty.status_code == 422


def test_generate_and_download_enforces_ownership(client, monkeypatch):
    first_token = _login(client, "document-owner@example.com")
    first_headers = {"Authorization": f"Bearer {first_token}"}
    session_id = client.post("/chat/sessions", json={"title": "Documents"}, headers=first_headers).json()["id"]
    monkeypatch.setattr(ChatDocumentService, "generate_content", lambda self, instruction, summaries, history, profile=None: "# Doctor Summary\nBalanced nutrition plan")
    generated = client.post(
        "/chat/documents/generate",
        json={"session_id": session_id, "instruction": "Doctor summary", "output_format": "pdf"},
        headers=first_headers,
    )
    assert generated.status_code == 200
    document_id = generated.json()["attachment"]["id"]
    download = client.get(f"/chat/documents/{document_id}/download", headers=first_headers)
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    second_token = _login(client, "document-stranger@example.com")
    denied = client.get(f"/chat/documents/{document_id}/download", headers={"Authorization": f"Bearer {second_token}"})
    assert denied.status_code == 404
