"""A review can remove or reorder rows, never change a cell."""

import json
import logging
from copy import deepcopy

from app.config import settings
from app.services.document.extraction_models import StructuredDocumentContent

logger = logging.getLogger(__name__)


def apply_review(headers: list[str], rows: list[list[str]], verdict: dict) -> list[list[str]]:
    drop, order = verdict.get("drop"), verdict.get("sort")
    if not isinstance(drop, list) or not isinstance(order, list):
        raise ValueError("Invalid review")
    if any(type(n) is not int or not 1 <= n <= len(rows) for n in drop):
        raise ValueError("Invalid row number")
    for key in order:
        if (
            not isinstance(key, dict)
            or key.get("column") not in headers
            or type(key.get("descending")) is not bool
        ):
            raise ValueError("Invalid sort column or direction")
    kept = [row for i, row in enumerate(rows, 1) if i not in drop]
    for key in reversed(order):
        index = headers.index(key["column"])
        present = [row for row in kept if row[index] != ""]
        missing = [row for row in kept if row[index] == ""]
        try:
            present.sort(
                key=lambda row: float(row[index].replace(",", "")), reverse=key["descending"]
            )
        except ValueError:
            present.sort(key=lambda row: row[index].casefold(), reverse=key["descending"])
        kept = present + missing
    return kept


async def review_content(
    content: StructuredDocumentContent, instruction: str, complete
) -> StructuredDocumentContent:
    if not settings.ENABLE_TABLE_REVIEW:
        return content
    reviewed = deepcopy(content)
    tables = reviewed.all_tables()
    if not tables:
        return content
    try:
        numbered = []
        total = 0
        for table in tables:
            numbered.append(
                {"headers": table.headers, "rows": list(enumerate(table.rows, total + 1))}
            )
            total += len(table.rows)
        prompt = (
            "Review this table against the request. Return only JSON: "
            '{"drop":[1],"sort":[{"column":"exact header","descending":false}]}. '
            "Row numbers are global across tables. Only remove rows violating explicit criteria or reorder; never rewrite. "
            + json.dumps({"request": instruction, "tables": numbered})
        )
        verdict = json.loads(await complete(prompt, []))
        if (
            not isinstance(verdict, dict)
            or not isinstance(verdict.get("drop"), list)
            or not isinstance(verdict.get("sort"), list)
        ):
            raise ValueError("Invalid verdict")
        if any(type(n) is not int or not 1 <= n <= total for n in verdict["drop"]):
            raise ValueError("Invalid row number")
        columns = {column for table in tables for column in table.headers}
        for key in verdict["sort"]:
            if (
                not isinstance(key, dict)
                or key.get("column") not in columns
                or type(key.get("descending")) is not bool
            ):
                raise ValueError("Invalid sort")
        offset = 0
        for table in tables:
            length = len(table.rows)
            local = {
                "drop": [n - offset for n in verdict["drop"] if offset < n <= offset + length],
                "sort": [key for key in verdict["sort"] if key["column"] in table.headers],
            }
            table.rows = apply_review(table.headers, table.rows, local)
            offset += length
        return reviewed
    except Exception:
        logger.warning("document.table_review_invalid_keeping_original")
        return content
