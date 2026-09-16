from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.document.content_parser import content_lines
from app.services.document.extraction_models import (
    StructuredDocumentContent,
)


class PdfGenerator:
    def generate(self, content: str, *, plain_text: bool = False) -> bytes:
        self._ensure_supported_text(content)
        buffer = BytesIO()
        styles, body, headings = self._styles()
        story = []
        for line in content_lines(content, plain_text=plain_text):
            if line.kind == "blank":
                story.append(Spacer(1, 4 * mm))
                continue
            if line.kind == "heading":
                story.append(Paragraph(escape(line.text), headings[min(3, line.level)]))
            elif line.kind == "bullet":
                story.append(Paragraph(escape(line.text), body, bulletText="•"))
            elif line.kind == "number":
                story.append(Paragraph(escape(line.text), body, bulletText="-"))
            else:
                story.append(Paragraph(escape(line.text), body))
        return self._build(buffer, story)

    def generate_structured(self, content: StructuredDocumentContent) -> bytes:
        self._ensure_supported_text(content.to_plain_text())
        buffer = BytesIO()
        _, body, headings = self._styles()
        story = [Paragraph(escape(content.title), headings[1])]
        self._append_blocks(
            story, content.paragraphs, content.bullet_lists, content.tables, body, headings
        )
        for section in content.sections:
            story.append(Paragraph(escape(section.heading), headings[2]))
            self._append_blocks(
                story,
                section.paragraphs,
                section.bullet_lists,
                section.tables,
                body,
                headings,
            )
        return self._build(buffer, story)

    @staticmethod
    def _ensure_supported_text(content: str) -> None:
        try:
            content.encode("cp1252")
        except UnicodeEncodeError as error:
            raise ValueError(
                "PDF cannot represent some characters; choose Word (.docx) to preserve the text."
            ) from error

    @staticmethod
    def _styles():
        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            "DocumentBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            textColor=colors.HexColor("#1F2937"),
            alignment=TA_LEFT,
            spaceAfter=6,
        )
        headings = {
            1: ParagraphStyle(
                "DocumentH1",
                parent=styles["Heading1"],
                fontSize=18,
                leading=22,
                textColor=colors.HexColor("#1F4E78"),
                spaceBefore=10,
                spaceAfter=8,
            ),
            2: ParagraphStyle(
                "DocumentH2",
                parent=styles["Heading2"],
                fontSize=14,
                leading=18,
                textColor=colors.HexColor("#2E74B5"),
                spaceBefore=8,
                spaceAfter=6,
            ),
            3: ParagraphStyle(
                "DocumentH3",
                parent=styles["Heading3"],
                fontSize=12,
                leading=15,
                textColor=colors.HexColor("#1F4E78"),
                spaceBefore=6,
                spaceAfter=4,
            ),
        }
        return styles, body, headings

    @staticmethod
    def _append_blocks(story, paragraphs, bullet_lists, tables, body, headings) -> None:
        for text in paragraphs:
            story.append(Paragraph(escape(text), body))
        for items in bullet_lists:
            for item in items:
                story.append(Paragraph(escape(item), body, bulletText="•"))
        for content_table in tables:
            story.append(Paragraph(escape(content_table.title), headings[3]))
            rows = []
            if content_table.headers:
                rows.append([Paragraph(escape(value), body) for value in content_table.headers])
            rows.extend(
                [Paragraph(escape(value), body) for value in values]
                for values in content_table.rows
            )
            table = Table(rows, repeatRows=1 if content_table.headers else 0)
            table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ]
                )
            )
            story.extend((table, Spacer(1, 3 * mm)))

    @staticmethod
    def _build(buffer: BytesIO, story) -> bytes:
        pdf = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            title="Generated Document",
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )
        pdf.build(story)
        return buffer.getvalue()
