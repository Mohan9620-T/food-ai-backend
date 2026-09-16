import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.document.extraction_models import OperationPlan


class DocumentType(str, Enum):
    XLSX = "xlsx"
    CSV = "csv"
    DOCX = "docx"
    PDF = "pdf"
    PPTX = "pptx"
    TXT = "txt"
    MARKDOWN = "markdown"
    IMAGE = "image"


class Fidelity(str, Enum):
    FULL = "FULL"
    HIGH = "HIGH"
    PARTIAL = "PARTIAL"
    BEST_EFFORT = "BEST_EFFORT"
    UNSUPPORTED = "UNSUPPORTED"


class InputArity(str, Enum):
    SINGLE = "SINGLE"
    MULTI = "MULTI"


class DocumentOperation(str, Enum):
    EXPAND_DISH_BY_DIETARY_CATEGORY = "expand_dish_by_dietary_category"
    SPLIT_BY_CATEGORY = "split_by_category"
    FILTER_COLUMN = "filter_column"
    FORMAT_WORKBOOK = "format_workbook"
    CREATE_DOCUMENT = "create_document"
    READ_DOCUMENT = "read_document"
    EXTRACT_DOCUMENT = "extract_document"
    SUMMARIZE_DOCUMENT = "summarize_document"
    ANALYZE_DOCUMENT = "analyze_document"
    ANSWER_DOCUMENT_QUESTION = "answer_document_question"
    EXTRACT_TABLE_TO_EXCEL = "extract_table_to_excel"
    CONVERT_DOCUMENT = "convert_document"
    MODIFY_DOCUMENT = "modify_document"
    MODIFY_PDF_PAGES = "modify_pdf_pages"
    MERGE_PDF = "merge_pdf"
    FILTER_AND_SORT_WORKBOOK = "filter_and_sort_workbook"


@dataclass(frozen=True)
class DocumentOperationDefinition:
    operation: DocumentOperation
    aliases: tuple[str, ...]
    requires_input: bool
    input_types: frozenset[DocumentType]
    output_types: frozenset[DocumentType]
    required_parameters: frozenset[str] = field(default_factory=frozenset)
    optional_parameters: frozenset[str] = field(default_factory=frozenset)
    expected_fidelity: Fidelity = Fidelity.HIGH
    destructive: bool = False
    input_arity: InputArity = InputArity.SINGLE


class DocumentOperationRegistry:
    """Canonical names and aliases for document operations and file types."""

    TYPE_ALIASES: dict[DocumentType, tuple[str, ...]] = {
        DocumentType.XLSX: ("xlsx", "excel", "spreadsheet", "workbook"),
        DocumentType.CSV: ("csv", "comma separated", "comma-separated"),
        DocumentType.DOCX: ("docx", "word", "word document"),
        DocumentType.PDF: ("pdf",),
        DocumentType.PPTX: ("pptx", "powerpoint", "presentation", "slide deck", "slides"),
        DocumentType.TXT: ("txt", "text file", "plain text"),
        DocumentType.MARKDOWN: ("markdown", "md", "markdown file"),
        DocumentType.IMAGE: (
            "image",
            "photo",
            "picture",
            "screenshot",
            "jpg",
            "jpeg",
            "png",
            "webp",
            "gif",
        ),
    }

    def __init__(self) -> None:
        readable = frozenset(DocumentType)
        creatable = readable - {DocumentType.IMAGE}
        spreadsheet = frozenset({DocumentType.XLSX})
        spreadsheet_or_csv = frozenset({DocumentType.XLSX, DocumentType.CSV})
        self._definitions = {
            definition.operation: definition
            for definition in (
                DocumentOperationDefinition(
                    DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY,
                    ("expand dish categories", "create category rows", "generate rows for x"),
                    True,
                    spreadsheet,
                    spreadsheet,
                    optional_parameters=frozenset({"sheet_name", "categories", "active_marker"}),
                    expected_fidelity=Fidelity.FULL,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.SPLIT_BY_CATEGORY,
                    ("split by category", "category sheets", "one sheet per category"),
                    True,
                    spreadsheet,
                    spreadsheet,
                    optional_parameters=frozenset({"sheet_name"}),
                    expected_fidelity=Fidelity.HIGH,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.FILTER_COLUMN,
                    ("filter column", "add column filter", "enable filtering"),
                    True,
                    spreadsheet_or_csv,
                    spreadsheet,
                    required_parameters=frozenset({"column"}),
                    expected_fidelity=Fidelity.FULL,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.FORMAT_WORKBOOK,
                    ("format workbook", "style spreadsheet", "clean up excel"),
                    True,
                    spreadsheet,
                    spreadsheet,
                    expected_fidelity=Fidelity.HIGH,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.CREATE_DOCUMENT,
                    ("create document", "generate document", "write document"),
                    False,
                    frozenset(),
                    creatable,
                    optional_parameters=frozenset({"title", "filename"}),
                    expected_fidelity=Fidelity.HIGH,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.READ_DOCUMENT,
                    ("read document", "extract text", "show document contents"),
                    True,
                    readable,
                    frozenset(),
                    expected_fidelity=Fidelity.HIGH,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.EXTRACT_DOCUMENT,
                    ("extract data", "extract information", "find fields"),
                    True,
                    readable,
                    frozenset(),
                    optional_parameters=frozenset({"fields"}),
                    expected_fidelity=Fidelity.HIGH,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.SUMMARIZE_DOCUMENT,
                    ("summarize document", "analyse document", "review document"),
                    True,
                    readable,
                    frozenset(),
                    optional_parameters=frozenset({"focus"}),
                    expected_fidelity=Fidelity.HIGH,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.ANALYZE_DOCUMENT,
                    ("analyze document", "analyse document", "inspect document"),
                    True,
                    readable,
                    frozenset(),
                ),
                DocumentOperationDefinition(
                    DocumentOperation.ANSWER_DOCUMENT_QUESTION,
                    ("question about document", "answer from document", "document q and a"),
                    True,
                    readable,
                    frozenset(),
                    optional_parameters=frozenset({"question"}),
                    expected_fidelity=Fidelity.HIGH,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.EXTRACT_TABLE_TO_EXCEL,
                    (
                        "extract table to excel",
                        "pdf table to excel",
                        "create excel from pdf table",
                    ),
                    True,
                    frozenset({DocumentType.PDF}),
                    frozenset({DocumentType.XLSX}),
                    expected_fidelity=Fidelity.HIGH,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.CONVERT_DOCUMENT,
                    ("convert document", "export as", "save as"),
                    True,
                    readable,
                    creatable,
                    expected_fidelity=Fidelity.PARTIAL,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.MODIFY_DOCUMENT,
                    ("modify document", "update document", "edit document", "replace text"),
                    True,
                    frozenset({DocumentType.DOCX, DocumentType.XLSX, DocumentType.PPTX}),
                    frozenset({DocumentType.DOCX, DocumentType.XLSX, DocumentType.PPTX}),
                    required_parameters=frozenset({"edits"}),
                    expected_fidelity=Fidelity.FULL,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.MODIFY_PDF_PAGES,
                    ("remove pdf pages", "extract pdf pages", "reorder pdf pages"),
                    True,
                    frozenset({DocumentType.PDF}),
                    frozenset({DocumentType.PDF}),
                    required_parameters=frozenset({"edits"}),
                    expected_fidelity=Fidelity.HIGH,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.MERGE_PDF,
                    ("merge pdf", "combine pdf files"),
                    True,
                    frozenset({DocumentType.PDF}),
                    frozenset({DocumentType.PDF}),
                    expected_fidelity=Fidelity.FULL,
                    input_arity=InputArity.MULTI,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.FILTER_AND_SORT_WORKBOOK,
                    ("filter and sort workbook", "filter records and sort"),
                    True,
                    spreadsheet,
                    spreadsheet,
                    required_parameters=frozenset({"edits"}),
                    expected_fidelity=Fidelity.FULL,
                ),
            )
        }
        self._aliases = {
            self.normalize(alias): definition.operation
            for definition in self._definitions.values()
            for alias in (definition.operation.value, *definition.aliases)
        }

    @staticmethod
    def normalize(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))

    def get(self, operation: DocumentOperation | str) -> DocumentOperationDefinition | None:
        try:
            key = (
                operation
                if isinstance(operation, DocumentOperation)
                else DocumentOperation(operation)
            )
        except ValueError:
            return None
        return self._definitions.get(key)

    def resolve_alias(self, alias: str) -> DocumentOperationDefinition | None:
        operation = self._aliases.get(self.normalize(alias))
        return self._definitions.get(operation) if operation is not None else None

    def operations(self) -> tuple[DocumentOperationDefinition, ...]:
        return tuple(self._definitions.values())

    def validate_plan(self, plan: "OperationPlan") -> None:
        """Validate an already schema-parsed plan against registered capabilities."""
        for step in plan.steps:
            definition = self.get(step.operation)
            if definition is None:
                raise ValueError(f"Operation '{step.operation}' is not registered.")
            is_multi = isinstance(step.input_ref, list)
            if definition.input_arity == InputArity.SINGLE and is_multi:
                raise ValueError(f"{step.operation.value} accepts exactly one input reference.")
            if definition.input_arity == InputArity.MULTI and not is_multi:
                raise ValueError(f"{step.operation.value} requires multiple input references.")
            if (
                definition.input_arity == InputArity.MULTI
                and isinstance(step.input_ref, list)
                and len(step.input_ref) < 2
            ):
                raise ValueError(f"{step.operation.value} requires at least two input references.")
            parameters = set(step.parameters)
            missing = definition.required_parameters - parameters
            unexpected = (
                parameters - definition.required_parameters - definition.optional_parameters
            )
            if missing:
                raise ValueError(
                    f"{step.operation.value} is missing required parameters: "
                    + ", ".join(sorted(missing))
                )
            if unexpected:
                raise ValueError(
                    f"{step.operation.value} contains unsupported parameters: "
                    + ", ".join(sorted(unexpected))
                )
            output = "markdown" if step.output_type == "md" else step.output_type
            if output == "text_response":
                if definition.output_types:
                    raise ValueError(
                        f"{step.operation.value} must produce a registered document type."
                    )
            else:
                output_type = DocumentType(output)
                if output_type not in definition.output_types:
                    raise ValueError(
                        f"{output_type.value.upper()} is not a valid output for "
                        f"{step.operation.value}."
                    )
                references = (
                    step.input_ref if isinstance(step.input_ref, list) else [step.input_ref]
                )
                input_types = {
                    document_type
                    for reference in references
                    if (document_type := self.document_type_from_filename(reference)) is not None
                }
                if input_types and not input_types.issubset(definition.input_types):
                    raise ValueError(
                        f"{step.operation.value} cannot use one or more referenced file types."
                    )
                if (
                    step.operation
                    in {
                        DocumentOperation.MODIFY_DOCUMENT,
                        DocumentOperation.MODIFY_PDF_PAGES,
                        DocumentOperation.FILTER_AND_SORT_WORKBOOK,
                    }
                    and input_types
                    and input_types != {output_type}
                ):
                    raise ValueError(
                        f"{step.operation.value} must preserve the input document type."
                    )

    @classmethod
    def document_type_from_filename(cls, filename: str | None) -> DocumentType | None:
        if not filename or "." not in filename:
            return None
        extension = filename.rsplit(".", 1)[-1].casefold()
        if extension in {"jpg", "jpeg", "png", "webp", "gif"}:
            extension = DocumentType.IMAGE.value
        elif extension == "md":
            extension = DocumentType.MARKDOWN.value
        try:
            return DocumentType(extension)
        except ValueError:
            return None

    @classmethod
    def document_types_in_text(cls, text: str) -> list[DocumentType]:
        normalized = cls.normalize(text)
        found: list[tuple[int, DocumentType]] = []
        for document_type, aliases in cls.TYPE_ALIASES.items():
            positions = [
                match.start()
                for alias in aliases
                for match in re.finditer(rf"\b{re.escape(cls.normalize(alias))}\b", normalized)
            ]
            if positions:
                found.append((min(positions), document_type))
        return [document_type for _, document_type in sorted(found)]
