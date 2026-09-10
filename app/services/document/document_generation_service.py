import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.services.document.document_operation_registry import DocumentType
from app.services.document.document_validation_service import DocumentValidationService
from app.services.pdf.pdf_generator import PdfGenerator
from app.services.powerpoint.pptx_generator import PptxGenerator
from app.services.spreadsheet.csv_generator import CsvGenerator
from app.services.spreadsheet.excel_generator import ExcelGenerator
from app.services.word.docx_generator import DocxGenerator


@dataclass(frozen=True)
class GeneratedDocument:
    file_data: bytes
    filename: str
    content_type: str
    document_type: DocumentType


class DocumentGenerationService:
    MIME_TYPES = {
        DocumentType.PDF: "application/pdf",
        DocumentType.DOCX: (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        DocumentType.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        DocumentType.CSV: "text/csv",
        DocumentType.PPTX: (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        DocumentType.TXT: "text/plain",
        DocumentType.MARKDOWN: "text/markdown",
    }
    EXTENSIONS = {
        **{document_type: f".{document_type.value}" for document_type in DocumentType},
        DocumentType.MARKDOWN: ".md",
    }
    WINDOWS_RESERVED_NAMES = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }

    def __init__(self, validator: DocumentValidationService | None = None) -> None:
        self.validator = validator or DocumentValidationService()
        self.pdf_generator = PdfGenerator()
        self.docx_generator = DocxGenerator()
        self.excel_generator = ExcelGenerator()
        self.csv_generator = CsvGenerator()
        self.pptx_generator = PptxGenerator()

    def generate(
        self,
        content: str,
        output_format: str,
        *,
        plain_text: bool = False,
        requested_filename: str | None = None,
    ) -> GeneratedDocument:
        document_type = self._document_type(output_format)
        if document_type in {DocumentType.TXT, DocumentType.MARKDOWN}:
            file_data = content.encode("utf-8")
        elif document_type == DocumentType.PDF:
            file_data = self.pdf_generator.generate(content, plain_text=plain_text)
        elif document_type == DocumentType.DOCX:
            file_data = self.docx_generator.generate(content, plain_text=plain_text)
        elif document_type == DocumentType.XLSX:
            file_data = self.excel_generator.generate(content)
        elif document_type == DocumentType.CSV:
            file_data = self.csv_generator.generate(content)
        elif document_type == DocumentType.PPTX:
            file_data = self.pptx_generator.generate(content)
        else:
            raise ValueError(f"Unsupported generated document type: {document_type.value}.")
        self.validator.validate(file_data, document_type)
        return GeneratedDocument(
            file_data=file_data,
            filename=self.safe_filename(requested_filename, document_type),
            content_type=self.MIME_TYPES[document_type],
            document_type=document_type,
        )

    @staticmethod
    def _document_type(output_format: str) -> DocumentType:
        normalized = output_format.casefold().strip()
        if normalized == "md":
            normalized = DocumentType.MARKDOWN.value
        try:
            return DocumentType(normalized)
        except ValueError as error:
            raise ValueError(
                "Choose PDF, DOCX, XLSX, CSV, PPTX, TXT, or Markdown as the output type."
            ) from error

    @classmethod
    def safe_filename(cls, requested_filename: str | None, document_type: DocumentType) -> str:
        raw_name = Path((requested_filename or "nutrition-document").replace("\\", "/")).name
        stem = Path(raw_name).stem
        ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
        safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", ascii_stem).strip("._-")[:100]
        if not safe_stem:
            safe_stem = "generated-document"
        if safe_stem.upper() in cls.WINDOWS_RESERVED_NAMES:
            safe_stem = f"generated-{safe_stem.lower()}"
        return f"{safe_stem}{cls.EXTENSIONS[document_type]}"
