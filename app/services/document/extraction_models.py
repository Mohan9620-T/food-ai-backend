from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.document.document_operation_registry import DocumentOperation, DocumentType


class ExtractionMode(str, Enum):
    FULL = "FULL"
    TRUNCATED = "TRUNCATED"
    OCR = "OCR"
    MIXED = "MIXED"
    FAILED = "FAILED"


class DocumentBlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    IMAGE = "image"
    SLIDE = "slide"
    SHEET_RANGE = "sheet_range"


@dataclass(frozen=True)
class DocumentSource:
    document_id: int | None = None
    filename: str | None = None
    detected_type: DocumentType | None = None
    mime: str | None = None


@dataclass(frozen=True)
class DocumentLocation:
    page: int | None = None
    slide: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    paragraph_index: int | None = None


@dataclass(frozen=True)
class DocumentBlock:
    type: DocumentBlockType
    text: str = ""
    cells: tuple[tuple[str, ...], ...] = ()
    location: DocumentLocation = field(default_factory=DocumentLocation)
    style: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedTable:
    name: str
    rows: tuple[tuple[str, ...], ...]
    page_number: int | None = None
    columns: tuple[str, ...] = ()
    header_detected: bool | None = None
    location: DocumentLocation | None = None


@dataclass(frozen=True)
class DocumentMetadata:
    page_count: int | None = None
    sheet_names: tuple[str, ...] = ()
    slide_count: int | None = None
    author: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class ExtractedDocument:
    """Backward-compatible structured representation returned by every reader."""

    document_type: DocumentType
    text: str
    tables: tuple[ExtractedTable, ...] = ()
    used_ocr: bool = False
    source: DocumentSource | None = None
    blocks: tuple[DocumentBlock, ...] = ()
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)
    extraction_mode: ExtractionMode = ExtractionMode.FULL
    coverage: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.used_ocr and self.extraction_mode == ExtractionMode.FULL:
            object.__setattr__(self, "extraction_mode", ExtractionMode.OCR)


DocumentRepresentation = ExtractedDocument


class GeneratedTableContent(BaseModel):
    """Validated table content returned by the text model, never file bytes."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Table", min_length=1, max_length=120)
    headers: list[str] = Field(default_factory=list, max_length=100)
    rows: list[list[str]] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def validate_width(self):
        width = len(self.headers) or max((len(row) for row in self.rows), default=0)
        if width == 0:
            raise ValueError("A generated table must contain headers or rows.")
        if any(len(row) != width for row in self.rows):
            raise ValueError("Every generated table row must have the same width as its headers.")
        return self


class GeneratedSectionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str = Field(min_length=1, max_length=200)
    paragraphs: list[str] = Field(default_factory=list, max_length=500)
    bullet_lists: list[list[str]] = Field(default_factory=list, max_length=100)
    tables: list[GeneratedTableContent] = Field(default_factory=list, max_length=50)


class StructuredDocumentContent(BaseModel):
    """Format-neutral content contract consumed by deterministic generators."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    paragraphs: list[str] = Field(default_factory=list, max_length=500)
    bullet_lists: list[list[str]] = Field(default_factory=list, max_length=100)
    tables: list[GeneratedTableContent] = Field(default_factory=list, max_length=50)
    sections: list[GeneratedSectionContent] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_content(self):
        has_content = bool(self.paragraphs or self.bullet_lists or self.tables or self.sections)
        if not has_content:
            raise ValueError("Generated document content must include at least one content block.")
        if any(not item.strip() for item in self.paragraphs):
            raise ValueError("Generated document paragraphs cannot be blank.")
        if any(not items or any(not item.strip() for item in items) for items in self.bullet_lists):
            raise ValueError("Generated bullet lists cannot be empty or contain blank items.")
        return self

    def all_tables(self) -> list[GeneratedTableContent]:
        return [*self.tables, *(table for section in self.sections for table in section.tables)]

    def to_markdown(self) -> str:
        lines = [f"# {self.title}", ""]
        self._append_markdown_blocks(lines, self.paragraphs, self.bullet_lists, self.tables)
        for section in self.sections:
            lines.extend((f"## {section.heading}", ""))
            self._append_markdown_blocks(
                lines, section.paragraphs, section.bullet_lists, section.tables
            )
        return "\n".join(lines).strip()

    def to_plain_text(self) -> str:
        lines = [self.title, ""]
        self._append_plain_blocks(lines, self.paragraphs, self.bullet_lists, self.tables)
        for section in self.sections:
            lines.extend((section.heading, ""))
            self._append_plain_blocks(
                lines, section.paragraphs, section.bullet_lists, section.tables
            )
        return "\n".join(lines).strip()

    @staticmethod
    def _append_markdown_blocks(
        lines: list[str],
        paragraphs: list[str],
        bullet_lists: list[list[str]],
        tables: list[GeneratedTableContent],
    ) -> None:
        for paragraph in paragraphs:
            lines.extend((paragraph, ""))
        for items in bullet_lists:
            lines.extend(f"- {item}" for item in items)
            lines.append("")
        for table in tables:
            lines.extend((f"### {table.title}", ""))
            headers = table.headers or [
                f"Column {index}" for index in range(1, len(table.rows[0]) + 1)
            ]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            lines.extend("| " + " | ".join(row) + " |" for row in table.rows)
            lines.append("")

    @staticmethod
    def _append_plain_blocks(
        lines: list[str],
        paragraphs: list[str],
        bullet_lists: list[list[str]],
        tables: list[GeneratedTableContent],
    ) -> None:
        for paragraph in paragraphs:
            lines.extend((paragraph, ""))
        for items in bullet_lists:
            lines.extend(f"- {item}" for item in items)
            lines.append("")
        for table in tables:
            lines.extend((table.title, ""))
            if table.headers:
                lines.append("\t".join(table.headers))
            lines.extend("\t".join(row) for row in table.rows)
            lines.append("")


class DocumentEditAction(str, Enum):
    REPLACE_TEXT = "replace_text"
    ADD_PARAGRAPH = "add_paragraph"
    REMOVE_PARAGRAPH = "remove_paragraph"
    UPDATE_TABLE_CELL = "update_table_cell"
    FORMAT_PARAGRAPH = "format_paragraph"
    FORMAT_TABLE = "format_table"
    UPDATE_CELL = "update_cell"
    INSERT_ROWS = "insert_rows"
    DELETE_ROWS = "delete_rows"
    INSERT_COLUMNS = "insert_columns"
    DELETE_COLUMNS = "delete_columns"
    SORT_RANGE = "sort_range"
    FILTER_ROWS = "filter_rows"
    FORMAT_RANGE = "format_range"
    FREEZE_PANES = "freeze_panes"
    REMOVE_PDF_PAGES = "remove_pdf_pages"
    EXTRACT_PDF_PAGES = "extract_pdf_pages"
    REORDER_PDF_PAGES = "reorder_pdf_pages"


class DocumentLocator(BaseModel):
    """A strict, format-neutral pointer resolved before a document is mutated."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    heading: str | None = None
    paragraph_index: int | None = Field(default=None, ge=1)
    table_index: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    sheet: str | None = None
    cell: str | None = None
    cell_range: str | None = None
    slide: int | None = Field(default=None, ge=1)
    shape_name: str | None = None
    pages: list[int] | None = None


class DocumentEdit(BaseModel):
    """A validated edit instruction; binary data never appears in this contract."""

    model_config = ConfigDict(extra="forbid")

    action: DocumentEditAction
    locator: DocumentLocator = Field(default_factory=DocumentLocator)
    value: object | None = None
    options: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_locator_for_action(self):
        locator = self.locator
        if self.action == DocumentEditAction.REPLACE_TEXT and not (
            (locator.text or "").strip() or (locator.heading or "").strip()
        ):
            raise ValueError("Text replacement requires a text or heading locator.")
        if self.action in {
            DocumentEditAction.ADD_PARAGRAPH,
            DocumentEditAction.REMOVE_PARAGRAPH,
            DocumentEditAction.FORMAT_PARAGRAPH,
        } and not (
            locator.paragraph_index
            or (locator.text or "").strip()
            or (locator.heading or "").strip()
        ):
            raise ValueError("A paragraph edit requires a paragraph locator.")
        if self.action == DocumentEditAction.UPDATE_TABLE_CELL and not (
            locator.table_index and locator.row and locator.column
        ):
            raise ValueError("A table-cell edit requires table, row, and column locators.")
        if (
            self.action
            in {
                DocumentEditAction.INSERT_ROWS,
                DocumentEditAction.DELETE_ROWS,
            }
            and not locator.row
        ):
            raise ValueError("A row edit requires a row locator.")
        if (
            self.action
            in {
                DocumentEditAction.INSERT_COLUMNS,
                DocumentEditAction.DELETE_COLUMNS,
            }
            and not locator.column
        ):
            raise ValueError("A column edit requires a column locator.")
        if self.action == DocumentEditAction.UPDATE_CELL and not (
            locator.cell or (locator.row and locator.column)
        ):
            raise ValueError("A cell edit requires a cell locator.")
        if self.action == DocumentEditAction.FORMAT_RANGE and not locator.cell_range:
            raise ValueError("Range formatting requires a cell-range locator.")
        if self.action == DocumentEditAction.FREEZE_PANES and not locator.cell:
            raise ValueError("Freeze panes requires a cell locator.")
        if (
            self.action
            in {
                DocumentEditAction.REMOVE_PDF_PAGES,
                DocumentEditAction.EXTRACT_PDF_PAGES,
                DocumentEditAction.REORDER_PDF_PAGES,
            }
            and not locator.pages
        ):
            raise ValueError("A PDF page edit requires page locators.")
        return self


class IntentCategory(str, Enum):
    READ = "READ"
    EXTRACT = "EXTRACT"
    SUMMARIZE = "SUMMARIZE"
    ANALYZE = "ANALYZE"
    MODIFY = "MODIFY"
    FORMAT = "FORMAT"
    CREATE = "CREATE"
    CONVERT = "CONVERT"
    TRANSFORM = "TRANSFORM"


class OperationPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: DocumentOperation
    input_ref: str | list[str]
    output_type: Literal["docx", "pdf", "xlsx", "csv", "pptx", "txt", "md", "text_response"]
    parameters: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_input_ref(self):
        references = self.input_ref if isinstance(self.input_ref, list) else [self.input_ref]
        if not references or any(not str(reference).strip() for reference in references):
            raise ValueError("Every input reference must be non-empty.")
        return self


class ClarificationOption(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=300)
    recommended: bool = Field(default=False, strict=True)


class ClarificationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=500)
    options: list[ClarificationOption] = Field(min_length=3, max_length=4)
    allow_other: bool = Field(default=True, strict=True)
    allow_freeform_answer: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def validate_options(self):
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("Clarification option IDs must be unique within each question.")
        if sum(option.recommended for option in self.options) > 1:
            raise ValueError("At most one option can be recommended per question.")
        if self.allow_other and not self.allow_freeform_answer:
            raise ValueError("Other answers require free-text input.")
        return self


class ClarificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[ClarificationQuestion] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_questions(self):
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError("Clarification question IDs must be unique.")
        return self


class OperationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_category: IntentCategory
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: bool
    clarification_question: str | None = None
    clarification_plan: ClarificationPlan | None = None
    steps: list[OperationPlanStep] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_clarification(self):
        if self.needs_clarification:
            if self.clarification_plan is not None:
                # Preserve the single-question contract for existing callers.
                self.clarification_question = self.clarification_plan.questions[0].prompt
            if not (self.clarification_question or "").strip():
                raise ValueError("A clarification plan must include a question.")
            if self.steps:
                raise ValueError("A clarification plan cannot include executable steps.")
        else:
            if self.clarification_plan is not None:
                raise ValueError("Unresolved clarification questions cannot be executable.")
            if not self.steps:
                raise ValueError("An executable plan must include at least one step.")
        return self
