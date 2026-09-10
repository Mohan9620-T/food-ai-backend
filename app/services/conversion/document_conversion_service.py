import csv
import shutil
import subprocess
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
)
from app.services.document.document_reading_service import DocumentReadingService
from app.services.document.document_validation_service import DocumentValidationService
from app.services.document.exceptions import DocumentProcessingUnavailableError


class UnsupportedDocumentConversionError(ValueError):
    """The requested source/target pair has no faithful deterministic handler."""


class LibreOfficeConverter:
    """Use headless LibreOffice for layout-preserving office-to-PDF conversion."""

    WINDOWS_CANDIDATES = (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    )

    def __init__(self, binary: str | None = None, timeout_seconds: int = 90) -> None:
        self.binary = binary or settings.LIBREOFFICE_BINARY or self._discover_binary()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def _discover_binary(cls) -> str | None:
        discovered = shutil.which("soffice") or shutil.which("libreoffice")
        if discovered:
            return discovered
        return next((str(path) for path in cls.WINDOWS_CANDIDATES if path.is_file()), None)

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def convert_to_pdf(self, file_data: bytes, filename: str) -> bytes:
        if not self.binary:
            raise DocumentProcessingUnavailableError(
                "DOCX/PPTX to PDF conversion requires headless LibreOffice. "
                "Install LibreOffice or set LIBREOFFICE_BINARY to the soffice executable."
            )
        safe_name = Path(filename).name
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
                detail = (completed.stderr or completed.stdout).strip()
                suffix = f" ({detail[:200]})" if detail else ""
                raise DocumentProcessingUnavailableError(
                    f"LibreOffice could not convert this document to PDF{suffix}."
                )
            return output.read_bytes()


class DocumentConversionService:
    """Strict, deterministic conversion handlers for explicitly supported pairs."""

    SUPPORTED_PAIRS = frozenset(
        {
            (DocumentType.CSV, DocumentType.XLSX),
            (DocumentType.XLSX, DocumentType.CSV),
            (DocumentType.TXT, DocumentType.MARKDOWN),
            (DocumentType.MARKDOWN, DocumentType.TXT),
            (DocumentType.DOCX, DocumentType.PDF),
            (DocumentType.PPTX, DocumentType.PDF),
        }
    )

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

    def ensure_supported(self, source: DocumentType, target: DocumentType) -> None:
        if (source, target) not in self.SUPPORTED_PAIRS:
            raise UnsupportedDocumentConversionError(
                f"Conversion from {source.value.upper()} to {target.value.upper()} is not "
                "supported. The source file was not changed."
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
        self.ensure_supported(source_type, output_type)
        self.validator.validate(file_data, source_type)
        output_name = self.output_filename(filename, output_type)

        if source_type == DocumentType.CSV and output_type == DocumentType.XLSX:
            extracted = self.reader.read(file_data, filename)
            generated = self.generator.generate_excel_from_tables(
                list(extracted.tables), requested_filename=output_name
            )
        elif source_type == DocumentType.XLSX and output_type == DocumentType.CSV:
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
        elif {source_type, output_type} == {DocumentType.TXT, DocumentType.MARKDOWN}:
            extracted = self.reader.read(file_data, filename)
            generated = self._document(extracted.text.encode("utf-8"), output_name, output_type)
        else:
            converted = self.libreoffice.convert_to_pdf(file_data, filename)
            generated = self._document(converted, output_name, output_type)

        self.validator.validate(generated.file_data, generated.document_type)
        return generated

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
