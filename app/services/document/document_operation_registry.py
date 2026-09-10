import re
from dataclasses import dataclass
from enum import Enum


class DocumentType(str, Enum):
    XLSX = "xlsx"
    CSV = "csv"
    DOCX = "docx"
    PDF = "pdf"
    PPTX = "pptx"
    TXT = "txt"
    MARKDOWN = "markdown"


class DocumentOperation(str, Enum):
    EXPAND_DISH_BY_DIETARY_CATEGORY = "expand_dish_by_dietary_category"
    SPLIT_BY_CATEGORY = "split_by_category"
    FILTER_COLUMN = "filter_column"
    FORMAT_WORKBOOK = "format_workbook"
    CREATE_DOCUMENT = "create_document"
    READ_DOCUMENT = "read_document"
    SUMMARIZE_DOCUMENT = "summarize_document"
    EXTRACT_TABLE_TO_EXCEL = "extract_table_to_excel"
    CONVERT_DOCUMENT = "convert_document"


@dataclass(frozen=True)
class DocumentOperationDefinition:
    operation: DocumentOperation
    aliases: tuple[str, ...]
    requires_input: bool
    input_types: frozenset[DocumentType]
    output_types: frozenset[DocumentType]


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
    }

    def __init__(self) -> None:
        readable = frozenset(DocumentType)
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
                ),
                DocumentOperationDefinition(
                    DocumentOperation.SPLIT_BY_CATEGORY,
                    ("split by category", "category sheets", "one sheet per category"),
                    True,
                    spreadsheet,
                    spreadsheet,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.FILTER_COLUMN,
                    ("filter column", "add column filter", "enable filtering"),
                    True,
                    spreadsheet_or_csv,
                    spreadsheet,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.FORMAT_WORKBOOK,
                    ("format workbook", "style spreadsheet", "clean up excel"),
                    True,
                    spreadsheet,
                    spreadsheet,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.CREATE_DOCUMENT,
                    ("create document", "generate document", "write document"),
                    False,
                    frozenset(),
                    readable,
                ),
                DocumentOperationDefinition(
                    DocumentOperation.READ_DOCUMENT,
                    ("read document", "extract text", "show document contents"),
                    True,
                    readable,
                    frozenset(),
                ),
                DocumentOperationDefinition(
                    DocumentOperation.SUMMARIZE_DOCUMENT,
                    ("summarize document", "analyse document", "review document"),
                    True,
                    readable,
                    frozenset(),
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
                ),
                DocumentOperationDefinition(
                    DocumentOperation.CONVERT_DOCUMENT,
                    ("convert document", "export as", "save as"),
                    True,
                    readable,
                    readable,
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

    @classmethod
    def document_type_from_filename(cls, filename: str | None) -> DocumentType | None:
        if not filename or "." not in filename:
            return None
        extension = filename.rsplit(".", 1)[-1].casefold()
        if extension == "md":
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
