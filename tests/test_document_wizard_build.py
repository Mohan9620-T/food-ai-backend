import hashlib
import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from openpyxl import Workbook, load_workbook

from app.api import chat_documents
from app.models.chat import ChatDocumentAttachment, ChatMessageRecord
from app.services.document.extraction_models import StructuredDocumentContent


def ready():
    return {
        "status": "ready",
        "steps": [{"operation": "create_document", "output_type": "xlsx", "parameters": {}}],
    }


def question(text="Which region?", options=True):
    payload = {"status": "clarification_required", "clarifying_question": text, "steps": []}
    if options:
        payload["clarification_options"] = [
            {"id": str(i), "label": label, "recommended": i == 1}
            for i, label in enumerate(["South India", "North India", "All India"], 1)
        ]
    return payload


@pytest.fixture
def build_setup(client, monkeypatch):
    credentials = {"email": "stateless-wizard@example.com", "password": "test-password"}
    client.post("/users/", json={**credentials, "fullname": "Review test"})
    token = client.post("/users/login", json=credentials).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    sid = client.post("/chat/sessions", json={}, headers=headers).json()["id"]
    model = MagicMock(return_value=json.dumps(ready()))
    # Replace this dependency, avoiding methods shadowed on shared service
    # instances by older execution tests.
    monkeypatch.setattr(chat_documents.automation_service, "chat_service", MagicMock(chat=model))
    content = StructuredDocumentContent.model_validate(
        {
            "title": "Dishes",
            "assumptions": ["Prices are approximate INR amounts."],
            "tables": [{"headers": ["Dish", "Price"], "rows": [["Idli", "30"], ["Dosa", "50"]]}],
        }
    )
    generate = AsyncMock(return_value=content)
    monkeypatch.setattr(
        type(chat_documents.pipeline_service.document_service), "generate_content", generate
    )
    return headers, sid, generate, model


def call(client, headers, sid, instruction="Create an Excel file with dishes", **kwargs):
    response = client.post(
        "/chat/documents/automate",
        headers=headers,
        json={"session_id": sid, "instruction": instruction, **kwargs},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_question_answer_review_replay_and_confirm_generate_once(client, db_session, build_setup):
    headers, sid, generate, model = build_setup
    renamed = ready()
    renamed["steps"][0]["parameters"] = {"filename": "Invented_Brochure.xlsx"}
    model.side_effect = [
        json.dumps(question()),
        json.dumps(ready()),
        json.dumps(renamed),
        json.dumps(ready()),
    ]
    first = call(client, headers, sid)
    assert first["clarification"]["options"][0]["recommended"] is True
    review = call(client, headers, sid, "South India")
    assert review["status"] == "ready_for_review"
    assert review["plan_summary"] == review["response"]
    assert review["attachments"] == []
    before_messages = db_session.query(ChatMessageRecord).count()
    assert call(client, headers, sid, "South India") == review
    assert db_session.query(ChatMessageRecord).count() == before_messages
    assert db_session.query(ChatDocumentAttachment).filter_by(kind="generated").count() == 0
    generate.assert_not_awaited()
    result = call(client, headers, sid, "South India", confirm=True)
    assert result["status"] == "done"
    assert len(result["attachments"]) == 1
    assert db_session.query(ChatDocumentAttachment).filter_by(kind="generated").count() == 1
    generate.assert_awaited_once()
    assert any(message.content == "South India" for message in model.call_args.kwargs["history"])
    attachment = result["attachments"][0]
    raw = client.get(f"/chat/documents/{attachment['id']}/download", headers=headers).content
    workbook = load_workbook(BytesIO(raw))
    for sheet in workbook:
        page = client.get(
            f"/chat/documents/{attachment['id']}/preview",
            params={"sheet": sheet.title},
            headers=headers,
        ).json()
        assert page["rows"] == [list(row) for row in sheet.values]
        assert page["sha256"] == hashlib.sha256(raw).hexdigest()
    assert attachment["provenance"] == "general_knowledge"


def test_confirm_cannot_skip_remaining_questions_or_new_ambiguity(client, build_setup):
    headers, sid, generate, model = build_setup
    model.side_effect = [
        json.dumps(question()),
        json.dumps(question("Which currency?")),
        json.dumps(ready()),
        json.dumps(question("What title?", False)),
    ]
    assert call(client, headers, sid)["status"] == "clarification_required"
    assert (
        call(client, headers, sid, "South India", confirm=True)["status"]
        == "clarification_required"
    )
    assert call(client, headers, sid, "INR")["status"] == "ready_for_review"
    assert call(client, headers, sid, "INR", confirm=True)["status"] == "clarification_required"
    generate.assert_not_awaited()


def test_plain_question_stays_compatible(client, build_setup):
    headers, sid, generate, model = build_setup
    model.return_value = json.dumps(question("What should I title this file?", False))
    result = call(client, headers, sid)
    assert result["response"] == "What should I title this file?"
    assert result["clarification"] is None
    generate.assert_not_awaited()


def test_default_gate_and_explicit_source_use_existing_pipeline(client, build_setup):
    headers, sid, generate, _ = build_setup
    upload = client.post(
        "/chat/documents",
        data={"session_id": sid, "analyze": "false"},
        files={"file": ("source.txt", b"Dish: Idli. Price: 30 INR.", "text/plain")},
        headers=headers,
    ).json()
    source = upload["attachment"]["id"]
    assert call(client, headers, sid, source_document_id=source)["status"] == "ready_for_review"
    generate.assert_not_awaited()
    result = call(client, headers, sid, source_document_id=source, confirm=True)
    assert result["attachments"][0]["source_document_ids"] == [source]
    assert result["attachments"][0]["provenance"] == "uploaded_source"


def test_generation_failure_never_returns_attachment(client, build_setup):
    headers, sid, generate, _ = build_setup
    generate.side_effect = ValueError("generation failed")
    result = call(client, headers, sid, confirm=True)
    assert result["status"] == "failed"
    assert not result["attachments"]


def test_preview_preserves_blanks_zero_formula_and_sheet_structure(client, build_setup):
    headers, sid, _, _ = build_setup
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Name", "Blank", "Zero", "Formula"])
    ws.append(["Item", None, 0, "=1+1"])
    wb.create_sheet("Dishes").append(["Idli", 30])
    buffer = BytesIO()
    wb.save(buffer)
    wb.close()
    uploaded = client.post(
        "/chat/documents",
        data={"session_id": sid, "analyze": "false"},
        files={
            "file": (
                "sample.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    document_id = uploaded.json()["attachment"]["id"]
    path = f"/chat/documents/{document_id}/preview"
    preview = client.get(
        path,
        params={
            "sheet": "Summary",
            "row_offset": 1,
            "column_offset": 1,
            "row_limit": 1,
            "column_limit": 3,
        },
        headers=headers,
    ).json()
    assert preview["rows"] == [[None, 0, "=1+1"]]
    assert preview["formulas"] == [[None, None, "=1+1"]]
    assert preview["sheets"] == [
        {"name": "Summary", "rows": 2, "columns": 4},
        {"name": "Dishes", "rows": 1, "columns": 2},
    ]
    assert client.get(path, params={"row_limit": 201}, headers=headers).status_code == 422
    assert client.get(path, params={"sheet": "missing"}, headers=headers).status_code == 404
    assert client.get(path).status_code == 401
