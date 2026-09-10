from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.services.document.content_parser import content_lines


class PdfGenerator:
    def generate(self, content: str, *, plain_text: bool = False) -> bytes:
        try:
            content.encode("cp1252")
        except UnicodeEncodeError as error:
            raise ValueError(
                "PDF cannot represent some characters; choose Word (.docx) to preserve the text."
            ) from error

        buffer = BytesIO()
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
        story = []
        for line in content_lines(content, plain_text=plain_text):
            if line.kind == "blank":
                story.append(Spacer(1, 4 * mm))
                continue
            if line.kind == "heading":
                story.append(Paragraph(escape(line.text), headings[min(3, line.level)]))
            elif line.kind == "bullet":
                story.append(Paragraph(escape(line.text), body, bulletText="&#8226;"))
            elif line.kind == "number":
                story.append(Paragraph(escape(line.text), body, bulletText="-"))
            else:
                story.append(Paragraph(escape(line.text), body))
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
