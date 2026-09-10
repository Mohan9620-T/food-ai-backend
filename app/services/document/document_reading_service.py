from pathlib import Path

from app.services.document.document_operation_registry import DocumentType
from app.services.document.exceptions import (
    DocumentProcessingUnavailableError,
    InvalidDocumentError,
)
from app.services.document.extraction_models import ExtractedDocument
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
    ) -> None:
        self.pdf_reader = pdf_reader or PdfDocumentReader()
        self.docx_reader = docx_reader or DocxReader()
        self.excel_reader = excel_reader or ExcelReader()
        self.text_reader = text_reader or TextDocumentReader()
        self.csv_reader = csv_reader or CsvReader(self.text_reader)
        self.pptx_reader = pptx_reader or PptxReader()

    def read(self, file_data: bytes, filename: str) -> ExtractedDocument:
        if not file_data:
            raise InvalidDocumentError("The uploaded document is empty.")
        document_type = self._document_type(filename)
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
            else:
                raise InvalidDocumentError("Unsupported document type.")
        except (InvalidDocumentError, DocumentProcessingUnavailableError):
            raise
        except Exception as error:
            raise InvalidDocumentError("The document is corrupt or could not be read.") from error
        if not extracted.text.strip() and not extracted.tables:
            raise InvalidDocumentError("The document does not contain readable text.")
        return extracted

    @staticmethod
    def _document_type(filename: str) -> DocumentType:
        extension = Path(filename).suffix.casefold()
        if extension in {".md", ".markdown"}:
            return DocumentType.MARKDOWN
        try:
            return DocumentType(extension.lstrip("."))
        except ValueError as error:
            raise InvalidDocumentError("Unsupported document type.") from error
