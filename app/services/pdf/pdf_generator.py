import re
from html import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import Flowable, Paragraph, Spacer, Table, TableStyle

from app.services.document.content_parser import content_lines, document_title
from app.services.document.extraction_models import GeneratedTableContent, StructuredDocumentContent
from app.services.pdf.pdf_theme import (
    CONTENT_WIDTH,
    NAVY,
    StatusBadge,
    build_pdf,
    document_fonts,
    ensure_supported_text,
    table_style,
)


class PdfGenerator:
    """Apply the approved theme to text exports and structured AI content."""

    def generate(self, content: str, *, plain_text: bool = False) -> bytes:
        ensure_supported_text(content)
        body, heading, white = self._styles()
        story: list[Flowable] = []
        lines = content_lines(content, plain_text=plain_text)
        index = 0
        while index < len(lines):
            line = lines[index]
            if (
                not plain_text
                and index + 1 < len(lines)
                and line.text.startswith("|")
                and re.fullmatch(r"[| :\-]+", lines[index + 1].text)
            ):
                end = index + 2
                while end < len(lines) and lines[end].text.startswith("|"):
                    end += 1

                def cells(text):
                    return [
                        value.strip().replace(r"\|", "|")
                        for value in re.split(r"(?<!\\)\|", text.strip().strip("|"))
                    ]

                headers = cells(line.text)
                rows = [cells(item.text) for item in lines[index + 2 : end]]
                # Direct text exports may exceed the structured-content contract.
                # Keep those tables as literal text rather than rejecting an export.
                if (
                    len(headers) <= 100
                    and len(rows) <= 10_000
                    and all(len(row) == len(headers) for row in rows)
                ):
                    self._append_table(
                        story,
                        GeneratedTableContent(headers=headers, rows=rows),
                        body,
                        white,
                        show_title=False,
                    )
                    index = end
                    continue
            if line.kind == "blank":
                story.append(Spacer(1, 6))
            elif line.kind == "heading":
                if line.level == 1:
                    story.append(Paragraph(escape(line.text), heading))
                else:
                    story.append(self._section_band(line.text, white))
            elif line.kind in {"bullet", "number"}:
                story.append(
                    Paragraph(
                        escape(line.text),
                        body,
                        bulletText="\u2022" if line.kind == "bullet" else "-",
                    )
                )
            else:
                story.append(Paragraph(escape(line.text), body))
            index += 1
        return build_pdf(story, title=document_title(content))

    def generate_structured(self, content: StructuredDocumentContent) -> bytes:
        ensure_supported_text(content.to_plain_text())
        body, heading, white = self._styles()
        story = [Paragraph(escape(content.title), heading)]
        self._append_blocks(
            story, content.paragraphs, content.bullet_lists, content.tables, body, white
        )
        for section in content.sections:
            story.append(self._section_band(section.heading, white))
            self._append_blocks(
                story, section.paragraphs, section.bullet_lists, section.tables, body, white
            )
        return build_pdf(story, title=content.title)

    @staticmethod
    def _styles():
        regular, bold = document_fonts()
        body = ParagraphStyle(
            "DocumentBody", fontName=regular, fontSize=10, leading=14, textColor=NAVY, spaceAfter=6
        )
        heading = ParagraphStyle(
            "DocumentTitle",
            parent=body,
            fontName=bold,
            fontSize=18,
            leading=23,
            spaceAfter=16,
            keepWithNext=True,
        )
        white = ParagraphStyle(
            "DocumentBand", parent=body, fontName=bold, textColor=colors.white, spaceAfter=0
        )
        return body, heading, white

    @staticmethod
    def _section_band(text, white):
        band = Table([[Paragraph(escape(text), white)]], colWidths=[CONTENT_WIDTH], hAlign="LEFT")
        band.setStyle(table_style())
        band.keepWithNext = True
        band.spaceBefore = 12
        band.spaceAfter = 10
        return band

    def _append_blocks(self, story, paragraphs, bullet_lists, tables, body, white):
        for text in paragraphs:
            story.append(Paragraph(escape(text), body))
        for items in bullet_lists:
            for item in items:
                story.append(Paragraph(escape(item), body, bulletText="\u2022"))
        for content_table in tables:
            self._append_table(story, content_table, body, white)

    def _append_table(self, story, content_table, body, white, *, show_title=True):
        width = len(content_table.headers) or len(content_table.rows[0])
        # Keep portrait pages and readable type; repeat the identifying first column
        # on wide-table panels instead of shrinking text or dropping columns.
        panels = (
            [list(range(width))]
            if width <= 6
            else [[0, *range(start, min(start + 5, width))] for start in range(1, width, 5)]
        )
        for panel_index, columns in enumerate(panels):
            if show_title or len(panels) > 1:
                title = content_table.title
                if len(panels) > 1:
                    title += f" (part {panel_index + 1} of {len(panels)})"
                style = ParagraphStyle(
                    "TableTitle",
                    parent=body,
                    fontName=document_fonts()[1],
                    spaceBefore=8,
                    keepWithNext=True,
                )
                story.append(Paragraph(escape(title), style))
            rows = []
            headers = content_table.headers
            if headers:
                rows.append([Paragraph(escape(headers[col]), white) for col in columns])
            for values in content_table.rows:
                row = []
                for col in columns:
                    value = values[col]
                    if (
                        headers
                        and headers[col].strip().casefold() == "status"
                        and value.strip().casefold() in {"active", "inactive"}
                    ):
                        row.append(StatusBadge(value, body.fontName))
                    else:
                        row.append(Paragraph(escape(value), body))
                rows.append(row)
            table = Table(
                rows,
                colWidths=self._column_widths(content_table, columns, body.fontName),
                repeatRows=1 if headers else 0,
                hAlign="LEFT",
                splitInRow=1,
            )
            table.setStyle(table_style(header_rows=1 if headers else 0, grid=True))
            table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.extend([table, Spacer(1, 10)])

    @staticmethod
    def _column_widths(content_table, columns, font):
        # Reserve room for a status badge in every column, then give remaining
        # space to longer content rather than wasting it on codes or short numbers.
        minimum = 75
        weights = []
        for col in columns:
            values = [row[col] for row in content_table.rows]
            if content_table.headers:
                values.append(content_table.headers[col])
            desired = max(pdfmetrics.stringWidth(value[:80], font, 10) + 24 for value in values)
            weights.append(max(0, min(desired, 240) - minimum))
        remaining = CONTENT_WIDTH - minimum * len(columns)
        total = sum(weights)
        return (
            [minimum + remaining * weight / total for weight in weights]
            if total
            else [CONTENT_WIDTH / len(columns)] * len(columns)
        )
