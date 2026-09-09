import csv
import re
from io import BytesIO, StringIO
from pathlib import Path

import pytesseract
from docx import Document
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader
from pypdfium2 import PdfDocument
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.schemas.chat import ChatHistoryMessage
from app.services.chat_service import ChatService


class InvalidDocumentError(ValueError):
    pass


class ChatDocumentService:
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
                workbook = load_workbook(BytesIO(file_data), read_only=True, data_only=True)
                text = "\n".join(
                    f"[{sheet.title}]\n" + "\n".join(
                        "\t".join("" if value is None else str(value) for value in row)
                        for row in sheet.iter_rows(values_only=True)
                    )
                    for sheet in workbook.worksheets
                )
            elif extension == ".csv":
                decoded = file_data.decode("utf-8-sig")
                text = "\n".join("\t".join(row) for row in csv.reader(StringIO(decoded)))
            elif extension == ".txt":
                text = file_data.decode("utf-8-sig")
            else:
                raise InvalidDocumentError("Unsupported document type.")
        except InvalidDocumentError:
            raise
        except Exception as error:
            raise InvalidDocumentError("The document is corrupt or could not be read.") from error
        if not text.strip():
            raise InvalidDocumentError("The document does not contain readable text.")
        return text.strip()

    def summarize(self, raw_text: str, instruction: str | None) -> str:
        prompt = (
            "Extract a concise, structured nutrition-assistant summary from this document. "
            "Include only supported facts such as lab values, diet plans, ingredients, allergies, "
            "restrictions, medications, dates, and food preferences. Do not diagnose.\n\n"
            f"User note: {instruction or 'No additional note.'}\n\nDocument:\n{raw_text[:30000]}"
        )
        return self.chat_service.chat(prompt, [], [])

    def generate_content(
        self,
        instruction: str,
        summaries: list[str],
        history: list[ChatHistoryMessage],
        profile: dict | None = None,
    ) -> str:
        source = "\n\n".join(summaries) or "No uploaded-document summary is available."
        prompt = (
            "Create document-ready content with a clear title, headings, concise sections, and "
            "actionable lists. Use only the supplied facts; flag missing information and do not "
            "provide a medical diagnosis.\n\n"
            f"Requested document: {instruction}\n\nUploaded document summaries:\n{source}"
            f"\n\nNutrition profile:\n{profile or 'No profile available.'}"
        )
        return self.chat_service.chat(prompt, history, [])

    def render(self, content: str, output_format: str) -> tuple[bytes, str, str]:
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", "nutrition-document").strip("-")
        if output_format == "docx":
            document = Document()
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    document.add_heading(stripped.lstrip("# "), level=min(3, len(stripped) - len(stripped.lstrip("#"))))
                else:
                    document.add_paragraph(stripped)
            buffer = BytesIO()
            document.save(buffer)
            return buffer.getvalue(), f"{safe_name}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        buffer = BytesIO()
        pdf = SimpleDocTemplate(buffer, pagesize=A4, title="Nutrition Document")
        styles = getSampleStyleSheet()
        story = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                story.append(Spacer(1, 8))
            else:
                style = styles["Heading2"] if stripped.startswith("#") else styles["BodyText"]
                story.append(Paragraph(stripped.lstrip("# "), style))
                story.append(Spacer(1, 5))
        pdf.build(story)
        return buffer.getvalue(), f"{safe_name}.pdf", "application/pdf"

    def _extract_pdf(self, file_data: bytes) -> str:
        reader = PdfReader(BytesIO(file_data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return text
        pdf = PdfDocument(file_data)
        pages = []
        for page in pdf:
            bitmap = page.render(scale=2)
            image = Image.fromarray(bitmap.to_numpy())
            pages.append(pytesseract.image_to_string(image))
        return "\n".join(pages)
