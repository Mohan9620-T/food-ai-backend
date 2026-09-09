import asyncio
import csv
import logging
import re
from codecs import BOM_UTF16_BE, BOM_UTF16_LE
from contextlib import aclosing, closing
from copy import copy
from html import escape
from io import BytesIO, StringIO
from pathlib import Path

import pytesseract
from docx import Document
from openpyxl import load_workbook
from openpyxl.styles import Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter
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

    @staticmethod
    def is_spreadsheet_update_request(instruction: str | None) -> bool:
        """Return true only for an explicit workbook formatting request."""
        if not instruction:
            return False
        normalized = instruction.casefold()
        if ChatDocumentService._is_category_split_request(normalized):
            return True
        formatting_terms = (
            "align",
            "alignment",
            "center",
            "centre",
            "left align",
            "right align",
            "format",
            "style",
            "design",
            "professional",
            "auto fit",
            "autofit",
            "column width",
            "wrap text",
            "header",
            "bold",
            "border",
            "freeze",
            "arrange",
            "organize",
            "organise",
            "clean up",
        )
        return any(term in normalized for term in formatting_terms)

    def format_spreadsheet(
        self, file_data: bytes, filename: str, instruction: str
    ) -> tuple[bytes, str, list[str]]:
        """Apply safe presentation changes while preserving every workbook cell."""
        normalized = instruction.casefold()
        split_by_category = self._is_category_split_request(normalized)
        professional = split_by_category or any(
            term in normalized
            for term in (
                "professional",
                "format",
                "style",
                "design",
                "arrange",
                "organize",
                "organise",
            )
        )
        horizontal = None
        if "center" in normalized or "centre" in normalized:
            horizontal = "center"
        elif "right align" in normalized or "align right" in normalized:
            horizontal = "right"
        elif "left align" in normalized or "align left" in normalized:
            horizontal = "left"

        requested_columns = {
            column_index_from_string(match)
            for match in re.findall(r"\b(?:column|col)\s+([a-z]{1,3})\b", normalized)
        }
        style_headers = professional or "header" in normalized or "bold" in normalized
        auto_fit = professional or any(
            term in normalized for term in ("auto fit", "autofit", "column width")
        )
        wrap_text = professional or "wrap text" in normalized
        add_borders = professional or "border" in normalized
        freeze_header = professional or "freeze" in normalized
        smart_alignment = horizontal is None and (
            professional or "align" in normalized or "alignment" in normalized
        )

        try:
            workbook = load_workbook(BytesIO(file_data), data_only=False)
            try:
                category_sheet_count = (
                    self._split_sheets_by_category(workbook) if split_by_category else 0
                )
                thin_border = Border(
                    left=Side(style="thin", color="D9E2F3"),
                    right=Side(style="thin", color="D9E2F3"),
                    top=Side(style="thin", color="D9E2F3"),
                    bottom=Side(style="thin", color="D9E2F3"),
                )
                for sheet in workbook.worksheets:
                    populated_rows = [
                        row
                        for row in sheet.iter_rows()
                        if any(cell.value is not None for cell in row)
                    ]
                    if not populated_rows:
                        continue
                    header_row = populated_rows[0]
                    header_number = header_row[0].row
                    for row in populated_rows:
                        for cell in row:
                            if cell.value is None:
                                continue
                            if requested_columns and cell.column not in requested_columns:
                                continue
                            alignment = copy(cell.alignment)
                            if horizontal is not None:
                                alignment.horizontal = horizontal
                            elif smart_alignment:
                                alignment.horizontal = (
                                    "center"
                                    if cell.row == header_number
                                    else "right"
                                    if isinstance(cell.value, (int, float))
                                    and not isinstance(cell.value, bool)
                                    else "left"
                                )
                            alignment.vertical = "center"
                            if wrap_text:
                                alignment.wrap_text = True
                            cell.alignment = alignment
                            if add_borders:
                                cell.border = thin_border
                    if style_headers:
                        for cell in header_row:
                            if cell.value is None:
                                continue
                            cell.font = Font(
                                name=cell.font.name or "Calibri",
                                size=cell.font.sz or 11,
                                bold=True,
                                color="FFFFFF",
                            )
                            cell.fill = PatternFill("solid", fgColor="1F4E78")
                            alignment = copy(cell.alignment)
                            alignment.horizontal = horizontal or "center"
                            alignment.vertical = "center"
                            alignment.wrap_text = True
                            cell.alignment = alignment
                    if auto_fit:
                        for column in range(1, sheet.max_column + 1):
                            maximum = max(
                                (
                                    len(str(sheet.cell(row=row, column=column).value))
                                    for row in range(1, sheet.max_row + 1)
                                    if sheet.cell(row=row, column=column).value is not None
                                ),
                                default=0,
                            )
                            sheet.column_dimensions[get_column_letter(column)].width = min(
                                max(maximum + 2, 10), 60
                            )
                    if freeze_header:
                        sheet.freeze_panes = f"A{header_number + 1}"
                output = BytesIO()
                workbook.save(output)
            finally:
                workbook.close()
        except InvalidDocumentError:
            raise
        except Exception as error:
            raise InvalidDocumentError(
                "The spreadsheet could not be updated. Check that it is a valid XLSX file."
            ) from error

        actions = []
        if split_by_category:
            actions.append(
                f"split items into {category_sheet_count} category worksheets while retaining source sheets"
            )
        if horizontal:
            scope = (
                "columns "
                + ", ".join(get_column_letter(column) for column in sorted(requested_columns))
                if requested_columns
                else "all populated cells"
            )
            actions.append(f"{horizontal} alignment for {scope}")
        elif smart_alignment:
            actions.append("smart text and number alignment")
        if style_headers:
            actions.append("formatted header rows")
        if auto_fit:
            actions.append("auto-fitted column widths")
        if wrap_text:
            actions.append("wrapped long text")
        if add_borders:
            actions.append("added table borders")
        if freeze_header:
            actions.append("froze header rows")
        if not actions:
            actions.append("preserved the workbook without dropping any populated cells")

        safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(filename).stem).strip("-")
        return output.getvalue(), f"{safe_stem or 'spreadsheet'}-updated.xlsx", actions

    @staticmethod
    def _is_category_split_request(normalized_instruction: str) -> bool:
        has_category = "category" in normalized_instruction
        asks_for_sheets = any(
            term in normalized_instruction
            for term in (
                "separate sheet",
                "separate worksheet",
                "sheet for each",
                "worksheet for each",
                "category-wise",
                "category wise",
                "split",
                "group into sheet",
                "group into worksheet",
            )
        )
        return has_category and asks_for_sheets

    @classmethod
    def _split_sheets_by_category(cls, workbook) -> int:
        created_count = 0
        source_sheets = list(workbook.worksheets)
        for source in source_sheets:
            populated_rows = [
                row for row in source.iter_rows() if any(cell.value is not None for cell in row)
            ]
            if len(populated_rows) < 2:
                continue
            header_row = populated_rows[0]
            category_cell = next(
                (
                    cell
                    for cell in header_row
                    if cell.value is not None and "category" in str(cell.value).casefold()
                ),
                None,
            )
            if category_cell is None:
                continue

            grouped_rows: dict[str, list[tuple]] = {}
            for row in populated_rows[1:]:
                category_value = row[category_cell.column - 1].value
                category = str(category_value).strip() if category_value is not None else ""
                grouped_rows.setdefault(category or "Uncategorized", []).append(row)

            for category, rows in grouped_rows.items():
                target = workbook.create_sheet(
                    cls._unique_category_sheet_title(workbook, source.title, category)
                )
                cls._copy_spreadsheet_row(header_row, target, 1)
                for target_row, source_row in enumerate(rows, start=2):
                    cls._copy_spreadsheet_row(source_row, target, target_row)
                target.auto_filter.ref = f"A1:{get_column_letter(source.max_column)}{len(rows) + 1}"
                created_count += 1

        if created_count == 0:
            raise InvalidDocumentError(
                "I could not find a Category column with item rows in this workbook. "
                "Add a column header containing 'Category' and try again."
            )
        return created_count

    @staticmethod
    def _copy_spreadsheet_row(source_row, target_sheet, target_row: int) -> None:
        for source_cell in source_row:
            target_cell = target_sheet.cell(row=target_row, column=source_cell.column)
            target_cell.value = source_cell.value
            if source_cell.has_style:
                target_cell.font = copy(source_cell.font)
                target_cell.fill = copy(source_cell.fill)
                target_cell.border = copy(source_cell.border)
                target_cell.alignment = copy(source_cell.alignment)
                target_cell.number_format = source_cell.number_format
                target_cell.protection = copy(source_cell.protection)
            if source_cell.hyperlink:
                target_cell.hyperlink = copy(source_cell.hyperlink)
            if source_cell.comment:
                target_cell.comment = copy(source_cell.comment)

    @staticmethod
    def _unique_category_sheet_title(workbook, source_title: str, category: str) -> str:
        cleaned = re.sub(r"[\\/*?:\[\]]", "-", category).strip(" '") or "Uncategorized"
        candidate = cleaned[:31]
        if candidate.casefold() not in {sheet.title.casefold() for sheet in workbook.worksheets}:
            return candidate
        base = re.sub(r"[\\/*?:\[\]]", "-", f"{source_title}-{cleaned}").strip(" '")
        existing = {sheet.title.casefold() for sheet in workbook.worksheets}
        for suffix in range(2, 10_000):
            suffix_text = f"-{suffix}"
            candidate = f"{base[: 31 - len(suffix_text)]}{suffix_text}"
            if candidate.casefold() not in existing:
                return candidate
        raise InvalidDocumentError("The workbook contains too many duplicate category names.")

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
