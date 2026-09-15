import csv
from io import StringIO

from app.services.document.content_parser import tabular_rows
from app.services.document.extraction_models import StructuredDocumentContent


class CsvGenerator:
    def generate(self, content: str) -> bytes:
        buffer = StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerows(tabular_rows(content))
        return buffer.getvalue().encode("utf-8-sig")

    def generate_structured(self, content: StructuredDocumentContent) -> bytes:
        rows: list[list[str]] = [[content.title]]
        rows.extend([paragraph] for paragraph in content.paragraphs)
        for items in content.bullet_lists:
            rows.extend([[f"- {item}"] for item in items])
        for section in content.sections:
            rows.append([section.heading])
            rows.extend([paragraph] for paragraph in section.paragraphs)
            for items in section.bullet_lists:
                rows.extend([[f"- {item}"] for item in items])
        for table in content.all_tables():
            rows.extend(([table.title], table.headers))
            rows.extend(table.rows)
            rows.append([])
        buffer = StringIO(newline="")
        csv.writer(buffer, lineterminator="\r\n").writerows(rows)
        return buffer.getvalue().encode("utf-8-sig")
