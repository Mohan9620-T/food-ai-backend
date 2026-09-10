import csv
from io import StringIO

from app.services.document.document_operation_registry import DocumentType
from app.services.document.extraction_models import ExtractedDocument, ExtractedTable
from app.services.document.text_reader import TextDocumentReader


class CsvReader:
    def __init__(self, text_reader: TextDocumentReader | None = None) -> None:
        self.text_reader = text_reader or TextDocumentReader()

    def read(self, file_data: bytes) -> ExtractedDocument:
        decoded = self.text_reader.decode(file_data)
        rows = tuple(tuple(cell for cell in row) for row in csv.reader(StringIO(decoded)))
        populated = tuple(row for row in rows if any(cell.strip() for cell in row))
        text = "\n".join("\t".join(row) for row in populated)
        tables = (ExtractedTable(name="CSV data", rows=populated),) if populated else ()
        return ExtractedDocument(document_type=DocumentType.CSV, text=text, tables=tables)
