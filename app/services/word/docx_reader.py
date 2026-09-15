from collections.abc import Callable
from io import BytesIO

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.document.document_operation_registry import DocumentType
from app.services.document.extraction_models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentLocation,
    DocumentMetadata,
    ExtractedDocument,
    ExtractedTable,
)


class DocxReader:
    def __init__(self, document_factory: Callable = Document) -> None:
        self.document_factory = document_factory

    def read(self, file_data: bytes) -> ExtractedDocument:
        document = self.document_factory(BytesIO(file_data))
        parts: list[str] = []
        blocks: list[DocumentBlock] = []
        tables: list[ExtractedTable] = []
        paragraph_index = 0
        table_index = 0
        for item in document.iter_inner_content():
            if isinstance(item, Paragraph):
                paragraph_index += 1
                value = item.text.strip()
                if not value:
                    continue
                style_name = str(getattr(item.style, "name", "") or "")
                if style_name.casefold().startswith("heading"):
                    block_type = DocumentBlockType.HEADING
                elif "list" in style_name.casefold():
                    block_type = DocumentBlockType.LIST
                else:
                    block_type = DocumentBlockType.PARAGRAPH
                blocks.append(
                    DocumentBlock(
                        type=block_type,
                        text=value,
                        location=DocumentLocation(paragraph_index=paragraph_index),
                        style={
                            "style_name": style_name or None,
                            "alignment": str(item.alignment)
                            if item.alignment is not None
                            else None,
                            "bold": any(run.bold is True for run in item.runs),
                            "italic": any(run.italic is True for run in item.runs),
                        },
                    )
                )
                parts.append(value)
            elif isinstance(item, Table):
                table_index += 1
                rows = tuple(tuple(cell.text.strip() for cell in row.cells) for row in item.rows)
                if not rows:
                    continue
                location = DocumentLocation(paragraph_index=paragraph_index + 1)
                tables.append(
                    ExtractedTable(
                        name=f"Table {table_index}",
                        rows=rows,
                        columns=rows[0],
                        header_detected=len(rows) > 1,
                        location=location,
                    )
                )
                blocks.append(
                    DocumentBlock(
                        type=DocumentBlockType.TABLE,
                        cells=rows,
                        location=location,
                    )
                )
                parts.append("\n".join("\t".join(row) for row in rows))
        properties = document.core_properties
        return ExtractedDocument(
            document_type=DocumentType.DOCX,
            text="\n".join(parts).strip(),
            tables=tuple(tables),
            blocks=tuple(blocks),
            metadata=DocumentMetadata(
                author=properties.author or None,
                title=properties.title or None,
            ),
            coverage=(f"{paragraph_index} paragraphs and {table_index} tables"),
        )
