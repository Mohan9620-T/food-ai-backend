from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from docx import Document
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from app.services.chat_document_service import (
    ChatDocumentService,
    DocumentProcessingUnavailableError,
)
from app.services.chat_service import ChatModelUnavailableError


def _login(client, email: str) -> str:
    client.post("/users/", json={"fullname": "Doc User", "email": email, "password": "secret123"})
    return client.post("/users/login", json={"email": email, "password": "secret123"}).json()[
        "access_token"
    ]


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
    monkeypatch.setattr(
        ChatDocumentService, "summarize", AsyncMock(return_value="Structured nutrition summary")
    )
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
    history = client.get(
        f"/chat/sessions/{body['session_id']}", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert history["messages"][0]["document_attachment"]["filename"] == "notes.txt"


def test_document_upload_rejects_invalid_and_empty_files(client):
    token = _login(client, "document-validation@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    invalid = client.post(
        "/chat/documents",
        files={"file": ("bad.exe", b"x", "application/octet-stream")},
        headers=headers,
    )
    empty = client.post(
        "/chat/documents", files={"file": ("empty.txt", b"", "text/plain")}, headers=headers
    )
    assert invalid.status_code == 415
    assert empty.status_code == 422


def test_generate_and_download_enforces_ownership(client, monkeypatch):
    first_token = _login(client, "document-owner@example.com")
    first_headers = {"Authorization": f"Bearer {first_token}"}
    session_id = client.post(
        "/chat/sessions", json={"title": "Documents"}, headers=first_headers
    ).json()["id"]
    monkeypatch.setattr(
        ChatDocumentService,
        "generate_content",
        AsyncMock(return_value="# Doctor Summary\nBalanced nutrition plan"),
    )
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
    denied = client.get(
        f"/chat/documents/{document_id}/download",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert denied.status_code == 404


def _document_bytes(extension):
    content = "Product details: oats 100"
    buffer = BytesIO()
    if extension == "pdf":
        canvas = Canvas(buffer)
        canvas.drawString(72, 750, content)
        canvas.save()
    elif extension == "docx":
        document = Document()
        document.add_heading("Product details", level=1)
        document.add_paragraph(content)
        document.save(buffer)
    elif extension == "xlsx":
        workbook = Workbook()
        workbook.active.append([content])
        workbook.save(buffer)
        workbook.close()
    else:
        return content.encode("utf-8")
    return buffer.getvalue()


@pytest.mark.parametrize("extension", ["pdf", "docx", "txt", "csv", "xlsx"])
def test_all_supported_uploads_can_be_reloaded_and_downloaded(client, monkeypatch, extension):
    token = _login(client, f"upload-{extension}@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    captured = []

    async def summarize(self, raw, note):
        captured.append((raw, note))
        return "# Product details\nOats: 100"

    monkeypatch.setattr(ChatDocumentService, "summarize", summarize)
    file_data = _document_bytes(extension)
    result = client.post(
        "/chat/documents",
        files={"file": (f"REPORT.{extension.upper()}", file_data, "application/octet-stream")},
        data={"message": "Show the headings and details"},
        headers=headers,
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert "oats 100" in captured[0][0]
    assert captured[0][1] == "Show the headings and details"
    reloaded = client.get(f"/chat/sessions/{body['session_id']}", headers=headers).json()
    assert len(reloaded["messages"]) == 2
    assert reloaded["messages"][0]["document_attachment"]["id"] == body["attachment"]["id"]
    download = client.get(f"/chat/documents/{body['attachment']['id']}/download", headers=headers)
    assert download.content == file_data


@pytest.mark.parametrize("mime", ["application/vnd.ms-excel", "text/plain", "text/csv"])
def test_csv_accepts_windows_browser_mime_types(client, monkeypatch, mime):
    token = _login(client, "csv-mime@example.com")
    monkeypatch.setattr(ChatDocumentService, "summarize", AsyncMock(return_value="Oats 100"))
    response = client.post(
        "/chat/documents",
        files={"file": ("data.csv", b"name,value\noats,100", mime)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_xlsx_formatting_instruction_returns_downloadable_updated_workbook_without_ai(
    client, monkeypatch
):
    token = _login(client, "spreadsheet-format@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    workbook = Workbook()
    first = workbook.active
    first.title = "Sales"
    first.append(["Item", "Amount"])
    first.append(["Oats", 100])
    first.append(["Rice", 250])
    first["B4"] = "=SUM(B2:B3)"
    second = workbook.create_sheet("Notes")
    second.append(["Status", "Comment"])
    second.append(["Ready", "This is a long note that should be wrapped"])
    source = BytesIO()
    workbook.save(source)
    workbook.close()

    monkeypatch.setattr(
        ChatDocumentService,
        "summarize",
        AsyncMock(side_effect=AssertionError("Spreadsheet formatting must not call AI")),
    )
    response = client.post(
        "/chat/documents",
        files={
            "file": (
                "sales.xlsx",
                source.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={
            "message": "Center align all cells, style the headers, auto fit columns, and wrap text"
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis_status"] == "skipped"
    assert body["attachment"]["kind"] == "generated"
    assert body["attachment"]["filename"] == "sales-updated.xlsx"
    assert "preserved the workbook data" in body["response"]
    download = client.get(f"/chat/documents/{body['attachment']['id']}/download", headers=headers)
    revised = load_workbook(BytesIO(download.content), data_only=False)
    try:
        assert revised.sheetnames == ["Sales", "Notes"]
        assert revised["Sales"]["A2"].value == "Oats"
        assert revised["Sales"]["B3"].value == 250
        assert revised["Sales"]["B4"].value == "=SUM(B2:B3)"
        assert revised["Sales"]["A2"].alignment.horizontal == "center"
        assert revised["Notes"]["B2"].alignment.wrap_text is True
        assert revised["Sales"]["A1"].font.bold is True
        assert revised["Sales"]["A1"].fill.fgColor.rgb == "001F4E78"
        assert revised["Sales"].column_dimensions["A"].width >= 10
    finally:
        revised.close()

    history = client.get(f"/chat/sessions/{body['session_id']}", headers=headers).json()
    assert history["messages"][0]["document_attachment"]["kind"] == "uploaded"
    assert history["messages"][1]["document_attachment"]["kind"] == "generated"


def test_missing_ocr_is_not_reported_as_corrupt_document(client, monkeypatch):
    token = _login(client, "ocr-unavailable@example.com")

    def unavailable(*args):
        raise DocumentProcessingUnavailableError(
            "Scanned PDFs require Tesseract OCR on the backend."
        )

    monkeypatch.setattr(ChatDocumentService, "extract", unavailable)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/chat/documents",
        files={"file": ("scan.pdf", b"scan", "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 503
    assert "Tesseract" in response.json()["detail"]
    assert client.get("/chat/sessions", headers=headers).json() == []


@pytest.mark.parametrize("output_format", ["pdf", "docx"])
def test_generates_document_from_instruction_in_new_chat(client, monkeypatch, output_format):
    token = _login(client, f"new-{output_format}@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    captured = []

    async def generate(self, instruction, summaries, history, profile=None):
        captured.append((instruction, summaries, history))
        return "# Project Summary\nFirst task: import a file.\nUse <label> & <value> safely."

    monkeypatch.setattr(ChatDocumentService, "generate_content", generate)
    response = client.post(
        "/chat/documents/generate",
        json={
            "session_id": None,
            "instruction": "  Create a project summary  ",
            "output_format": output_format,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert captured[0] == ("Create a project summary", [], [])
    body = response.json()
    downloaded = client.get(f"/chat/documents/{body['attachment']['id']}/download", headers=headers)
    if output_format == "pdf":
        text = "".join(page.extract_text() for page in PdfReader(BytesIO(downloaded.content)).pages)
    else:
        text = "\n".join(p.text for p in Document(BytesIO(downloaded.content)).paragraphs)
    assert "Project Summary" in text
    assert "<label> & <value>" in text
    history = client.get(f"/chat/sessions/{body['session_id']}", headers=headers).json()
    assert history["messages"][1]["document_attachment"]["kind"] == "generated"


def test_generate_rejects_empty_instruction_and_does_not_create_failed_sessions(
    client, monkeypatch
):
    token = _login(client, "failed-generation@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    invalid = client.post("/chat/documents/generate", json={"instruction": "   "}, headers=headers)
    assert invalid.status_code == 422

    def unavailable(*args):
        raise ChatModelUnavailableError("Model unavailable")

    monkeypatch.setattr(ChatDocumentService, "generate_content", AsyncMock(side_effect=unavailable))
    failed = client.post(
        "/chat/documents/generate", json={"instruction": "Create a summary"}, headers=headers
    )
    assert failed.status_code == 503
    assert client.get("/chat/sessions", headers=headers).json() == []


def test_document_routes_check_session_ownership_before_processing(client, monkeypatch):
    owner = _login(client, "session-owner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner}"}
    session_id = client.post(
        "/chat/sessions", json={"title": "Private"}, headers=owner_headers
    ).json()["id"]
    other = _login(client, "session-other@example.com")
    headers = {"Authorization": f"Bearer {other}"}

    def unexpected(*args):
        raise AssertionError("Do not process another user's session")

    monkeypatch.setattr(ChatDocumentService, "extract", unexpected)
    monkeypatch.setattr(ChatDocumentService, "generate_content", unexpected)
    upload = client.post(
        "/chat/documents",
        files={"file": ("note.txt", b"Note", "text/plain")},
        data={"session_id": str(session_id)},
        headers=headers,
    )
    generate = client.post(
        "/chat/documents/generate",
        json={"session_id": session_id, "instruction": "Summarize"},
        headers=headers,
    )
    assert upload.status_code == generate.status_code == 404
