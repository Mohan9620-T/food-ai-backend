"""Direct document requests return the requested chat text as well as real files."""

import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from docx import Document
from pypdf import PdfReader

from app.api import chat_documents
from app.services.chat_document_service import ChatDocumentService
from app.services.document.exceptions import InvalidDocumentError
from app.services.document.image_document_reader import ImageDocumentReader

TRANSCRIPTION = "# Testing types\n\nUnit testing: 12 cases\n\nIntegration testing: 8 cases\n\nFinal label: QA-END-731"


def test_image_chat_text_omits_internal_ocr_coordinates_without_losing_labels():
    raw = 'Image OCR lines (left/top are pixel coordinates):\n{"left": 10, "top": 20, "text": "Code: 0012", "confidence": 96}\n{"left": 10, "top": 40, "text": "Amount: 0", "confidence": 91}'
    assert ImageDocumentReader.display_text(raw) == "Code: 0012\n\nAmount: 0"
    assert ImageDocumentReader.display_text(TRANSCRIPTION) == TRANSCRIPTION


@pytest.mark.parametrize("tail", ["invalid JSON", '{"left": 10}', "[]", ""])
def test_unknown_ocr_format_keeps_all_source_text(tail):
    text = "Image OCR lines (left/top are pixel coordinates):\n" + tail
    assert ImageDocumentReader.display_text(text) == text


@pytest.fixture
def source(client, monkeypatch, valid_png_bytes):
    credentials = {"email": "direct-output@example.com", "password": "test-password"}
    client.post("/users/", json={**credentials, "fullname": "Direct output test"})
    token = client.post("/users/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    uploaded = client.post(
        "/chat/documents",
        headers=headers,
        data={"analyze": "false"},
        files={"file": ("testing.png", valid_png_bytes, "image/png")},
    ).json()
    extract = MagicMock(return_value=ImageDocumentReader._document(TRANSCRIPTION, ()))
    monkeypatch.setattr(ImageDocumentReader, "read", extract)
    generate = AsyncMock(side_effect=AssertionError("Exact export must not rewrite extracted text"))
    monkeypatch.setattr(ChatDocumentService, "generate_content", generate)
    return headers, uploaded, extract, generate


def plan(monkeypatch, output, *, text_step=True):
    steps = [
        {
            "operation": "create_document",
            "input_file": "testing.png",
            "output_type": output,
            "parameters": {"filename": f"ExtractedData.{output}"},
        }
    ]
    if text_step:
        steps.insert(
            0,
            {
                "operation": "extract_document",
                "input_file": "testing.png",
                "output_type": "text_response",
            },
        )
    model = MagicMock(chat=MagicMock(return_value=json.dumps({"status": "ready", "steps": steps})))
    monkeypatch.setattr(chat_documents.automation_service, "chat_service", model)


def execute(client, source, output):
    headers, uploaded, _, _ = source
    return client.post(
        "/chat/documents/automate",
        headers=headers,
        json={
            "session_id": uploaded["session_id"],
            "source_document_id": uploaded["attachment"]["id"],
            "instruction": f"Extract all text from the image and display it in chat, then create a {output} file containing the same extracted information. Do not summarize.",
        },
    )


@pytest.mark.parametrize("output", ["pdf", "docx", "txt", "markdown"])
@pytest.mark.parametrize("text_step", [True, False])
def test_text_and_file_return_together_without_confirmation(
    client, monkeypatch, source, output, text_step
):
    plan(monkeypatch, output, text_step=text_step)
    response = execute(client, source, output)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "done", body
    assert body["response"].startswith(TRANSCRIPTION)
    assert "Review before building" not in body["response"]
    assert len(body["attachments"]) == 1
    headers, uploaded, extract, generate = source
    extract.assert_called_once()
    assert "display it in chat" in extract.call_args.kwargs["instruction"]
    generate.assert_not_awaited()
    attachment = body["attachments"][0]
    assert attachment["source_document_ids"] == [uploaded["attachment"]["id"]]
    download = client.get(f"/chat/documents/{attachment['id']}/download", headers=headers)
    assert download.status_code == 200
    if output == "pdf":
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(download.content)).pages)
    elif output == "docx":
        text = "\n".join(p.text for p in Document(BytesIO(download.content)).paragraphs)
    else:
        text = download.content.decode("utf-8")
    assert all(
        label in text
        for label in ["Unit testing: 12 cases", "Integration testing: 8 cases", "QA-END-731"]
    )
    history = client.get(f"/chat/sessions/{uploaded['session_id']}", headers=headers).json()
    assert history["messages"][-1]["content"] == body["response"]
    assert history["messages"][-1]["automation"]["response"] == body


def test_file_failure_keeps_extracted_text_in_response_and_history(client, monkeypatch, source):
    plan(monkeypatch, "pdf")
    monkeypatch.setattr(
        chat_documents.pipeline_service.document_service.document_generator,
        "generate",
        MagicMock(side_effect=InvalidDocumentError("PDF export failed")),
    )
    response = execute(client, source, "pdf")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "partial"
    assert body["response"].startswith(TRANSCRIPTION)
    assert "PDF export failed" in body["response"]
    assert body["attachments"] == []
    headers, uploaded, _, _ = source
    history = client.get(f"/chat/sessions/{uploaded['session_id']}", headers=headers).json()
    assert history["messages"][-1]["content"] == body["response"]


def test_all_text_steps_are_returned_in_order(client, monkeypatch, source):
    headers, uploaded, _, _ = source
    steps = [
        {
            "operation": "extract_document",
            "input_file": "testing.png",
            "output_type": "text_response",
        },
        {
            "operation": "summarize_document",
            "input_file": "testing.png",
            "output_type": "text_response",
        },
    ]
    monkeypatch.setattr(
        chat_documents.automation_service,
        "chat_service",
        MagicMock(chat=MagicMock(return_value=json.dumps({"status": "ready", "steps": steps}))),
    )
    monkeypatch.setattr(
        ChatDocumentService, "summarize", AsyncMock(return_value="Summary: 20 cases.")
    )
    result = client.post(
        "/chat/documents/automate",
        headers=headers,
        json={
            "session_id": uploaded["session_id"],
            "source_document_id": uploaded["attachment"]["id"],
            "instruction": "Extract all text from the image then summarize it in chat.",
        },
    ).json()
    assert result["status"] == "done"
    assert result["response"].startswith(TRANSCRIPTION + "\n\nSummary: 20 cases.")
    assert not result["attachments"]


def test_selected_upload_is_used_when_planner_repeats_an_older_filename(
    client, monkeypatch, source
):
    headers, uploaded, _, _ = source
    client.post(
        "/chat/documents",
        headers=headers,
        data={"session_id": uploaded["session_id"], "analyze": "false"},
        files={"file": ("old.txt", b"Wrong source: old information", "text/plain")},
    ).raise_for_status()
    steps = [
        {"operation": "extract_document", "input_file": "old.txt", "output_type": "text_response"},
        {"operation": "create_document", "input_file": "testing.png", "output_type": "pdf"},
    ]
    monkeypatch.setattr(
        chat_documents.automation_service,
        "chat_service",
        MagicMock(chat=MagicMock(return_value=json.dumps({"status": "ready", "steps": steps}))),
    )
    response = execute(client, source, "pdf")
    body = response.json()
    assert body["status"] == "done", body
    assert body["response"].startswith(TRANSCRIPTION)
    assert "Wrong source" not in body["response"]
    assert body["steps"][0]["source_document_ids"] == [uploaded["attachment"]["id"]]
