from collections.abc import Callable
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.services.document.document_operation_registry import DocumentType
from app.services.document.document_validation_service import (
    DocumentValidationService,
    GeneratedDocumentValidationError,
)
from app.services.document.exceptions import (
    DocumentProcessingUnavailableError,
    InvalidDocumentError,
)
from app.services.document.extraction_models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentLocation,
    DocumentMetadata,
    DocumentSource,
    ExtractedDocument,
    ExtractionMode,
)
from app.services.document.text_reader import TextDocumentReader
from app.services.pdf.pdf_reader import PdfDocumentReader
from app.services.powerpoint.pptx_reader import PptxReader
from app.services.spreadsheet.csv_reader import CsvReader
from app.services.spreadsheet.excel_reader import ExcelReader
from app.services.word.docx_reader import DocxReader


class DocumentReadingService:
    """Route bytes to deterministic, format-specific readers."""

    def __init__(
        self,
        *,
        pdf_reader: PdfDocumentReader | None = None,
        docx_reader: DocxReader | None = None,
        excel_reader: ExcelReader | None = None,
        csv_reader: CsvReader | None = None,
        pptx_reader: PptxReader | None = None,
        text_reader: TextDocumentReader | None = None,
        image_reader: Callable[[bytes], ExtractedDocument] | None = None,
        validator: DocumentValidationService | None = None,
    ) -> None:
        self.pdf_reader = pdf_reader or PdfDocumentReader()
        self.docx_reader = docx_reader or DocxReader()
        self.excel_reader = excel_reader or ExcelReader()
        self.text_reader = text_reader or TextDocumentReader()
        self.csv_reader = csv_reader or CsvReader(self.text_reader)
        self.pptx_reader = pptx_reader or PptxReader()
        self.image_reader = image_reader or self._read_image
        self.validator = validator or DocumentValidationService()

    def read(self, file_data: bytes, filename: str) -> ExtractedDocument:
        if not file_data:
            raise InvalidDocumentError("The uploaded document is empty.")
        document_type = self._document_type(filename)
        if document_type in {DocumentType.DOCX, DocumentType.XLSX, DocumentType.PPTX} and (
            file_data.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
        ):
            raise InvalidDocumentError(
                "This Office document appears password-protected. Remove its password and "
                "upload it again."
            )
        if document_type in {
            DocumentType.PDF,
            DocumentType.DOCX,
            DocumentType.XLSX,
            DocumentType.PPTX,
        }:
            try:
                self.validator.validate_file_signature(file_data, document_type)
            except GeneratedDocumentValidationError as error:
                raise InvalidDocumentError(
                    "The file is too large or malformed (or corrupt)."
                ) from error
        try:
            if document_type == DocumentType.PDF:
                extracted = self.pdf_reader.read(file_data)
            elif document_type == DocumentType.DOCX:
                extracted = self.docx_reader.read(file_data)
            elif document_type == DocumentType.XLSX:
                extracted = self.excel_reader.read(file_data)
            elif document_type == DocumentType.CSV:
                extracted = self.csv_reader.read(file_data)
            elif document_type == DocumentType.PPTX:
                extracted = self.pptx_reader.read(file_data)
            elif document_type in {DocumentType.TXT, DocumentType.MARKDOWN}:
                extracted = self.text_reader.read(file_data, document_type)
            elif document_type == DocumentType.IMAGE:
                extracted = self.image_reader(file_data)
            else:
                raise InvalidDocumentError("Unsupported document type.")
        except (InvalidDocumentError, DocumentProcessingUnavailableError):
            raise
        except Exception as error:
            raise InvalidDocumentError("The document is corrupt or could not be read.") from error
        if not extracted.text.strip() and not extracted.tables:
            raise InvalidDocumentError("The document does not contain readable text.")
        source = DocumentSource(
            filename=Path(filename).name,
            detected_type=document_type,
            mime=self._mime_type(filename, document_type),
        )
        return replace(extracted, source=source)

    @staticmethod
    def _document_type(filename: str) -> DocumentType:
        extension = Path(filename).suffix.casefold()
        if extension in {".md", ".markdown"}:
            return DocumentType.MARKDOWN
        if extension in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return DocumentType.IMAGE
        try:
            return DocumentType(extension.lstrip("."))
        except ValueError as error:
            raise InvalidDocumentError("Unsupported document type.") from error

    @staticmethod
    def _mime_type(filename: str, document_type: DocumentType) -> str:
        if document_type == DocumentType.IMAGE:
            extension = Path(filename).suffix.casefold()
            return "image/jpeg" if extension in {".jpg", ".jpeg"} else f"image/{extension[1:]}"
        return {
            DocumentType.PDF: "application/pdf",
            DocumentType.DOCX: (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            DocumentType.XLSX: (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            DocumentType.CSV: "text/csv",
            DocumentType.PPTX: (
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
            DocumentType.TXT: "text/plain",
            DocumentType.MARKDOWN: "text/markdown",
        }[document_type]

    @staticmethod
    def _read_image(file_data: bytes) -> ExtractedDocument:
        try:
            with Image.open(BytesIO(file_data)) as image:
                image.verify()
            from app.services.chat_vision_service import ChatVisionService

            text = ChatVisionService._run_ocr(file_data).strip()
        except (OSError, ValueError) as error:
            raise InvalidDocumentError("The image is corrupt or could not be read.") from error
        except Exception as error:
            if error.__class__.__name__ == "TesseractNotFoundError":
                raise DocumentProcessingUnavailableError(
                    "Image text extraction is unavailable because OCR is not enabled on the server."
                ) from error
            raise DocumentProcessingUnavailableError(
                "The server could not extract text from this image."
            ) from error
        if not text:
            raise InvalidDocumentError("The image does not contain readable text.")
        return ExtractedDocument(
            document_type=DocumentType.IMAGE,
            text=text,
            used_ocr=True,
            blocks=(
                DocumentBlock(
                    type=DocumentBlockType.IMAGE,
                    text=text,
                    location=DocumentLocation(page=1),
                ),
            ),
            metadata=DocumentMetadata(page_count=1),
            extraction_mode=ExtractionMode.OCR,
            coverage="1 of 1 image",
        )
