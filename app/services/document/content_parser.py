import csv
import re
from dataclasses import dataclass
from io import StringIO


@dataclass(frozen=True)
class ContentLine:
    kind: str
    text: str = ""
    level: int = 0


def document_title(content: str) -> str:
    for line in content.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:120]
    return "Generated Document"


def tabular_rows(content: str) -> list[list[str]]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    markdown_rows = [line for line in lines if line.startswith("|") and line.endswith("|")]
    if len(markdown_rows) >= 2:
        rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in markdown_rows]
        return [row for row in rows if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in row)]

    for delimiter in (",", "\t", ";"):
        rows = list(csv.reader(StringIO(content), delimiter=delimiter))
        if rows and max((len(row) for row in rows), default=0) > 1:
            return [
                [cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)
            ]
    return [[line] for line in lines] or [[content]]


def content_lines(content: str, *, plain_text: bool = False) -> list[ContentLine]:
    """Return simple Markdown-aware blocks for deterministic document rendering."""
    parsed: list[ContentLine] = []
    for line in content.splitlines():
        stripped = line.strip()
        if plain_text:
            parsed.append(ContentLine("paragraph", line))
        elif not stripped:
            parsed.append(ContentLine("blank"))
        elif heading := re.match(r"^(#{1,6})\s+(.+)$", stripped):
            parsed.append(ContentLine("heading", heading.group(2), len(heading.group(1))))
        elif bullet := re.match(r"^[-*+]\s+(.+)$", stripped):
            parsed.append(ContentLine("bullet", bullet.group(1)))
        elif numbered := re.match(r"^\d+[.)]\s+(.+)$", stripped):
            parsed.append(ContentLine("number", numbered.group(1)))
        else:
            parsed.append(ContentLine("paragraph", stripped))
    return parsed
