"""Check the generated PDF itself, including layout and recoverable table data."""

from io import BytesIO

import pdfplumber
import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4

from app.services.document.extraction_models import (
    GeneratedSectionContent,
    GeneratedTableContent,
    StructuredDocumentContent,
)
from app.services.pdf.pdf_generator import PdfGenerator


def _text(data):
    return [page.extract_text() for page in PdfReader(BytesIO(data)).pages]


def _has_color(objects, rgb):
    return any(obj.get("non_stroking_color") == pytest.approx(rgb, abs=0.00001) for obj in objects)


def test_new_pdf_uses_reference_design_with_its_own_content():
    data = PdfGenerator().generate_structured(
        StructuredDocumentContent(
            title="Project Progress",
            paragraphs=["All milestones are listed below."],
            sections=[
                GeneratedSectionContent(
                    heading="Delivery status",
                    tables=[
                        GeneratedTableContent(
                            title="Milestones",
                            headers=["Task", "Cost", "Status"],
                            rows=[
                                ["Design", "₹ 1,250.00", "Active"],
                                ["Launch", "₹ 2,000.00", "Inactive"],
                            ],
                        )
                    ],
                )
            ],
        )
    )
    text = _text(data)
    assert len(text) == 2
    assert "Project Progress" in text[0]
    assert "Catering Menu" not in "\n".join(text)
    assert "Page 1" not in text[0]
    assert "Page 2 of 2" in text[1]
    assert "₹ 1,250.00" in text[1]
    with pdfplumber.open(BytesIO(data)) as document:
        cover, body = document.pages
        assert _has_color(cover.rects, (31 / 255, 58 / 255, 95 / 255))
        assert _has_color(cover.chars, (201 / 255, 162 / 255, 39 / 255))
        assert _has_color(body.rects, (244 / 255, 246 / 255, 248 / 255))
        assert _has_color(body.curves, (228 / 255, 243 / 255, 232 / 255))
        assert _has_color(body.curves, (232 / 255, 235 / 255, 239 / 255))
        assert body.extract_tables()[0] == [
            ["Task", "Cost", "Status"],
            ["Design", "₹ 1,250.00", "Active"],
            ["Launch", "₹ 2,000.00", "Inactive"],
        ]


def test_long_tables_repeat_headers_and_preserve_every_record():
    rows = [[f"ID-{index:03}", f"Complete record {index}", "Active"] for index in range(100)]
    data = PdfGenerator().generate_structured(
        StructuredDocumentContent(
            title="Inventory Report",
            tables=[
                GeneratedTableContent(
                    title="Inventory", headers=["Code", "Description", "Status"], rows=rows
                )
            ],
        )
    )
    pages = _text(data)
    assert len(pages) > 3
    for index, text in enumerate(pages[1:], 2):
        assert "Inventory Report" in text
        assert f"Page {index} of {len(pages)}" in text
        assert "Code" in text and "Description" in text and "Status" in text
        assert "ID-" in text
    content = "\n".join(pages[1:])
    for row in rows:
        assert content.count(row[0]) == 1


def test_markdown_tables_are_formatted_and_plain_text_is_preserved():
    source = "# Monthly Costs\n\n## Breakdown\n| Item | Cost |\n| --- | ---: |\n| Meal | ₹ 3.00 |\n| A \\| B | ₹ 4.00 |"
    formatted = PdfGenerator().generate(source)
    with pdfplumber.open(BytesIO(formatted)) as document:
        assert document.pages[1].extract_tables()[0] == [
            ["Item", "Cost"],
            ["Meal", "₹ 3.00"],
            ["A | B", "₹ 4.00"],
        ]
    literal = "\n".join(_text(PdfGenerator().generate(source, plain_text=True))[1:])
    for line in source.splitlines():
        assert line in literal


def test_wide_table_uses_readable_portrait_panels_without_losing_columns():
    headers = [f"Field {index}" for index in range(12)]
    rows = [[f"R{r}C{c}" for c in range(12)] for r in range(3)]
    data = PdfGenerator().generate_structured(
        StructuredDocumentContent(
            title="Wide Report",
            tables=[GeneratedTableContent(headers=headers, rows=rows)],
        )
    )
    text = "\n".join(_text(data)[1:])
    assert "part 1 of 3" in text and "part 3 of 3" in text
    for row in rows:
        for value in row:
            assert value in text
    with pdfplumber.open(BytesIO(data)) as document:
        for page in document.pages:
            assert page.width == pytest.approx(A4[0], abs=0.01)
            assert page.height == pytest.approx(A4[1], abs=0.01)
            assert all(40 <= char["x0"] <= char["x1"] <= page.width - 40 for char in page.chars)


def test_long_title_and_long_table_cell_fit_pages():
    title = "Detailed operations and planning report with monthly delivery milestones " * 2
    cell = "Original description with all details retained. " * 160 + "FINAL VALUE"
    data = PdfGenerator().generate_structured(
        StructuredDocumentContent(
            title=title,
            sections=[
                GeneratedSectionContent(
                    heading="Long descriptions",
                    tables=[
                        GeneratedTableContent(
                            headers=["Item", "Description"], rows=[["Record-1", cell]]
                        )
                    ],
                )
            ],
        )
    )
    pages = _text(data)
    text = " ".join(" ".join(pages).split())
    assert "FINAL VALUE" in text
    assert text.count("Original description") == 160
    for page_text in pages[1:]:
        assert "Original description" in page_text or "FINAL" in page_text
    with pdfplumber.open(BytesIO(data)) as document:
        for page in document.pages:
            assert all(0 <= char["top"] < char["bottom"] < page.height for char in page.chars)
            assert all(40 <= char["x0"] <= char["x1"] <= page.width - 40 for char in page.chars)


def test_section_band_stays_with_first_content_line():
    data = PdfGenerator().generate_structured(
        StructuredDocumentContent(
            title="Multi-section report",
            sections=[
                GeneratedSectionContent(
                    heading=f"Section {index:02}",
                    paragraphs=[f"START-{index:02} " + "This is the complete section text. " * 35],
                )
                for index in range(12)
            ],
        )
    )
    for text in _text(data)[1:]:
        for index in range(12):
            if f"Section {index:02}" in text:
                assert f"START-{index:02}" in text


def test_malformed_markdown_table_is_preserved_as_text():
    source = "# Raw data\n| Key | Value |\n| --- | --- |\n| all | three | values |"
    text = "\n".join(_text(PdfGenerator().generate(source))[1:])
    for line in source.splitlines()[1:]:
        assert line in text


def test_headerless_table_keeps_first_row_as_data():
    data = PdfGenerator().generate_structured(
        StructuredDocumentContent(
            title="Numbers",
            tables=[GeneratedTableContent(rows=[["One", "1"], ["Two", "2"]])],
        )
    )
    with pdfplumber.open(BytesIO(data)) as document:
        assert document.pages[1].extract_tables()[0] == [["One", "1"], ["Two", "2"]]


def test_text_export_beyond_structured_table_limits_preserves_all_cells():
    headers = "| " + " | ".join(f"H{index:03}" for index in range(101)) + " |"
    separator = "| " + " | ".join(["---"] * 101) + " |"
    values = [f"VALUE-{index:03}" for index in range(101)]
    source = "# Wide source\n" + headers + "\n" + separator + "\n| " + " | ".join(values) + " |"
    text = "\n".join(_text(PdfGenerator().generate(source))[1:])
    for value in values:
        assert value in text
