from io import BytesIO

from pptx import Presentation
from pptx.util import Inches, Pt

from app.services.document.content_parser import document_title


class PptxGenerator:
    MAX_LINES_PER_SLIDE = 7

    def generate(self, content: str) -> bytes:
        presentation = Presentation()
        presentation.slide_width = Inches(13.333)
        presentation.slide_height = Inches(7.5)
        title = document_title(content)
        title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
        title_slide.shapes.title.text = title
        if len(title_slide.placeholders) > 1:
            title_slide.placeholders[1].text = "Generated from the conversation"

        lines = [line.strip().lstrip("#").strip() for line in content.splitlines() if line.strip()]
        if lines and lines[0] == title:
            lines = lines[1:]
        for start in range(0, len(lines), self.MAX_LINES_PER_SLIDE):
            chunk = lines[start : start + self.MAX_LINES_PER_SLIDE]
            slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            slide.shapes.title.text = "Details" if start == 0 else "Details (continued)"
            frame = slide.placeholders[1].text_frame
            frame.clear()
            for index, line in enumerate(chunk):
                paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
                paragraph.text = line.lstrip("-*+ ")
                paragraph.font.size = Pt(20)
        buffer = BytesIO()
        presentation.save(buffer)
        return buffer.getvalue()
