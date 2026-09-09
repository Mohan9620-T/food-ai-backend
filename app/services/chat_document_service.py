import asyncio
import csv
import logging
import re
from codecs import BOM_UTF16_BE, BOM_UTF16_LE
from contextlib import aclosing, closing
from html import escape
from io import BytesIO, StringIO
from pathlib import Path

import pytesseract
from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader
from pypdf.errors import DependencyError
from pypdfium2 import PdfDocument
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.config import settings
from app.schemas.chat import ChatHistoryMessage
from app.services.chat_service import ChatModelUnavailableError, ChatService

logger = logging.getLogger(__name__)


class InvalidDocumentError(ValueError):
    pass


class DocumentProcessingUnavailableError(RuntimeError):
    pass


class ChatDocumentService:
    OCR_PAGE_TIMEOUT_SECONDS = 30
    MAX_DOCUMENT_CONTEXT_CHARS = 30_000

    def __init__(self, chat_service: ChatService | None = None):
        self.chat_service = chat_service or ChatService()

    def extract(self, file_data: bytes, filename: str) -> str:
        extension = Path(filename).suffix.lower()
        try:
            if extension == ".pdf":
                text = self._extract_pdf(file_data)
            elif extension == ".docx":
                document = Document(BytesIO(file_data))
                text = "\n".join(paragraph.text for paragraph in document.paragraphs)
                for table in document.tables:
                    text += "\n" + "\n".join(
                        "\t".join(cell.text for cell in row.cells) for row in table.rows
                    )
            elif extension == ".xlsx":
                sheets = []
                with closing(
                    load_workbook(BytesIO(file_data), read_only=True, data_only=True)
                ) as workbook:
                    for sheet in workbook.worksheets:
                        rows = "\n".join(
                            "\t".join("" if value is None else str(value) for value in row)
                            for row in sheet.iter_rows(values_only=True)
                            if any(value is not None for value in row)
                        )
                        if rows.strip():
                            sheets.append(f"[{sheet.title}]\n{rows}")
                text = "\n".join(sheets)
            elif extension == ".csv":
                decoded = self._decode_text(file_data)
                text = "\n".join("\t".join(row) for row in csv.reader(StringIO(decoded)))
            elif extension == ".txt":
                text = self._decode_text(file_data)
            else:
                raise InvalidDocumentError("Unsupported document type.")
        except (InvalidDocumentError, DocumentProcessingUnavailableError):
            raise
        except DependencyError as error:
            raise DocumentProcessingUnavailableError(
                "The server cannot read this PDF's encryption. Upload an unencrypted copy."
            ) from error
        except Exception as error:
            raise InvalidDocumentError("The document is corrupt or could not be read.") from error
        if not text.strip():
            raise InvalidDocumentError("The document does not contain readable text.")
        return text.strip()

    async def summarize(self, raw_text: str, instruction: str | None) -> str:
        prompt = (
            "Read the uploaded document and respond to the user note using only its supported facts. "
            "If there is no specific request, provide a concise, structured summary. "
            "Preserve relevant headings, subheadings, names, dates, values, and table details. "
            "Summarize the actual subject of the document; do not omit information just because "
            "it is unrelated to nutrition. For health information, do not diagnose.\n\n"
            f"User note: {instruction or 'No additional note.'}\n\n"
            f"Document:\n{self._source_excerpt([raw_text])}"
        )
        return await self._complete(prompt, [])

    async def generate_content(
        self,
        instruction: str,
        summaries: list[str],
        history: list[ChatHistoryMessage],
        profile: dict | None = None,
    ) -> str:
        source = self._source_excerpt(summaries) or "No uploaded-document summary is available."
        prompt = (
            "Create document-ready content with a clear title, headings, concise sections, and "
            "actionable lists. Use only the supplied facts; flag missing information and do not "
            "provide a medical diagnosis.\n\n"
            f"Requested document: {instruction}\n\nUploaded document summaries:\n{source}"
            f"\n\nNutrition profile:\n{profile or 'No profile available.'}"
        )
        return await self._complete(prompt, history)

    @classmethod
    def _source_excerpt(cls, sources: list[str]) -> str:
        """Bound only AI context; saved source text and direct exports stay complete."""
        total_chars = sum(len(source) for source in sources) + max(0, len(sources) - 1) * 2
        remaining = cls.MAX_DOCUMENT_CONTEXT_CHARS
        parts = []
        for index, source in enumerate(sources):
            if index:
                separator = "\n\n"[:remaining]
                parts.append(separator)
                remaining -= len(separator)
            if remaining == 0:
                break
            excerpt = source[:remaining]
            parts.append(excerpt)
            remaining -= len(excerpt)
        excerpt = "".join(parts)
        if total_chars > cls.MAX_DOCUMENT_CONTEXT_CHARS:
            disclosure = (
                f"[Source excerpt truncated: first {cls.MAX_DOCUMENT_CONTEXT_CHARS:,} "
                f"of {total_chars:,} characters supplied. The remaining source content was not "
                "provided. Tell the user this limitation; do not claim to have read the full "
                "document or invent details from omitted content.]\n\n"
            )
            return disclosure + excerpt
        return excerpt

    async def _complete(self, prompt: str, history: list[ChatHistoryMessage]) -> str:
        # One deadline covers NVIDIA, any Ollama fallback, and all response tokens.
        # Cancelling async HTTP closes the connection instead of leaving a worker
        # thread generating for another five minutes after the UI reports failure.
        try:
            async with asyncio.timeout(settings.DOCUMENT_AI_TIMEOUT_SECONDS):
                async with aclosing(self.chat_service.stream_chat(prompt, history, [])) as stream:
                    chunks = [chunk async for chunk in stream]
        except TimeoutError as error:
            logger.warning("chat.document_ai_timeout")
            raise ChatModelUnavailableError(
                "Document AI generation timed out. Your draft is unchanged. Retry, or select "
                "'Export text without AI' to save your existing text as PDF or Word."
            ) from error
        except ChatModelUnavailableError as error:
            raise ChatModelUnavailableError(
                "Document AI generation could not finish. Your draft is unchanged. Retry, or "
                "select 'Export text without AI' to save your existing text as PDF or Word."
            ) from error
        content = "".join(chunks)
        if not content.strip():
            raise ChatModelUnavailableError(
                "The AI returned no document content. Retry, or use 'Export text without AI'."
            )
        return content

    def render(
        self, content: str, output_format: str, *, plain_text: bool = False
    ) -> tuple[bytes, str, str]:
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", "nutrition-document").strip("-")
        if output_format == "docx":
            document = Document()
            for line in content.splitlines():
                if plain_text:
                    document.add_paragraph(line)
                    continue
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    document.add_heading(
                        stripped.lstrip("# "),
                        level=min(3, len(stripped) - len(stripped.lstrip("#"))),
                    )
                else:
                    document.add_paragraph(stripped)
            buffer = BytesIO()
            document.save(buffer)
            return (
                buffer.getvalue(),
                f"{safe_name}.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        buffer = BytesIO()
        # Built-in ReportLab fonts use WinAnsi; unsupported Unicode otherwise
        # becomes black squares even though the API reports a successful export.
        try:
            content.encode("cp1252")
        except UnicodeEncodeError as error:
            raise InvalidDocumentError(
                "PDF cannot represent some characters; choose Word (.docx) to preserve the text."
            ) from error
        pdf = SimpleDocTemplate(buffer, pagesize=A4, title="Nutrition Document")
        styles = getSampleStyleSheet()
        story = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                story.append(Spacer(1, 8))
            else:
                style = (
                    styles["Heading2"]
                    if not plain_text and stripped.startswith("#")
                    else styles["BodyText"]
                )
                text = line if plain_text else stripped.lstrip("# ")
                story.append(Paragraph(escape(text), style))
                story.append(Spacer(1, 5))
        pdf.build(story)
        return buffer.getvalue(), f"{safe_name}.pdf", "application/pdf"

    @staticmethod
    def _decode_text(file_data: bytes) -> str:
        encoding = "utf-16" if file_data.startswith((BOM_UTF16_LE, BOM_UTF16_BE)) else "utf-8-sig"
        try:
            text = file_data.decode(encoding)
        except UnicodeDecodeError as error:
            raise InvalidDocumentError(
                "The text file uses an unsupported encoding. Save it as UTF-8 or UTF-16 and upload it again."
            ) from error
        if any(ord(character) < 32 and character not in "\t\n\r\f" for character in text):
            raise InvalidDocumentError("The file contains binary data instead of readable text.")
        return text

    def _extract_pdf(self, file_data: bytes) -> str:
        reader = PdfReader(BytesIO(file_data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise InvalidDocumentError(
                "This PDF is password-protected. Remove its password and upload it again."
            )
        pages = []
        ocr_indexes = []
        for index, source_page in enumerate(reader.pages):
            text = source_page.extract_text() or ""
            pages.append(text)
            # Retain searchable pages and only render pages that need OCR.
            # A page with no content stream is physically empty, so skip it.
            if not text.strip() and source_page.get_contents() is not None:
                ocr_indexes.append(index)
        if not ocr_indexes:
            return "\n".join(pages)
        try:
            with closing(PdfDocument(file_data)) as pdf:
                for index in ocr_indexes:
                    with closing(pdf[index]) as page:
                        with closing(page.render(scale=2)) as bitmap:
                            # PDFium can create a Pillow image directly; to_numpy()
                            # requires an optional dependency that we do not install.
                            with closing(bitmap.to_pil()) as image:
                                pages[index] = pytesseract.image_to_string(
                                    image, timeout=self.OCR_PAGE_TIMEOUT_SECONDS
                                )
        except pytesseract.TesseractNotFoundError as error:
            raise DocumentProcessingUnavailableError(
                "This PDF contains scanned pages, but OCR is unavailable on the server. "
                "Upload a searchable PDF or ask the administrator to enable OCR."
            ) from error
        except pytesseract.TesseractError as error:
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
        return "\n".join(pages)
