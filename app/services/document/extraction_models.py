from dataclasses import dataclass

from app.services.document.document_operation_registry import DocumentType


@dataclass(frozen=True)
class ExtractedTable:
    name: str
    rows: tuple[tuple[str, ...], ...]
    page_number: int | None = None


@dataclass(frozen=True)
class ExtractedDocument:
    document_type: DocumentType
    text: str
    tables: tuple[ExtractedTable, ...] = ()
    used_ocr: bool = False
