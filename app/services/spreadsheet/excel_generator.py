from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.services.document.content_parser import tabular_rows


class ExcelGenerator:
    def generate(self, content: str) -> bytes:
        rows = tabular_rows(content)
        width = max(len(row) for row in rows)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Document"
        for row in rows:
            sheet.append([*row, *([""] * (width - len(row)))])
        sheet.freeze_panes = "A2" if len(rows) > 1 else None
        sheet.auto_filter.ref = sheet.dimensions if len(rows) > 1 else None
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.alignment = Alignment(horizontal="left", vertical="center")
        for column in range(1, width + 1):
            values = [
                str(sheet.cell(row=row, column=column).value or "")
                for row in range(1, len(rows) + 1)
            ]
            sheet.column_dimensions[get_column_letter(column)].width = min(
                60, max(10, max(map(len, values), default=0) + 2)
            )
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        return buffer.getvalue()
