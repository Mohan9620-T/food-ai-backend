import json
from unittest.mock import MagicMock

import pytest

from app.schemas.chat import ChatDocumentAutomationRequest
from app.services.document.document_automation_service import (
    AvailableDocument,
    DocumentAutomationService,
)
from app.services.document.document_operation_registry import DocumentOperation, DocumentType

ERP_REQUEST = (
    "Prepare a Excel based ERP with summary dashboard flowchart which should include liability, "
    "asset, sales, Expense, Purchase, Profit and create separate sheet for all the category "
    "(day wise entries for Sales with mode of payments with reconciliation collection wise bank "
    "& cash comparison column with summary sheet, Expense ledger wise creation narration facility "
    "debit, credit, day expense total, Month total & summary, Purchase ledger with vendor wise "
    "purchase report which should include month total value (Supplier wise), quantity and outstanding "
    "supplier wise, Stock Availability, selling quantity (Daily & Monthly summary). "
    "Report should need in following pattern. Attached image Please make it complete ERP pattern "
    "with all report facility. Create new excel document"
)


@pytest.mark.parametrize(
    "documents", [(), (AvailableDocument(1, "old.pdf", DocumentType.PDF, True),)]
)
def test_new_erp_with_missing_layout_example_creates_from_written_requirements(documents):
    model = MagicMock()
    plan = DocumentAutomationService(chat_service=model).plan(ERP_REQUEST, documents)
    assert plan.status == "ready"
    assert len(plan.steps) == 1
    assert plan.steps[0].intent.operation == DocumentOperation.CREATE_DOCUMENT
    assert plan.steps[0].intent.output_type == DocumentType.XLSX
    assert plan.steps[0].source_filenames == ()
    assert "standard layout" in plan.steps[0].instruction
    model.chat.assert_not_called()


def test_a_long_image_extraction_request_still_needs_the_actual_image():
    model = MagicMock()
    request = (
        "Read this image and extract all item names, countries, prices, totals, quantities, "
        "descriptions and notes into a new Excel document with a separate sheet for each "
        "category, preserving every visible number and every original label exactly."
    )
    plan = DocumentAutomationService(chat_service=model).plan(request, ())
    assert plan.status == "clarification_required"
    assert "upload" in plan.clarifying_question.lower()
    model.chat.assert_not_called()


def test_user_can_choose_to_create_without_any_existing_upload():
    model = MagicMock()
    plan = DocumentAutomationService(chat_service=model).plan(
        "Create a new Excel from this image with Item and Quantity columns",
        (AvailableDocument(1, "old.png", DocumentType.IMAGE, True),),
        source_mode="description",
    )
    assert plan.status == "ready"
    assert plan.steps[0].source_filenames == ()
    assert plan.steps[0].intent.output_type == DocumentType.XLSX


@pytest.mark.parametrize(
    "format_name,output_type",
    [("Excel", DocumentType.XLSX), ("Word", DocumentType.DOCX), ("PDF", DocumentType.PDF)],
)
def test_model_cannot_require_an_upload_for_a_new_described_document(format_name, output_type):
    model = MagicMock()
    model.chat.return_value = json.dumps(
        {
            "status": "clarification_required",
            "steps": [],
            "clarifying_question": "Please upload a source document first.",
        }
    )
    plan = DocumentAutomationService(chat_service=model).plan(
        f"Create a new {format_name} document about workplace organization",
        (),
    )
    assert plan.status == "ready"
    assert plan.steps[0].intent.output_type == output_type
    assert plan.steps[0].source_filenames == ()


def test_unspecified_new_document_asks_for_content_instead_of_upload():
    model = MagicMock()
    model.chat.return_value = json.dumps(
        {
            "status": "clarification_required",
            "steps": [],
            "clarifying_question": "Please upload an image first.",
        }
    )
    plan = DocumentAutomationService(chat_service=model).plan("Create new Excel document", ())
    assert plan.status == "clarification_required"
    assert "contain" in plan.clarifying_question
    assert "upload" not in plan.clarifying_question.lower()


def test_description_mode_rejects_a_conflicting_source_id():
    with pytest.raises(ValueError, match="written requirements"):
        ChatDocumentAutomationRequest(
            session_id=1,
            instruction="Create Excel",
            source_document_id=1,
            source_mode="description",
        )
