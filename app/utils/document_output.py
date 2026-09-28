"""Output intent shared by legacy spreadsheet routes and document automation."""

import re


def requests_word_output(instruction: str | None) -> bool:
    """Recognize Word delivery, including common conversational phrasing."""
    text = re.sub(r'"[^"]*"|“[^”]*”|`[^`]*`', " ", positive_instruction(instruction or ""))
    target = r"(?:word(?:\s+documents?)?|docx)"
    return bool(
        re.search(
            r"\b(?:create|crate|creat|generate|make|build|prepare|produce|draft|write|"
            r"give|send|deliver|download)\s+"
            r"(?:(?:me|us|one|a|an|the|new|only|downloadable|microsoft|ms)\s+)*"
            + target
            + r"\b|\b(?:convert|export|save|give|send|make|turn)\b[^.\n]{0,140}?"
            r"\b(?:to|as|into)\s+(?:(?:a|an|the|microsoft|ms)\s+)*"
            + target
            + r"\b|\b"
            + target
            + r"\s+(?:file\s+)?(?:(?:ah|la|aa)\s+)?(?:kudu|venum|pannu|create|crate|creat|generate)\b",
            text,
            re.I,
        )
    )


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
