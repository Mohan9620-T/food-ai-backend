import csv
import shutil
import subprocess
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import settings
from app.services.document.document_generation_service import (
    DocumentGenerationService,
    GeneratedDocument,
)
from app.services.document.document_operation_registry import (
    DocumentOperationRegistry,
    DocumentType,
    Fidelity,
)
from app.services.document.document_reading_service import DocumentReadingService
from app.services.document.document_validation_service import DocumentValidationService
from app.services.document.exceptions import DocumentProcessingUnavailableError
from app.services.document.extraction_models import (
    GeneratedTableContent,
    StructuredDocumentContent,
)


class UnsupportedDocumentConversionError(ValueError):
    """The requested source/target pair has no faithful deterministic handler."""


@dataclass(frozen=True)
class ConversionCapability:
    source_type: DocumentType
    output_type: DocumentType
    fidelity: Fidelity
    handler: str
    requires_libreoffice: bool = False
    limitation: str | None = None


class LibreOfficeConverter:
    """Use headless LibreOffice for layout-preserving office-to-PDF conversion."""

    WINDOWS_CANDIDATES = (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    )

    def __init__(self, binary: str | None = None, timeout_seconds: int | None = None) -> None:
        self.binary = binary if binary is not None else self._discover_binary()
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.DOCUMENT_CONVERSION_TIMEOUT_SECONDS
        )

    @classmethod
    def _discover_binary(cls) -> str | None:
        if settings.LIBREOFFICE_BINARY:
            configured = Path(settings.LIBREOFFICE_BINARY).expanduser()
            return str(configured.resolve()) if configured.is_file() else None
        discovered = shutil.which("soffice") or shutil.which("libreoffice")
        if discovered:
            return str(Path(discovered).resolve())
        return next(
            (str(path.resolve()) for path in cls.WINDOWS_CANDIDATES if path.is_file()), None
        )

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def convert_to_pdf(self, file_data: bytes, filename: str) -> bytes:
        if not self.binary:
            raise DocumentProcessingUnavailableError(
                "DOCX/PPTX to PDF conversion requires headless LibreOffice. "
                "Install LibreOffice or set LIBREOFFICE_BINARY to the soffice executable."
            )
        source_type = DocumentOperationRegistry.document_type_from_filename(filename)
        if source_type not in {DocumentType.DOCX, DocumentType.PPTX, DocumentType.XLSX}:
            raise UnsupportedDocumentConversionError(
                "LibreOffice PDF conversion requires a DOCX, PPTX, or XLSX source."
            )
        safe_name = DocumentGenerationService.safe_filename(filename, source_type)
        with TemporaryDirectory(prefix="food-ai-convert-") as directory:
            workdir = Path(directory)
            source = workdir / safe_name
            source.write_bytes(file_data)
            try:
                completed = subprocess.run(
                    [
                        self.binary,
                        "--headless",
                        f"-env:UserInstallation={workdir.as_uri()}/profile",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(workdir),
                        str(source),
                    ],
                    check=False,
                    capture_output=True,
                    shell=False,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                raise DocumentProcessingUnavailableError(
                    "LibreOffice timed out while converting the document to PDF."
                ) from error
            except OSError as error:
                raise DocumentProcessingUnavailableError(
                    "LibreOffice could not be started for document conversion."
                ) from error
            output = source.with_suffix(".pdf")
            if completed.returncode != 0 or not output.is_file():
                raise DocumentProcessingUnavailableError(
                    "LibreOffice could not convert this document to PDF."
                )
            return output.read_bytes()


class DocumentConversionService:
    """Strict, deterministic conversion handlers for explicitly supported pairs."""

    CAPABILITY_MATRIX = {
        (capability.source_type, capability.output_type): capability
        for capability in (
            ConversionCapability(
                DocumentType.PDF,
                DocumentType.DOCX,
                Fidelity.BEST_EFFORT,
                "pdf_to_docx",
                limitation=(
                    "The Word file is reconstructed from extracted PDF content and is not "
                    "pixel-perfect."
                ),
            ),
            ConversionCapability(
                DocumentType.PDF,
                DocumentType.XLSX,
                Fidelity.PARTIAL,
                "pdf_to_xlsx",
                limitation=(
                    "Only tables detected in the PDF are included; complex table layout may "
                    "not be preserved."
                ),
            ),
            ConversionCapability(
                DocumentType.PDF,
                DocumentType.TXT,
                Fidelity.HIGH,
                "extract_to_text",
                limitation=(
                    "The text file preserves extracted reading order, not the PDF's visual layout."
                ),
            ),
            ConversionCapability(
                DocumentType.PDF,
                DocumentType.MARKDOWN,
                Fidelity.PARTIAL,
                "extract_to_text",
                limitation=(
                    "Markdown is reconstructed from extracted PDF text; complex visual structure "
                    "is not retained."
                ),
            ),
            ConversionCapability(
                DocumentType.DOCX,
                DocumentType.PDF,
                Fidelity.HIGH,
                "office_to_pdf",
                requires_libreoffice=True,
                limitation="LibreOffice rendering can introduce minor layout differences.",
            ),
            ConversionCapability(
                DocumentType.DOCX,
                DocumentType.TXT,
                Fidelity.HIGH,
                "extract_to_text",
                limitation="The text file preserves content and reading order, not Word styling.",
            ),
            ConversionCapability(
                DocumentType.DOCX,
                DocumentType.MARKDOWN,
                Fidelity.HIGH,
                "extract_to_text",
                limitation=(
                    "Markdown preserves extracted headings, paragraphs, and tables where "
                    "available, but not all Word styling."
                ),
            ),
            ConversionCapability(
                DocumentType.PPTX,
                DocumentType.PDF,
                Fidelity.HIGH,
                "office_to_pdf",
                requires_libreoffice=True,
                limitation="LibreOffice rendering can introduce minor layout differences.",
            ),
            ConversionCapability(
                DocumentType.XLSX,
                DocumentType.PDF,
                Fidelity.HIGH,
                "office_to_pdf",
                requires_libreoffice=True,
                limitation=(
                    "LibreOffice uses the workbook's print settings and can introduce minor "
                    "layout differences."
                ),
            ),
            ConversionCapability(
                DocumentType.XLSX,
                DocumentType.CSV,
                Fidelity.PARTIAL,
                "xlsx_to_csv",
                limitation="CSV preserves cell values only; workbook formatting is not retained.",
            ),
            ConversionCapability(
                DocumentType.CSV,
                DocumentType.XLSX,
                Fidelity.HIGH,
                "csv_to_xlsx",
            ),
            ConversionCapability(
                DocumentType.TXT,
                DocumentType.MARKDOWN,
                Fidelity.FULL,
                "text_copy",
            ),
            ConversionCapability(
                DocumentType.MARKDOWN,
                DocumentType.TXT,
                Fidelity.FULL,
                "text_copy",
            ),
        )
    }
    SUPPORTED_PAIRS = frozenset(CAPABILITY_MATRIX)

    def __init__(
        self,
        *,
        reader: DocumentReadingService | None = None,
        generator: DocumentGenerationService | None = None,
        validator: DocumentValidationService | None = None,
        libreoffice: LibreOfficeConverter | None = None,
    ) -> None:
        self.reader = reader or DocumentReadingService()
        self.generator = generator or DocumentGenerationService()
        self.validator = validator or DocumentValidationService()
        self.libreoffice = libreoffice or LibreOfficeConverter()

    def ensure_supported(self, source: DocumentType, target: DocumentType) -> ConversionCapability:
        capability = self.CAPABILITY_MATRIX.get((source, target))
        if capability is None:
            raise UnsupportedDocumentConversionError(
                f"Conversion from {source.value.upper()} to {target.value.upper()} is not "
                "supported. The source file was not changed."
            )
        if capability.requires_libreoffice and not self.libreoffice.available:
            raise DocumentProcessingUnavailableError(
                f"Conversion from {source.value.upper()} to PDF requires headless LibreOffice, "
                "which is not available on this server. The source file was not changed."
            )
        return capability

    def available_capabilities(self) -> tuple[ConversionCapability, ...]:
        return tuple(
            capability
            for capability in self.CAPABILITY_MATRIX.values()
            if not capability.requires_libreoffice or self.libreoffice.available
        )

    def convert(
        self,
        file_data: bytes,
        filename: str,
        output_type: DocumentType,
    ) -> GeneratedDocument:
        source_type = DocumentOperationRegistry.document_type_from_filename(filename)
        if source_type is None:
            raise UnsupportedDocumentConversionError("The source document type is not supported.")
        self.validator.validate(file_data, source_type)
        capability = self.ensure_supported(source_type, output_type)
        output_name = self.output_filename(filename, output_type)

        if capability.handler == "pdf_to_docx":
            extracted = self.reader.read(file_data, filename)
            generated = self.generator.generate(
                self._pdf_word_content(extracted, filename),
                DocumentType.DOCX.value,
                requested_filename=output_name,
            )
        elif capability.handler == "pdf_to_xlsx":
            extracted = self.reader.read(file_data, filename)
            if not extracted.tables:
                raise UnsupportedDocumentConversionError(
                    "No extractable tables were found in this PDF. No empty Excel workbook was "
                    "created, and the source PDF is unchanged."
                )
            generated = self.generator.generate_excel_from_tables(
                list(extracted.tables), requested_filename=output_name
            )
        elif capability.handler == "csv_to_xlsx":
            extracted = self.reader.read(file_data, filename)
            generated = self.generator.generate_excel_from_tables(
                list(extracted.tables), requested_filename=output_name
            )
        elif capability.handler == "xlsx_to_csv":
            extracted = self.reader.read(file_data, filename)
            if len(extracted.tables) != 1:
                raise UnsupportedDocumentConversionError(
                    "XLSX to CSV conversion requires exactly one populated worksheet. "
                    "Choose a worksheet explicitly before converting a multi-sheet workbook."
                )
            buffer = StringIO(newline="")
            writer = csv.writer(buffer, lineterminator="\r\n")
            writer.writerows(extracted.tables[0].rows)
            generated = self._document(
                buffer.getvalue().encode("utf-8-sig"), output_name, output_type
            )
        elif capability.handler == "text_copy":
            extracted = self.reader.read(file_data, filename)
            generated = self._document(extracted.text.encode("utf-8"), output_name, output_type)
        elif capability.handler == "extract_to_text":
            extracted = self.reader.read(file_data, filename)
            content = (
                f"# {Path(filename).stem}\n\n{extracted.text}"
                if output_type == DocumentType.MARKDOWN
                else extracted.text
            )
            generated = self._document(content.encode("utf-8"), output_name, output_type)
        elif capability.handler == "office_to_pdf":
            converted = self.libreoffice.convert_to_pdf(file_data, filename)
            generated = self._document(converted, output_name, output_type)
        else:  # pragma: no cover - the static matrix is exhaustively tested
            raise UnsupportedDocumentConversionError(
                "The registered conversion handler is unavailable. The source file was not changed."
            )

        assessment = self.validator.assess_conversion(
            file_data,
            source_type,
            generated.file_data,
            generated.document_type,
            expected=capability.fidelity,
            limitation=capability.limitation,
        )
        return replace(
            generated,
            fidelity=assessment.fidelity,
            fidelity_note=assessment.note,
        )

    @staticmethod
    def _pdf_word_content(extracted, filename: str) -> StructuredDocumentContent:
        paragraphs = [
            block.text.strip()
            for block in extracted.blocks
            if block.text.strip() and not block.cells
        ]
        if not paragraphs and extracted.text.strip():
            paragraphs = [extracted.text.strip()]
        tables: list[GeneratedTableContent] = []
        for index, extracted_table in enumerate(extracted.tables, start=1):
            rows = [list(row) for row in extracted_table.rows]
            headers: list[str] = []
            if extracted_table.header_detected and rows:
                headers = rows.pop(0)
            tables.append(
                GeneratedTableContent(
                    title=extracted_table.name or f"Table {index}",
                    headers=headers,
                    rows=rows,
                )
            )
        return StructuredDocumentContent(
            title=f"{Path(filename).stem} - Reconstructed",
            paragraphs=paragraphs,
            tables=tables,
        )

    def _document(
        self, file_data: bytes, filename: str, document_type: DocumentType
    ) -> GeneratedDocument:
        self.validator.validate(file_data, document_type)
        return GeneratedDocument(
            file_data=file_data,
            filename=filename,
            content_type=DocumentGenerationService.MIME_TYPES[document_type],
            document_type=document_type,
        )

    @staticmethod
    def output_filename(filename: str, output_type: DocumentType) -> str:
        stem = Path(filename).stem or "document"
        requested = f"{stem}-converted{DocumentGenerationService.EXTENSIONS[output_type]}"
        return DocumentGenerationService.safe_filename(requested, output_type)
