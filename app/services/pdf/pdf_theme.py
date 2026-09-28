"""Shared A4 document design, based on the approved navy-and-gold menu PDF."""

from datetime import date
from html import escape
from io import BytesIO
from pathlib import Path
from threading import RLock

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from app.services.document.exceptions import InvalidDocumentError

NAVY = colors.HexColor("#1F3A5F")
GOLD = colors.HexColor("#C9A227")
GREY = colors.HexColor("#F4F6F8")
CONTENT_WIDTH = A4[0] - 36 * mm - 12
_FONT_LOCK = RLock()


def document_fonts() -> tuple[str, str]:
    with _FONT_LOCK:
        if "DocumentSans" not in pdfmetrics.getRegisteredFontNames():
            pairs = [
                (
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                ),
                (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
            ]
            pair = next((pair for pair in pairs if all(path.is_file() for path in pair)), None)
            if pair is None:
                raise InvalidDocumentError("Install DejaVu Sans fonts to generate PDF documents.")
            for name, path in zip(("DocumentSans", "DocumentSansBold"), pair, strict=True):
                pdfmetrics.registerFont(TTFont(name, str(path)))
            pdfmetrics.registerFontFamily(
                "DocumentSans",
                normal="DocumentSans",
                bold="DocumentSansBold",
                italic="DocumentSans",
                boldItalic="DocumentSansBold",
            )
    return "DocumentSans", "DocumentSansBold"


def ensure_supported_text(content: str) -> None:
    # Preserve the existing text contract. The embedded font adds the rupee sign
    # used in the approved design, but does not add shaping for complex scripts.
    try:
        content.replace("\u20b9", "Rs").encode("cp1252")
    except UnicodeEncodeError as error:
        raise ValueError(
            "PDF cannot represent some characters; choose Word (.docx) to preserve the text."
        ) from error
    regular, _ = document_fonts()
    font = pdfmetrics.getFont(regular)
    if any(not ch.isspace() and ord(ch) not in font.face.charToGlyph for ch in content):
        raise ValueError(
            "The PDF font cannot represent some characters; choose Word (.docx) to preserve the text."
        )


def table_style(*, header_rows: int = 1, grid: bool = False) -> TableStyle:
    commands = [
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, GREY]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, header_rows), (-1, -1), 0.25, colors.HexColor("#E2E7EC")),
    ]
    if header_rows:
        commands.append(("BACKGROUND", (0, 0), (-1, header_rows - 1), NAVY))
    if grid:
        # Subtle boundaries keep generated tables extractable on re-upload.
        commands.append(("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8E0E8")))
    return TableStyle(commands)


def build_pdf(
    story: list,
    *,
    title: str,
    subtitle: str | None = None,
    detail: str | None = None,
    today: date | None = None,
) -> bytes:
    regular, bold = document_fonts()
    ensure_supported_text("\n".join(value for value in (title, subtitle, detail) if value))
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=23 * mm,
        bottomMargin=20 * mm,
        title=title,
    )

    def cover(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, *A4, stroke=0, fill=1)
        text_style = ParagraphStyle(
            "CoverTitle", fontName=bold, fontSize=34, leading=42, textColor=GOLD, alignment=1
        )
        title_block = Paragraph(escape(title), text_style)
        _, height = title_block.wrap(CONTENT_WIDTH, A4[1])
        y = min(A4[1] - 70 * mm, max(520, 370 + height)) - height
        title_block.drawOn(canvas, (A4[0] - CONTENT_WIDTH) / 2, y)
        y -= 32
        if subtitle:
            block = Paragraph(
                escape(subtitle),
                ParagraphStyle(
                    "CoverSubtitle",
                    fontName=regular,
                    fontSize=18,
                    leading=24,
                    textColor=GOLD,
                    alignment=1,
                ),
            )
            _, height = block.wrap(CONTENT_WIDTH, A4[1])
            y -= height
            block.drawOn(canvas, (A4[0] - CONTENT_WIDTH) / 2, y)
            y -= 24
        canvas.setStrokeColor(GOLD)
        canvas.line(65 * mm, y, A4[0] - 65 * mm, y)
        canvas.setFillColor(GOLD)
        if detail:
            y -= 34
            canvas.setFont(bold, 14)
            canvas.drawCentredString(A4[0] / 2, y, detail)
        y -= 30
        canvas.setFont(regular, 11)
        canvas.drawCentredString(A4[0] / 2, y, (today or date.today()).strftime("%d %B %Y"))
        canvas.restoreState()

    document.build(
        [Spacer(1, 1), PageBreak(), *story],
        onFirstPage=cover,
        canvasmaker=lambda *args, **kwargs: NumberedCanvas(
            *args, title=title, font=regular, **kwargs
        ),
    )
    return buffer.getvalue()


class StatusBadge(Flowable):
    def __init__(self, text: str, font: str):
        super().__init__()
        self.text, self.font = text, font
        self.width = pdfmetrics.stringWidth(text, font, 9) + 14
        self.height = 19

    def wrap(self, available_width, available_height):
        if self.width > available_width:
            raise InvalidDocumentError(
                "A Status value is too long for the PDF badge; use a short status label."
            )
        return self.width, self.height

    def draw(self):
        active = self.text.strip().casefold() == "active"
        self.canv.setFillColor(colors.HexColor("#E4F3E8" if active else "#E8EBEF"))
        self.canv.roundRect(0, 0, self.width, self.height, 7, stroke=0, fill=1)
        self.canv.setFillColor(colors.HexColor("#22633C" if active else "#505966"))
        self.canv.setFont(self.font, 9)
        self.canv.drawString(7, 6, self.text)


class NumberedCanvas(Canvas):
    def __init__(self, *args, title: str, font: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages: list[dict] = []
        self._header_title, self._header_font = title, font

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        count = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            if self._pageNumber > 1:
                self.saveState()
                self.setFillColor(NAVY)
                self.setFont(self._header_font, 10)
                header = self._header_title
                while pdfmetrics.stringWidth(header, self._header_font, 10) > CONTENT_WIDTH:
                    header = header[:-4].rstrip() + "..."
                self.drawString(18 * mm, A4[1] - 14 * mm, header)
                self.setStrokeColor(colors.HexColor("#D8E0E8"))
                self.line(18 * mm, A4[1] - 17 * mm, A4[0] - 18 * mm, A4[1] - 17 * mm)
                self.setFont(self._header_font, 9)
                self.drawRightString(
                    A4[0] - 18 * mm, 11 * mm, f"Page {self._pageNumber} of {count}"
                )
                self.restoreState()
            super().showPage()
        super().save()
