from collections.abc import Callable
from io import BytesIO

from docx import Document

from app.services.document.document_operation_registry import DocumentType
from app.services.document.extraction_models import ExtractedDocument, ExtractedTable


class DocxReader:
    def __init__(self, document_factory: Callable = Document) -> None:
        self.document_factory = document_factory

    def read(self, file_data: bytes) -> ExtractedDocument:
        document = self.document_factory(BytesIO(file_data))
        parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        tables: list[ExtractedTable] = []
        for index, table in enumerate(document.tables, start=1):
            rows = tuple(tuple(cell.text.strip() for cell in row.cells) for row in table.rows)
            if rows:
                tables.append(ExtractedTable(name=f"Table {index}", rows=rows))
                parts.append("\n".join("\t".join(row) for row in rows))
        return ExtractedDocument(
            document_type=DocumentType.DOCX,
            text="\n".join(parts).strip(),
            tables=tuple(tables),
        )
