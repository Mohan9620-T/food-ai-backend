import json
import subprocess
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.api import chat_documents
from app.services.conversion.document_conversion_service import LibreOfficeConverter
from app.services.document.document_intent_service import InvalidDocumentParametersError
from app.services.document.document_operation_registry import (
    DocumentOperation,
    DocumentType,
    Fidelity,
)
from app.services.document.document_pipeline_service import (
    DocumentPipelineService,
    StructuredPipelineStep,
)
from app.services.document.document_validation_service import (
    DocumentValidationService,
    GeneratedDocumentValidationError,
)
from app.services.document.exceptions import DocumentProcessingUnavailableError
from app.services.document.extraction_models import ExtractedDocument, ExtractionMode


def _zip(parts: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return output.getvalue()


def _login(client, email: str = "phase6-security@example.com") -> dict[str, str]:
    client.post(
        "/users/",
        json={"fullname": "Phase Six", "email": email, "password": "secret123"},
    )
    token = client.post("/users/login", json={"email": email, "password": "secret123"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def test_ooxml_zip_bomb_is_rejected_by_upload_before_expansion(client):
    headers = _login(client)
    bomb = _zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<document>" + b"A" * (2 * 1024 * 1024) + b"</document>",
        }
    )

    started = time.perf_counter()
    response = client.post(
        "/chat/documents",
        headers=headers,
        data={"analyze": "false"},
        files={
            "file": (
                "compressed.docx",
                bomb,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 422
    assert "too large or malformed" in response.json()["detail"]
    assert "134217728" not in response.text
    assert elapsed < 2.0


@pytest.mark.parametrize(
    "unsafe_part",
    ["../outside.xml", "/absolute.xml", "C:/drive.xml"],
)
def test_ooxml_path_traversal_entries_are_rejected(unsafe_part):
    data = _zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<document/>",
            unsafe_part: b"<data/>",
        }
    )

    with pytest.raises(GeneratedDocumentValidationError, match="too large or malformed"):
        DocumentValidationService().validate_ooxml_container(data, DocumentType.DOCX)


@pytest.mark.parametrize("marker", [b"<!DOCTYPE document>", b"<!ENTITY secret SYSTEM 'file:///x'>"])
def test_ooxml_dtd_and_entity_declarations_are_rejected(marker):
    data = _zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": marker + b"<document/>",
        }
    )

    with pytest.raises(GeneratedDocumentValidationError, match="too large or malformed"):
        DocumentValidationService().validate_ooxml_container(data, DocumentType.DOCX)


def test_magic_bytes_are_checked_before_pdf_parser():
    with pytest.raises(GeneratedDocumentValidationError, match="too large or malformed"):
        DocumentValidationService().validate_file_signature(b"not a pdf", DocumentType.PDF)


def test_libreoffice_failure_does_not_expose_subprocess_output_and_cleans_temp(monkeypatch):
    observed: dict[str, Path] = {}

    def fail(command, **kwargs):
        del kwargs
        source = Path(command[-1])
        observed["directory"] = source.parent
        assert source.exists()
        return SimpleNamespace(
            returncode=1,
            stderr=r"C:\private\source.docx NVIDIA_API_KEY=secret",
            stdout="",
        )

    monkeypatch.setattr(subprocess, "run", fail)
    converter = LibreOfficeConverter(binary="soffice", timeout_seconds=1)

    with pytest.raises(DocumentProcessingUnavailableError) as caught:
        converter.convert_to_pdf(b"safe", "source.docx")

    assert "private" not in str(caught.value)
    assert "secret" not in str(caught.value)
    assert not observed["directory"].exists()


def test_duplicate_operations_are_rejected_before_execution():
    service = DocumentPipelineService()
    duplicate = StructuredPipelineStep(
        document_type=DocumentType.PDF,
        operation=DocumentOperation.READ_DOCUMENT,
        input_file="source.pdf",
        output_type=None,
        parameters={},
    )

    with pytest.raises(InvalidDocumentParametersError, match="duplicates"):
        service.plan_structured((duplicate, duplicate), input_filename="source.pdf")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (ExtractionMode.FULL, Fidelity.FULL),
        (ExtractionMode.OCR, Fidelity.HIGH),
        (ExtractionMode.MIXED, Fidelity.HIGH),
        (ExtractionMode.TRUNCATED, Fidelity.PARTIAL),
        (ExtractionMode.FAILED, Fidelity.BEST_EFFORT),
    ],
)
def test_extraction_fidelity_is_derived_from_actual_mode(mode, expected):
    extracted = ExtractedDocument(
        document_type=DocumentType.PDF,
        text="content",
        extraction_mode=mode,
    )

    assert DocumentValidationService.fidelity_for_extraction(extracted).fidelity == expected


def test_automation_response_discloses_actual_step_fidelity(client, monkeypatch):
    headers = _login(client, "phase6-fidelity@example.com")
    uploaded = client.post(
        "/chat/documents",
        headers=headers,
        data={"message": "Upload data", "analyze": "false"},
        files={"file": ("items.csv", b"Item,Count\nRice,2\n", "text/csv")},
    )
    assert uploaded.status_code == 200, uploaded.text
    session_id = uploaded.json()["session_id"]

    monkeypatch.setattr(
        chat_documents.automation_service.chat_service,
        "chat",
        lambda *args, **kwargs: json.dumps(
            {
                "status": "ready",
                "clarifying_question": None,
                "steps": [
                    {
                        "document_type": "csv",
                        "operation": "convert_document",
                        "input_file": "items.csv",
                        "output_type": "xlsx",
                        "parameters": {},
                    }
                ],
            }
        ),
    )
    response = client.post(
        "/chat/documents/automate",
        headers=headers,
        json={
            "confirm": True,
            "session_id": session_id,
            "instruction": "Convert items.csv to Excel",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["steps"][0]["fidelity"] == "HIGH"
    assert "Verified fidelity" in body["response"]
    assert "items-converted.xlsx: HIGH" in body["response"]
