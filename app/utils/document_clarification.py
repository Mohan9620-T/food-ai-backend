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
