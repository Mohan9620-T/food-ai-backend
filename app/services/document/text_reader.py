import re
from codecs import BOM_UTF16_BE, BOM_UTF16_LE

from app.services.document.document_operation_registry import DocumentType
from app.services.document.exceptions import InvalidDocumentError
from app.services.document.extraction_models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentLocation,
    ExtractedDocument,
    ExtractedTable,
)


class TextDocumentReader:
    @staticmethod
    def decode(file_data: bytes) -> str:
        text, _ = TextDocumentReader.decode_with_encoding(file_data)
        return text

    @staticmethod
    def decode_with_encoding(file_data: bytes) -> tuple[str, str]:
        encodings = (
            ("utf-16",)
            if file_data.startswith((BOM_UTF16_LE, BOM_UTF16_BE))
            else ("utf-8-sig", "cp1252")
        )
        text = None
        selected = None
        for encoding in encodings:
            try:
                text = file_data.decode(encoding)
                selected = encoding
                break
            except UnicodeDecodeError:
                continue
        if text is None or selected is None:
            raise InvalidDocumentError(
                "The text file uses an unsupported encoding. Save it as UTF-8, UTF-16, or Windows-1252 and upload it again."
            )
        if any(ord(character) < 32 and character not in "\t\n\r\f" for character in text):
            raise InvalidDocumentError("The file contains binary data instead of readable text.")
        return text, selected

    def read(self, file_data: bytes, document_type: DocumentType) -> ExtractedDocument:
        text = self.decode(file_data)
        tables = self._markdown_tables(text) if document_type == DocumentType.MARKDOWN else ()
        blocks = (
            self._markdown_blocks(text, tables)
            if document_type == DocumentType.MARKDOWN
            else tuple(
                DocumentBlock(
                    type=DocumentBlockType.PARAGRAPH,
                    text=paragraph.strip(),
                    location=DocumentLocation(paragraph_index=index),
                )
                for index, paragraph in enumerate(re.split(r"\n\s*\n", text), start=1)
                if paragraph.strip()
            )
        )
        return ExtractedDocument(
            document_type=document_type,
            text=text.strip(),
            tables=tables,
            blocks=blocks,
            coverage=f"{len(text.splitlines())} lines",
        )

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

    @staticmethod
    def _markdown_blocks(
        text: str, tables: tuple[ExtractedTable, ...]
    ) -> tuple[DocumentBlock, ...]:
        blocks: list[DocumentBlock] = []
        lines = text.splitlines()
        index = 0
        table_index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            location = DocumentLocation(paragraph_index=index + 1)
            if not stripped:
                index += 1
                continue
            if stripped.startswith("```"):
                start = index
                code = [line]
                index += 1
                while index < len(lines):
                    code.append(lines[index])
                    if lines[index].strip().startswith("```"):
                        index += 1
                        break
                    index += 1
                blocks.append(
                    DocumentBlock(
                        type=DocumentBlockType.PARAGRAPH,
                        text="\n".join(code),
                        location=DocumentLocation(paragraph_index=start + 1),
                        style={"code_block": True},
                    )
                )
                continue
            heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if heading:
                blocks.append(
                    DocumentBlock(
                        type=DocumentBlockType.HEADING,
                        text=heading.group(2),
                        location=location,
                        style={"level": len(heading.group(1))},
                    )
                )
                index += 1
                continue
            if (
                index + 1 < len(lines)
                and "|" in line
                and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1])
            ):
                if table_index < len(tables):
                    table = tables[table_index]
                    table_index += 1
                    blocks.append(
                        DocumentBlock(
                            type=DocumentBlockType.TABLE,
                            cells=table.rows,
                            location=location,
                        )
                    )
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    index += 1
                continue
            if re.match(r"^(?:[-*+] |\d+[.)] )", stripped):
                blocks.append(
                    DocumentBlock(type=DocumentBlockType.LIST, text=stripped, location=location)
                )
            else:
                blocks.append(
                    DocumentBlock(
                        type=DocumentBlockType.PARAGRAPH,
                        text=stripped,
                        location=location,
                    )
                )
            index += 1
        return tuple(blocks)
