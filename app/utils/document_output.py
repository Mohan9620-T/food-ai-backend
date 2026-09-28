"""Output intent shared by legacy spreadsheet routes and document automation."""

import re


def positive_instruction(instruction: str) -> str:
    return re.sub(r"\b(?:do not|don't|never)\b[^.\n]*", " ", instruction, flags=re.I)


def requests_pdf_output(instruction: str | None) -> bool:
    text = positive_instruction(instruction or "")
    return bool(
        re.search(
            r"\b(?:create|generate|make|build|prepare|produce|give|deliver)\s+"
            r"(?:(?:me|one|a|an|the|downloadable|new|only)\s+)*\.?pdf\b"
            r"|\b(?:convert|covert|export|save)\b[^.\n]{0,120}?\b(?:to|as|into)\s+(?:a\s+)?\.?pdf\b"
            r"|\boutput\s+(?:(?:must|should)\s+)?(?:be|as|is)\s+(?:a\s+)?\.?pdf\b"
            r"|\bpdf\s+(?:file\s+)?(?:ah\s+)?(?:kudu|venum|create|generate)\b",
            text,
            re.I,
        )
    )
