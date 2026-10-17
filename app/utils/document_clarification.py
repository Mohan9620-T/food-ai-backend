"""Concrete choices for a user's ambiguous spreadsheet layout description."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LayoutChoice:
    id: str
    label: str
    description: str


@dataclass(frozen=True)
class LayoutQuestion:
    question: str
    options: tuple[LayoutChoice, ...]


def spreadsheet_layout_question(description: str) -> LayoutQuestion | None:
    match = re.fullmatch(
        r"\s*Header\s*[-:]\s*([^\n]{1,40})\s*\n\s*Column\s*[-:]\s*([^\n]{1,40})\s*",
        description,
        re.I,
    )
    if match is None:
        return None
    header, column = (value.strip().strip('"') for value in match.groups())
    if not header or not column:
        return None
    return LayoutQuestion(
        question=f'Which Excel layout should I use for "{header}" and "{column}"?',
        options=(
            LayoutChoice(
                "title_and_column",
                f'Create a new Excel with title "{header}", column "{column}", and blank data rows.',
                "One title and one column; enter the records later.",
            ),
            LayoutChoice(
                "two_columns",
                f'Create a new Excel with two columns "{header}" and "{column}", and blank data rows.',
                "Use both values as column names.",
            ),
            LayoutChoice(
                "header_and_value",
                f'Create a new Excel with column "{header}" and first row value "{column}".',
                "One column with one supplied data value.",
            ),
        ),
    )


_FILE = re.compile(
    r"\b(pdf|excel|xlsx|csv|word|docx|pptx|spreadsheet|workbook|file|document|report|presentation|slides|markdown)\b",
    re.I,
)
_MAKE = re.compile(r"\b(make|create|build|generate|prepare|export|convert|save|write)\b", re.I)
_AS = re.compile(
    r"\b(as|in|into)\s+(an?\s+)?(pdf|excel|xlsx|csv|word|docx|pptx|spreadsheet|workbook|markdown)\b",
    re.I,
)


def file_request_gate(message: str) -> bool:
    """No model call for ordinary chat."""
    return bool((_FILE.search(message) and _MAKE.search(message)) or _AS.search(message))


def validate_file_questions(value: object) -> list[dict]:
    """Reject malformed model questions rather than inventing missing choices."""
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise ValueError("Expected one to three questions")
    result = []
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("question"), str)
            or not item["question"].strip()
        ):
            raise ValueError("Invalid question")
        options = item.get("options")
        if not isinstance(options, list) or not 2 <= len(options) <= 4:
            raise ValueError("Expected two to four options")
        if any(
            not isinstance(o, dict) or not isinstance(o.get("label"), str) or not o["label"].strip()
            for o in options
        ):
            raise ValueError("Invalid option")
        defaults = [i for i, o in enumerate(options) if o.get("recommended") is True]
        recommended = defaults[0] if len(defaults) == 1 else 0
        result.append(
            {
                "question": item["question"][:500],
                "options": [
                    {"label": o["label"][:300], "recommended": i == recommended}
                    for i, o in enumerate(options)
                ],
            }
        )
    return result
