import csv
from io import StringIO

from app.services.document.document_operation_registry import DocumentType
from app.services.document.extraction_models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentLocation,
    ExtractedDocument,
    ExtractedTable,
)
from app.services.document.text_reader import TextDocumentReader


class CsvReader:
    def __init__(self, text_reader: TextDocumentReader | None = None) -> None:
        self.text_reader = text_reader or TextDocumentReader()

    def read(self, file_data: bytes) -> ExtractedDocument:
        decoded, encoding = self.text_reader.decode_with_encoding(file_data)
        try:
            if len([line for line in decoded.splitlines() if line.strip()]) < 2:
                raise csv.Error("A single record does not provide enough delimiter evidence.")
            dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = tuple(tuple(cell for cell in row) for row in csv.reader(StringIO(decoded), dialect))
        populated = tuple(row for row in rows if any(cell.strip() for cell in row))
        text = "\n".join("\t".join(row) for row in populated)
        try:
            has_header = bool(populated) and csv.Sniffer().has_header(decoded[:8192])
        except csv.Error:
            has_header = False
        table = (
            ExtractedTable(
                name="CSV data",
                rows=populated,
                columns=populated[0] if has_header else (),
                header_detected=has_header,
                location=DocumentLocation(
                    cell_range=f"R1C1:R{len(populated)}C{max(map(len, populated))}"
                ),
            )
            if populated
            else None
        )
        return ExtractedDocument(
            document_type=DocumentType.CSV,
            text=text,
            tables=(table,) if table else (),
            blocks=(
                DocumentBlock(
                    type=DocumentBlockType.TABLE,
                    cells=populated,
                    location=table.location or DocumentLocation(),
                    style={"delimiter": dialect.delimiter, "encoding": encoding},
                ),
            )
            if table
            else (),
            coverage=f"{len(populated)} rows",
        )
