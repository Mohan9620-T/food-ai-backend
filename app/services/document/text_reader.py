import re
from codecs import BOM_UTF16_BE, BOM_UTF16_LE

from app.services.document.document_operation_registry import DocumentType
from app.services.document.exceptions import InvalidDocumentError
from app.services.document.extraction_models import ExtractedDocument, ExtractedTable


class TextDocumentReader:
    @staticmethod
    def decode(file_data: bytes) -> str:
        encoding = "utf-16" if file_data.startswith((BOM_UTF16_LE, BOM_UTF16_BE)) else "utf-8-sig"
        try:
            text = file_data.decode(encoding)
        except UnicodeDecodeError as error:
            raise InvalidDocumentError(
                "The text file uses an unsupported encoding. Save it as UTF-8 or UTF-16 and upload it again."
            ) from error
        if any(ord(character) < 32 and character not in "\t\n\r\f" for character in text):
            raise InvalidDocumentError("The file contains binary data instead of readable text.")
        return text

    def read(self, file_data: bytes, document_type: DocumentType) -> ExtractedDocument:
        text = self.decode(file_data)
        tables = self._markdown_tables(text) if document_type == DocumentType.MARKDOWN else ()
        return ExtractedDocument(document_type=document_type, text=text.strip(), tables=tables)

    @staticmethod
    def _markdown_tables(text: str) -> tuple[ExtractedTable, ...]:
        lines = text.splitlines()
        tables: list[ExtractedTable] = []
        index = 0
        while index < len(lines):
            if "|" not in lines[index]:
                index += 1
                continue
            block: list[str] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                block.append(lines[index].strip())
                index += 1
            if len(block) < 2:
                continue
            rows = [tuple(cell.strip() for cell in line.strip("|").split("|")) for line in block]
            if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
                continue
            data_rows = (rows[0], *rows[2:])
            if data_rows:
                tables.append(ExtractedTable(name=f"Table {len(tables) + 1}", rows=data_rows))
        return tuple(tables)
