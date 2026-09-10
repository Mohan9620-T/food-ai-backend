from unittest.mock import MagicMock

import pytest

from app.services.chat_document_service import ChatDocumentService
from app.services.document import (
    AmbiguousDocumentIntentError,
    DocumentIntentService,
    DocumentOperation,
    DocumentOperationRegistry,
    DocumentType,
    InvalidDocumentParametersError,
    MissingDocumentInputError,
    UnsupportedDocumentOperationError,
)


@pytest.mark.parametrize(
    ("instruction", "operation"),
    [
        ("Expand this Excel", DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY),
        ("Create category rows", DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY),
        ("Generate rows for every x", DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY),
        ("Split the item list category-wise", DocumentOperation.SPLIT_BY_CATEGORY),
        ("Create one sheet per Category", DocumentOperation.SPLIT_BY_CATEGORY),
        ("Put each category in a separate worksheet", DocumentOperation.SPLIT_BY_CATEGORY),
        ("Add a filter to the Category column", DocumentOperation.FILTER_COLUMN),
        ("Filter only the Item Category column", DocumentOperation.FILTER_COLUMN),
        ("Enable filtering for the Category* column", DocumentOperation.FILTER_COLUMN),
        ("Format this Excel workbook", DocumentOperation.FORMAT_WORKBOOK),
        ("Style the spreadsheet professionally", DocumentOperation.FORMAT_WORKBOOK),
        ("Clean up my Excel", DocumentOperation.FORMAT_WORKBOOK),
    ],
)
def test_existing_spreadsheet_operations_resolve_through_the_central_intent_service(
    instruction, operation
):
    intent = DocumentIntentService().resolve(instruction, input_file="items.xlsx")

    assert intent.operation == operation
    assert intent.document_type == DocumentType.XLSX
    assert intent.output_type == DocumentType.XLSX
    assert intent.input_file == "items.xlsx"


@pytest.mark.parametrize(
    ("instruction", "operation", "input_file", "output_type"),
    [
        ("Create a PDF report", DocumentOperation.CREATE_DOCUMENT, None, DocumentType.PDF),
        ("Generate a Word document", DocumentOperation.CREATE_DOCUMENT, None, DocumentType.DOCX),
        ("Write a Markdown file", DocumentOperation.CREATE_DOCUMENT, None, DocumentType.MARKDOWN),
        ("Read this document", DocumentOperation.READ_DOCUMENT, "notes.txt", None),
        ("Extract text from report.pdf", DocumentOperation.READ_DOCUMENT, None, None),
        ("Show me the contents", DocumentOperation.READ_DOCUMENT, "slides.pptx", None),
        ("Summarize this file", DocumentOperation.SUMMARIZE_DOCUMENT, "items.xlsx", None),
        ("Analyze report.pdf", DocumentOperation.SUMMARIZE_DOCUMENT, None, None),
        ("Review this presentation", DocumentOperation.SUMMARIZE_DOCUMENT, "deck.pptx", None),
        ("Convert report.docx to PDF", DocumentOperation.CONVERT_DOCUMENT, None, DocumentType.PDF),
        ("Export slides.pptx as PDF", DocumentOperation.CONVERT_DOCUMENT, None, DocumentType.PDF),
        ("Save data.csv as XLSX", DocumentOperation.CONVERT_DOCUMENT, None, DocumentType.XLSX),
    ],
)
def test_universal_document_operations_resolve_to_structured_intents(
    instruction, operation, input_file, output_type
):
    intent = DocumentIntentService().resolve(instruction, input_file=input_file)

    assert intent.operation == operation
    assert intent.output_type == output_type
    if operation == DocumentOperation.CREATE_DOCUMENT:
        assert intent.document_type == output_type
    assert set(intent.as_dict()) == {
        "document_type",
        "operation",
        "input_file",
        "output_type",
        "parameters",
    }


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("book.xlsx", DocumentType.XLSX),
        ("rows.csv", DocumentType.CSV),
        ("letter.docx", DocumentType.DOCX),
        ("report.pdf", DocumentType.PDF),
        ("deck.pptx", DocumentType.PPTX),
        ("notes.txt", DocumentType.TXT),
        ("readme.md", DocumentType.MARKDOWN),
    ],
)
def test_all_phase_one_document_types_are_recognized(filename, expected):
    assert DocumentOperationRegistry.document_type_from_filename(filename) == expected


def test_registry_resolves_canonical_names_and_aliases_to_one_definition():
    registry = DocumentOperationRegistry()

    canonical = registry.resolve_alias("expand_dish_by_dietary_category")
    alias = registry.resolve_alias("create category rows")

    assert canonical is not None
    assert alias is canonical
    assert canonical.operation == DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY
    assert len(registry.operations()) == 9


def test_filter_parameters_are_extracted_by_existing_spreadsheet_logic():
    intent = DocumentIntentService().resolve(
        "Add a filter only to the Category* column.", input_file="items.xlsx"
    )

    assert intent.parameters == {"column": "Category*"}


def test_expansion_alias_reuses_existing_detection_and_does_not_call_ai(monkeypatch):
    document_service = ChatDocumentService()
    chat = MagicMock(side_effect=AssertionError("Known deterministic intent must not call AI"))
    stream_chat = MagicMock(
        side_effect=AssertionError("Known deterministic intent must not call AI")
    )
    monkeypatch.setattr(document_service.chat_service, "chat", chat)
    monkeypatch.setattr(document_service.chat_service, "stream_chat", stream_chat)
    intent = DocumentIntentService(document_service=document_service).resolve(
        "Create category rows", input_file="dish-master.xlsx"
    )

    assert intent.operation == DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY
    chat.assert_not_called()
    stream_chat.assert_not_called()


def test_ambiguous_request_returns_a_clean_error():
    with pytest.raises(AmbiguousDocumentIntentError, match="more than one document operation"):
        DocumentIntentService().resolve("Summarize report.docx and convert it to PDF")


def test_unsupported_operation_returns_a_clean_error():
    with pytest.raises(UnsupportedDocumentOperationError, match="not supported"):
        DocumentIntentService().resolve("Encrypt report.pdf with a password")


def test_operation_requiring_a_file_returns_a_clean_missing_input_error():
    with pytest.raises(MissingDocumentInputError, match="Upload or select"):
        DocumentIntentService().resolve("Summarize this PDF")


@pytest.mark.parametrize(
    ("instruction", "input_file", "message"),
    [
        ("Convert report.pdf to PDF", None, "already in that format"),
        ("Create a document", None, "Specify the output document type"),
        ("Add a filter to the Category column", "items.pdf", "requires one of"),
    ],
)
def test_invalid_parameters_return_clean_errors(instruction, input_file, message):
    with pytest.raises(InvalidDocumentParametersError, match=message):
        DocumentIntentService().resolve(instruction, input_file=input_file)


def test_user_supplied_paths_are_reduced_to_a_safe_filename():
    intent = DocumentIntentService().resolve(
        "Read this document", input_file="../../private/report.pdf"
    )

    assert intent.input_file == "report.pdf"
