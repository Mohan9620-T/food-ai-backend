import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.services.document.document_operation_registry import DocumentType, Fidelity
from app.services.document.document_validation_service import DocumentValidationService
from app.services.document.extraction_models import (
    ExtractedTable,
    StructuredDocumentContent,
)
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
    fidelity: Fidelity = Fidelity.HIGH
    fidelity_note: str | None = None


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
        content: str | StructuredDocumentContent,
        output_format: str,
        *,
        plain_text: bool = False,
        requested_filename: str | None = None,
    ) -> GeneratedDocument:
        document_type = self._document_type(output_format)
        if isinstance(content, StructuredDocumentContent):
            file_data = self._generate_structured(content, document_type)
            requested_filename = requested_filename or content.title
        else:
            file_data = self._generate_text(content, document_type, plain_text=plain_text)
        if isinstance(content, StructuredDocumentContent):
            source_text = (
                content.to_markdown()
                if document_type == DocumentType.MARKDOWN
                else content.to_plain_text()
            )
        else:
            source_text = content
        assessment = self.validator.assess_generation(
            file_data, document_type, source_text=source_text
        )
        return GeneratedDocument(
            file_data=file_data,
            filename=self.safe_filename(requested_filename, document_type),
            content_type=self.MIME_TYPES[document_type],
            document_type=document_type,
            fidelity=assessment.fidelity,
            fidelity_note=assessment.note,
        )

    def _generate_text(
        self, content: str, document_type: DocumentType, *, plain_text: bool
    ) -> bytes:
        if document_type in {DocumentType.TXT, DocumentType.MARKDOWN}:
            return content.encode("utf-8")
        if document_type == DocumentType.PDF:
            return self.pdf_generator.generate(content, plain_text=plain_text)
        if document_type == DocumentType.DOCX:
            return self.docx_generator.generate(content, plain_text=plain_text)
        if document_type == DocumentType.XLSX:
            return self.excel_generator.generate(content)
        if document_type == DocumentType.CSV:
            return self.csv_generator.generate(content)
        if document_type == DocumentType.PPTX:
            return self.pptx_generator.generate(content)
        raise ValueError(f"Unsupported generated document type: {document_type.value}.")

    def _generate_structured(
        self, content: StructuredDocumentContent, document_type: DocumentType
    ) -> bytes:
        if document_type == DocumentType.TXT:
            return content.to_plain_text().encode("utf-8")
        if document_type == DocumentType.MARKDOWN:
            return content.to_markdown().encode("utf-8")
        generators = {
            DocumentType.PDF: self.pdf_generator.generate_structured,
            DocumentType.DOCX: self.docx_generator.generate_structured,
            DocumentType.XLSX: self.excel_generator.generate_structured,
            DocumentType.CSV: self.csv_generator.generate_structured,
            DocumentType.PPTX: self.pptx_generator.generate_structured,
        }
        try:
            generator = generators[document_type]
        except KeyError as error:
            raise ValueError(
                f"Unsupported generated document type: {document_type.value}."
            ) from error
        return generator(content)

    @staticmethod
    def parse_ai_content(response: str) -> StructuredDocumentContent:
        """Parse only the validated JSON contract; never accept model-produced file bytes."""
        candidate = response.strip()
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL)
        if fence:
            candidate = fence.group(1).strip()
        if not candidate.startswith("{") or not candidate.endswith("}"):
            raise ValueError("The AI returned invalid structured document content.")
        try:
            payload = json.loads(candidate)
            return StructuredDocumentContent.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            raise ValueError("The AI returned invalid structured document content.") from error

    def generate_excel_from_tables(
        self,
        tables: list[ExtractedTable],
        *,
        requested_filename: str | None = None,
    ) -> GeneratedDocument:
        file_data = self.excel_generator.generate_tables(tables)
        document_type = DocumentType.XLSX
        assessment = self.validator.assess_generation(file_data, document_type)
        return GeneratedDocument(
            file_data=file_data,
            filename=self.safe_filename(
                requested_filename or "extracted-tables.xlsx", document_type
            ),
            content_type=self.MIME_TYPES[document_type],
            document_type=document_type,
            fidelity=assessment.fidelity,
            fidelity_note=assessment.note,
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
        # A title may contain periods ("U.S. Presidents"). Strip only a file
        # extension, so the rest of a dotted title does not get lost.
        suffix = Path(raw_name).suffix
        stem = Path(raw_name).stem if re.fullmatch(r"\.[a-zA-Z0-9]{1,10}", suffix) else raw_name
        ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
        safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", ascii_stem).strip("._-")[:100]
        if not safe_stem:
            safe_stem = "generated-document"
        if safe_stem.upper() in cls.WINDOWS_RESERVED_NAMES:
            safe_stem = f"generated-{safe_stem.lower()}"
        return f"{safe_stem}{cls.EXTENSIONS[document_type]}"
