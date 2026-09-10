from collections.abc import Callable
from contextlib import closing
from io import BytesIO

from openpyxl import load_workbook

from app.services.document.document_operation_registry import DocumentType
from app.services.document.extraction_models import ExtractedDocument, ExtractedTable


class ExcelReader:
    def __init__(self, workbook_loader: Callable = load_workbook) -> None:
        self.workbook_loader = workbook_loader

    def read(self, file_data: bytes) -> ExtractedDocument:
        text_parts: list[str] = []
        tables: list[ExtractedTable] = []
        with closing(
            self.workbook_loader(BytesIO(file_data), read_only=True, data_only=True)
        ) as workbook:
            for sheet in workbook.worksheets:
                rows = tuple(
                    tuple("" if value is None else str(value) for value in row)
                    for row in sheet.iter_rows(values_only=True)
                    if any(value is not None for value in row)
                )
                if not rows:
                    continue
                tables.append(ExtractedTable(name=sheet.title, rows=rows))
                text_parts.append(f"[{sheet.title}]\n" + "\n".join("\t".join(row) for row in rows))
        return ExtractedDocument(
            document_type=DocumentType.XLSX,
            text="\n".join(text_parts).strip(),
            tables=tuple(tables),
        )
