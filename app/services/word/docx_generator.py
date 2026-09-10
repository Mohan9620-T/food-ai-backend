from io import BytesIO

from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.shared import Inches, Pt, RGBColor

from app.services.document.content_parser import content_lines


class DocxGenerator:
    """Generate a business-readable DOCX using the standard business brief preset."""

    def generate(self, content: str, *, plain_text: bool = False) -> bytes:
        document = Document()
        section = document.sections[0]
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
        section.top_margin = Inches(1)
        section.right_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.header_distance = Inches(0.492)
        section.footer_distance = Inches(0.492)

        normal = document.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)
        normal.paragraph_format.space_after = Pt(6)
        normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
        normal.paragraph_format.line_spacing = 1.1
        heading_tokens = {
            1: (16, "2E74B5", 16, 8),
            2: (13, "2E74B5", 12, 6),
            3: (12, "1F4D78", 8, 4),
        }
        for level, (size, color, before, after) in heading_tokens.items():
            style = document.styles[f"Heading {level}"]
            style.font.name = "Calibri"
            style.font.size = Pt(size)
            style.font.color.rgb = RGBColor.from_string(color)
            style.paragraph_format.space_before = Pt(before)
            style.paragraph_format.space_after = Pt(after)

        for line in content_lines(content, plain_text=plain_text):
            if line.kind == "heading":
                document.add_heading(line.text, level=min(3, line.level))
            elif line.kind == "bullet":
                paragraph = document.add_paragraph(line.text, style="List Bullet")
                paragraph.paragraph_format.space_after = Pt(6)
            elif line.kind == "number":
                paragraph = document.add_paragraph(line.text, style="List Number")
                paragraph.paragraph_format.space_after = Pt(6)
            elif line.kind == "blank":
                document.add_paragraph()
            else:
                document.add_paragraph(line.text)

        buffer = BytesIO()
        document.save(buffer)
        return buffer.getvalue()
