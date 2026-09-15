from collections.abc import Callable
from contextlib import closing
from io import BytesIO
from typing import Any

import pdfplumber
import pytesseract
from pypdf import PdfReader as PyPdfReader
from pypdf.errors import DependencyError
from pypdfium2 import PdfDocument

from app.services.document.document_operation_registry import DocumentType
from app.services.document.exceptions import (
    DocumentProcessingUnavailableError,
    InvalidDocumentError,
)
from app.services.document.extraction_models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentLocation,
    DocumentMetadata,
    ExtractedDocument,
    ExtractedTable,
    ExtractionMode,
)


class PdfDocumentReader:
    OCR_PAGE_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        *,
        pdf_reader_factory: Callable = PyPdfReader,
        pdf_document_factory: Callable = PdfDocument,
        ocr_engine: Any = pytesseract,
    ) -> None:
        self.pdf_reader_factory = pdf_reader_factory
        self.pdf_document_factory = pdf_document_factory
        self.ocr_engine = ocr_engine

    def read(self, file_data: bytes) -> ExtractedDocument:
        try:
            reader = self.pdf_reader_factory(BytesIO(file_data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise InvalidDocumentError(
                    "This PDF is password-protected. Remove its password and upload it again."
                )
            pages: list[str] = []
            ocr_indexes: list[int] = []
            for index, source_page in enumerate(reader.pages):
                text = source_page.extract_text() or ""
                pages.append(text)
                # A content stream can contain only vector rules/backgrounds (for
                # example, empty spreadsheet print pages). OCR is useful only when
                # a textless page actually contains raster image content.
                if not text.strip() and self._page_has_images(source_page):
                    ocr_indexes.append(index)
            if ocr_indexes:
                self._ocr_pages(file_data, pages, ocr_indexes)
            tables = self._extract_tables(file_data)
            ordered_blocks: list[DocumentBlock] = []
            for index, text in enumerate(pages, start=1):
                if text.strip():
                    ordered_blocks.append(
                        DocumentBlock(
                            type=DocumentBlockType.PARAGRAPH,
                            text=text.strip(),
                            location=DocumentLocation(page=index),
                            style={
                                "extracted_by": (
                                    "ocr" if index - 1 in ocr_indexes else "text_layer"
                                )
                            },
                        )
                    )
                ordered_blocks.extend(
                    DocumentBlock(
                        type=DocumentBlockType.TABLE,
                        cells=table.rows,
                        location=table.location or DocumentLocation(page=index),
                    )
                    for table in tables
                    if table.page_number == index
                )
            blocks = tuple(ordered_blocks)
            metadata = getattr(reader, "metadata", None)
            page_count = len(reader.pages)
            mode = (
                ExtractionMode.OCR
                if page_count and len(ocr_indexes) == page_count
                else ExtractionMode.MIXED
                if ocr_indexes
                else ExtractionMode.FULL
            )
            return ExtractedDocument(
                document_type=DocumentType.PDF,
                text="\n".join(pages).strip(),
                tables=tables,
                used_ocr=bool(ocr_indexes),
                blocks=blocks,
                metadata=DocumentMetadata(
                    page_count=page_count,
                    author=str(getattr(metadata, "author", "") or "") or None,
                    title=str(getattr(metadata, "title", "") or "") or None,
                ),
                extraction_mode=mode,
                coverage=f"pages 1-{page_count} of {page_count}" if page_count else "0 of 0 pages",
                warnings=tuple(
                    f"Page {index + 1} had no text layer; OCR was used." for index in ocr_indexes
                ),
            )
        except (InvalidDocumentError, DocumentProcessingUnavailableError):
            raise
        except DependencyError as error:
            raise DocumentProcessingUnavailableError(
                "The server cannot read this PDF's encryption. Upload an unencrypted copy."
            ) from error

    @staticmethod
    def _page_has_images(source_page: Any) -> bool:
        try:
            return bool(source_page.images)
        except (AttributeError, KeyError, TypeError, ValueError):
            return False

    def _ocr_pages(self, file_data: bytes, pages: list[str], indexes: list[int]) -> None:
        try:
            with closing(self.pdf_document_factory(file_data)) as pdf:
                for index in indexes:
                    with closing(pdf[index]) as page:
                        with closing(page.render(scale=2)) as bitmap:
                            with closing(bitmap.to_pil()) as image:
                                pages[index] = self.ocr_engine.image_to_string(
                                    image, timeout=self.OCR_PAGE_TIMEOUT_SECONDS
                                )
        except self.ocr_engine.TesseractNotFoundError as error:
            raise DocumentProcessingUnavailableError(
                "This PDF contains scanned pages, but OCR is unavailable on the server. "
                "Upload a searchable PDF or ask the administrator to enable OCR."
            ) from error
        except self.ocr_engine.TesseractError as error:
            raise DocumentProcessingUnavailableError(
                "The server could not read the scanned PDF using OCR. "
                "Upload a searchable PDF or contact the administrator."
            ) from error
        except RuntimeError as error:
            if "timeout" in str(error).lower():
                raise DocumentProcessingUnavailableError(
                    "Reading the scanned PDF took too long. "
                    "Try a smaller document or upload a searchable PDF."
                ) from error
            raise

    @staticmethod
    def _extract_tables(file_data: bytes) -> tuple[ExtractedTable, ...]:
        tables: list[ExtractedTable] = []
        with pdfplumber.open(BytesIO(file_data)) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                for table_index, raw_table in enumerate(page.extract_tables(), start=1):
                    rows = tuple(
                        tuple("" if cell is None else str(cell).strip() for cell in row)
                        for row in raw_table
                        if row and any(cell is not None and str(cell).strip() for cell in row)
                    )
                    if rows:
                        tables.append(
                            ExtractedTable(
                                name=f"Page {page_number} table {table_index}",
                                rows=rows,
                                page_number=page_number,
                                columns=rows[0] if rows else (),
                                header_detected=len(rows) > 1,
                                location=DocumentLocation(page=page_number),
                            )
                        )
        return tuple(tables)
