import csv
from io import StringIO

from app.services.document.content_parser import tabular_rows


class CsvGenerator:
    def generate(self, content: str) -> bytes:
        buffer = StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerows(tabular_rows(content))
        return buffer.getvalue().encode("utf-8-sig")
