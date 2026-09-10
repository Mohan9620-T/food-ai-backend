from collections.abc import Callable
from io import BytesIO

from pptx import Presentation

from app.services.document.document_operation_registry import DocumentType
from app.services.document.extraction_models import ExtractedDocument, ExtractedTable


class PptxReader:
    def __init__(self, presentation_factory: Callable = Presentation) -> None:
        self.presentation_factory = presentation_factory

    def read(self, file_data: bytes) -> ExtractedDocument:
        presentation = self.presentation_factory(BytesIO(file_data))
        parts: list[str] = []
        tables: list[ExtractedTable] = []
        for slide_index, slide in enumerate(presentation.slides, start=1):
            slide_parts: list[str] = []
            for shape in slide.shapes:
                if getattr(shape, "has_table", False):
                    rows = tuple(
                        tuple(cell.text.strip() for cell in row.cells) for row in shape.table.rows
                    )
                    if rows:
                        tables.append(ExtractedTable(name=f"Slide {slide_index} table", rows=rows))
                        slide_parts.append("\n".join("\t".join(row) for row in rows))
                elif getattr(shape, "has_text_frame", False):
                    value = str(getattr(shape, "text", "")).strip()
                    if value:
                        slide_parts.append(value)
            if slide_parts:
                parts.append(f"[Slide {slide_index}]\n" + "\n".join(slide_parts))
        return ExtractedDocument(
            document_type=DocumentType.PPTX,
            text="\n".join(parts).strip(),
            tables=tuple(tables),
        )
