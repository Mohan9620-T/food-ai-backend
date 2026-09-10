import asyncio
import csv
import logging
import re
from contextlib import aclosing, closing
from copy import copy, deepcopy
from enum import Enum
from io import BytesIO, StringIO
from pathlib import Path

import pytesseract
from docx import Document
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries
from openpyxl.worksheet.worksheet import Worksheet
from pypdf import PdfReader
from pypdfium2 import PdfDocument

from app.config import settings
from app.schemas.chat import ChatHistoryMessage
from app.services.chat_service import ChatModelUnavailableError, ChatService
from app.services.document.document_generation_service import DocumentGenerationService
from app.services.document.document_reading_service import DocumentReadingService
from app.services.document.exceptions import (
    DocumentProcessingUnavailableError as DocumentProcessingUnavailableError,
)
from app.services.document.exceptions import InvalidDocumentError
from app.services.document.extraction_models import ExtractedDocument
from app.services.pdf.pdf_reader import PdfDocumentReader
from app.services.spreadsheet.excel_reader import ExcelReader
from app.services.word.docx_reader import DocxReader

logger = logging.getLogger(__name__)


class SpreadsheetOperation(str, Enum):
    SPLIT_BY_CATEGORY = "split_by_category"
    FILTER_COLUMN = "filter_column"
    EXPAND_DISH_BY_DIETARY_CATEGORY = "expand_dish_by_dietary_category"
    FORMAT_WORKBOOK = "format_workbook"


class ChatDocumentService:
    OCR_PAGE_TIMEOUT_SECONDS = 30
    MAX_DOCUMENT_CONTEXT_CHARS = 30_000
    DISH_CATEGORY_DEFINITIONS = (
        ("regular", "Regular", "7R", "7R - Regular"),
        ("easy_to_chew", "Easy to Chew", "7EC", "7EC - Easy"),
        ("soft_and_bite_sized", "Soft & Bite", "6SB", "6SB - Chopped"),
        ("minced_and_moist", "Minced & Moist", "5MM", "5MM - Minced"),
        ("pureed", "Pureed", "4Pu", "4Pu - Pureed"),
    )

    def __init__(self, chat_service: ChatService | None = None):
        self.chat_service = chat_service or ChatService()
        self.document_generator = DocumentGenerationService()
        # Pass the module-level factories through so existing dependency-injection
        # and regression tests continue to exercise the extracted readers.
        self.document_reader = DocumentReadingService(
            pdf_reader=PdfDocumentReader(
                pdf_reader_factory=PdfReader,
                pdf_document_factory=PdfDocument,
                ocr_engine=pytesseract,
            ),
            docx_reader=DocxReader(document_factory=Document),
            excel_reader=ExcelReader(workbook_loader=load_workbook),
        )

    def extract(self, file_data: bytes, filename: str) -> str:
        return self.extract_document(file_data, filename).text

    def extract_document(self, file_data: bytes, filename: str) -> ExtractedDocument:
        return self.document_reader.read(file_data, filename)

    @staticmethod
    def is_spreadsheet_update_request(instruction: str | None) -> bool:
        """Return true only for an explicit, supported workbook update request."""
        return ChatDocumentService.spreadsheet_operation(instruction) is not None

    @staticmethod
    def spreadsheet_operation(instruction: str | None) -> SpreadsheetOperation | None:
        if not instruction or not instruction.strip():
            return None
        if ChatDocumentService.is_dish_category_expansion_request(instruction):
            return SpreadsheetOperation.EXPAND_DISH_BY_DIETARY_CATEGORY
        if ChatDocumentService.is_category_split_request(instruction):
            return SpreadsheetOperation.SPLIT_BY_CATEGORY
        if ChatDocumentService.is_filter_column_request(instruction):
            return SpreadsheetOperation.FILTER_COLUMN
        normalized = instruction.casefold()
        formatting_terms = (
            "align",
            "alignment",
            "center",
            "centre",
            "left align",
            "right align",
            "format",
            "style",
            "design",
            "professional",
            "auto fit",
            "autofit",
            "column width",
            "wrap text",
            "header",
            "bold",
            "border",
            "freeze",
            "arrange",
            "organize",
            "organise",
            "clean up",
        )
        if any(term in normalized for term in formatting_terms):
            return SpreadsheetOperation.FORMAT_WORKBOOK
        return None

    @staticmethod
    def is_filter_column_request(instruction: str | None) -> bool:
        if not instruction:
            return False
        normalized = instruction.casefold()
        asks_for_filter = bool(re.search(r"\bfilter(?:s|ed|ing)?\b", normalized))
        names_a_column = bool(
            re.search(r"\bcolumn\b", normalized)
            or re.search(r"\bcategory\s*\*?\b", normalized)
            or re.search(r"[`\"'][^`\"']+[`\"']", instruction)
        )
        return asks_for_filter and names_a_column

    @staticmethod
    def is_dish_category_expansion_request(instruction: str | None) -> bool:
        if not instruction or not instruction.strip():
            return False
        normalized = ChatDocumentService._normalize_words(instruction)
        compact = normalized.replace(" and ", " ").replace(" ", "")
        explicitly_transforms_excel = bool(
            re.search(r"\btransform\s+(?:the|this|my)?\s*excel\b", normalized)
        )
        requests_category_rows = any(
            phrase in normalized
            for phrase in (
                "expand this excel",
                "expand the excel",
                "expand my excel",
                "separate rows based on",
                "one row for every category",
                "one row per category",
                "split each dish into rows",
                "split every dish into rows",
                "create a row for every category",
                "create rows for each category",
                "create category rows",
                "generate rows for every x",
                "expand each dish row",
                "expand every dish row",
                "expand dish rows",
            )
        )
        names_texture_categories = (
            "five texture categories" in normalized
            or "five category columns" in normalized
            or all(
                marker in compact
                for marker in (
                    "regular",
                    "easytochew",
                    "softbite",
                    "mincedmoist",
                    "pureed",
                )
            )
        )
        marked_category_rows = (
            "category marked x" in normalized or "categories marked x" in normalized
        )
        short_expansion_alias = any(
            phrase in normalized
            for phrase in (
                "expand this excel",
                "expand the excel",
                "expand my excel",
                "create category rows",
                "generate rows for every x",
            )
        )
        return (
            explicitly_transforms_excel
            or short_expansion_alias
            or (requests_category_rows and (names_texture_categories or marked_category_rows))
        )

    def format_spreadsheet(
        self, file_data: bytes, filename: str, instruction: str
    ) -> tuple[bytes, str, list[str]]:
        """Apply safe presentation changes while preserving every workbook cell."""
        normalized = instruction.casefold()
        operation = self.spreadsheet_operation(instruction)
        split_by_category = operation == SpreadsheetOperation.SPLIT_BY_CATEGORY
        filter_column = operation == SpreadsheetOperation.FILTER_COLUMN
        expand_dish_categories = operation == SpreadsheetOperation.EXPAND_DISH_BY_DIETARY_CATEGORY
        if operation is None:
            raise InvalidDocumentError("Describe the spreadsheet update you want me to apply.")
        professional = split_by_category or any(
            term in normalized
            for term in (
                "professional",
                "format",
                "style",
                "design",
                "arrange",
                "organize",
                "organise",
            )
        )
        horizontal = None
        if "center" in normalized or "centre" in normalized:
            horizontal = "center"
        elif "right align" in normalized or "align right" in normalized:
            horizontal = "right"
        elif "left align" in normalized or "align left" in normalized:
            horizontal = "left"

        requested_columns = {
            column_index_from_string(match)
            for match in re.findall(r"\b(?:column|col)\s+([a-z]{1,3})\b", normalized)
        }
        style_headers = professional or "header" in normalized or "bold" in normalized
        auto_fit = professional or any(
            term in normalized for term in ("auto fit", "autofit", "column width")
        )
        wrap_text = professional or "wrap text" in normalized
        add_borders = professional or "border" in normalized
        freeze_header = professional or "freeze" in normalized
        smart_alignment = horizontal is None and (
            professional or "align" in normalized or "alignment" in normalized
        )

        category_sheet_count = 0
        source_sheet_title = ""
        expanded_header_row = 0
        expanded_source_rows = 0
        expanded_generated_rows = 0
        source_workbook = None
        output_workbook = None
        source_extension = Path(filename).suffix.casefold()
        try:
            if source_extension == ".csv" and filter_column:
                source_workbook = Workbook()
                source_sheet = source_workbook.active
                source_sheet.title = "Sheet1"
                for row in csv.reader(StringIO(self._decode_text(file_data))):
                    source_sheet.append(row)
            else:
                source_workbook = load_workbook(BytesIO(file_data), data_only=False)
            try:
                if split_by_category:
                    output_workbook, category_sheet_count, source_sheet_title = (
                        self._split_sheets_by_category(source_workbook, normalized)
                    )
                else:
                    output_workbook = source_workbook
                if expand_dish_categories:
                    (
                        expanded_source_rows,
                        expanded_generated_rows,
                        source_sheet_title,
                        expanded_header_row,
                    ) = self._expand_dish_category_rows(output_workbook, normalized)
                filtered_ranges: dict[str, str] = {}
                filtered_header = ""
                if filter_column:
                    filtered_ranges, filtered_header = self._apply_column_filter(
                        output_workbook, instruction
                    )
                elif not expand_dish_categories:
                    self._format_workbook(
                        output_workbook,
                        horizontal=horizontal,
                        requested_columns=requested_columns,
                        smart_alignment=smart_alignment,
                        style_headers=style_headers,
                        auto_fit=auto_fit,
                        wrap_text=wrap_text,
                        add_borders=add_borders,
                        freeze_header=freeze_header,
                    )
                output = BytesIO()
                output_workbook.save(output)
            finally:
                if output_workbook is not None and output_workbook is not source_workbook:
                    output_workbook.close()
                if source_workbook is not None:
                    source_workbook.close()

            generated_data = output.getvalue()
            with closing(
                load_workbook(
                    BytesIO(generated_data),
                    read_only=expand_dish_categories,
                    data_only=False,
                )
            ) as validated:
                if split_by_category and len(validated.sheetnames) != category_sheet_count:
                    raise InvalidDocumentError(
                        "The generated category workbook failed validation. Please try again."
                    )
                if filter_column and any(
                    validated[sheet_name].auto_filter.ref != expected_range
                    for sheet_name, expected_range in filtered_ranges.items()
                ):
                    raise InvalidDocumentError(
                        "The generated Excel filter failed validation. Please try again."
                    )
                if expand_dish_categories:
                    validated_rows = sum(
                        1
                        for row in validated[source_sheet_title].iter_rows(
                            min_row=expanded_header_row + 1
                        )
                        if any(cell.value is not None for cell in row)
                    )
                    if validated_rows != expanded_generated_rows:
                        raise InvalidDocumentError(
                            "The generated dish category workbook failed validation. Please try again."
                        )
        except InvalidDocumentError:
            raise
        except Exception as error:
            source_label = "CSV" if source_extension == ".csv" else "XLSX"
            raise InvalidDocumentError(
                f"The spreadsheet could not be updated. Check that it is a valid {source_label} file."
            ) from error

        actions = []
        if split_by_category:
            actions.append(
                f"split {source_sheet_title} into {category_sheet_count} category worksheets"
            )
        if filter_column:
            worksheet_label = "worksheet" if len(filtered_ranges) == 1 else "worksheets"
            actions.append(
                f"added a filter only to the {filtered_header} column on "
                f"{len(filtered_ranges)} {worksheet_label}"
            )
        if expand_dish_categories:
            actions.append(
                f"processed {expanded_source_rows:,} original dish rows and generated "
                f"{expanded_generated_rows:,} category rows on {source_sheet_title}"
            )
        if horizontal:
            scope = (
                "columns "
                + ", ".join(get_column_letter(column) for column in sorted(requested_columns))
                if requested_columns
                else "all populated cells"
            )
            actions.append(f"{horizontal} alignment for {scope}")
        elif smart_alignment:
            actions.append("smart text and number alignment")
        if style_headers:
            actions.append("formatted header rows")
        if auto_fit:
            actions.append("auto-fitted column widths")
        if wrap_text:
            actions.append("wrapped long text")
        if add_borders:
            actions.append("added table borders")
        if freeze_header:
            actions.append("froze header rows")
        if not actions:
            actions.append("preserved the workbook without dropping any populated cells")

        safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(filename).stem).strip("_")
        if split_by_category:
            suffix = "category_wise"
        elif filter_column:
            suffix = "filter_updated"
        elif expand_dish_categories:
            suffix = "category_expanded"
        else:
            suffix = "updated"
        output_stem = safe_stem or "spreadsheet"
        if not output_stem.casefold().endswith(f"_{suffix}"):
            output_stem = f"{output_stem}_{suffix}"
        return generated_data, f"{output_stem}.xlsx", actions

    @classmethod
    def _expand_dish_category_rows(
        cls, workbook: Workbook, normalized_instruction: str
    ) -> tuple[int, int, str, int]:
        candidates: list[tuple[Worksheet, int, Cell, Cell, dict[str, Cell]]] = []
        workbook_has_values = False
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 25)):
                if not any(cell.value is not None for cell in row):
                    continue
                workbook_has_values = True
                category_cells: dict[str, Cell] = {}
                code_cell = None
                label_cell = None
                for cell in row:
                    if cell.value is None:
                        continue
                    header_key = cls._dish_category_header_key(str(cell.value))
                    if header_key is not None:
                        category_cells[header_key] = cell
                    normalized_header = cls._normalize_words(str(cell.value))
                    if normalized_header == "code":
                        code_cell = cell
                    elif normalized_header == "label":
                        label_cell = cell
                if (
                    len(category_cells) == len(cls.DISH_CATEGORY_DEFINITIONS)
                    and code_cell is not None
                    and label_cell is not None
                ):
                    candidates.append((sheet, row[0].row, code_cell, label_cell, category_cells))
                    break

        if not workbook_has_values:
            raise InvalidDocumentError("The spreadsheet is empty and has no dish rows to expand.")
        if not candidates:
            raise InvalidDocumentError(
                "I couldn't find all five required dish category columns in the uploaded Excel "
                "file: Regular, Easy to chew, Soft and bite-sized, Minced and Moist, and Pureed."
            )
        explicitly_named = [
            candidate
            for candidate in candidates
            if cls._sheet_is_requested(candidate[0].title, normalized_instruction)
        ]
        if len(explicitly_named) == 1:
            selected = explicitly_named[0]
        elif len(candidates) == 1:
            selected = candidates[0]
        else:
            names = ", ".join(f"'{candidate[0].title}'" for candidate in candidates[:5])
            raise InvalidDocumentError(
                f"More than one worksheet contains the five dish category columns ({names}). "
                "Mention the worksheet name in your instruction."
            )

        sheet, header_row, code_header, label_header, category_headers = selected
        source_rows = [
            row_number
            for row_number in range(header_row + 1, sheet.max_row + 1)
            if any(
                sheet.cell(row=row_number, column=column).value is not None
                for column in range(1, sheet.max_column + 1)
            )
        ]
        if not source_rows:
            raise InvalidDocumentError(
                f"Worksheet '{sheet.title}' has a header row but no dish rows to expand."
            )

        source_title = sheet.title
        source_index = workbook.index(sheet)
        original_max_row = sheet.max_row
        target = workbook.create_sheet(
            cls._unique_category_sheet_title(workbook, "Expanded Dish Rows")
        )
        header_merges = [
            str(merged_range)
            for merged_range in sheet.merged_cells.ranges
            if merged_range.max_row <= header_row
        ]
        source_tables = [deepcopy(table) for table in sheet.tables.values()]
        cls._copy_worksheet_layout(sheet, target)

        generated_row_count = 0
        definitions = {definition[0]: definition for definition in cls.DISH_CATEGORY_DEFINITIONS}
        target_row = 0
        for row_number in range(1, header_row + 1):
            target_row += 1
            cls._copy_spreadsheet_row(tuple(sheet[row_number]), target, target_row)
        for row_number in source_rows:
            active_categories = [
                key
                for key, _, _, _ in cls.DISH_CATEGORY_DEFINITIONS
                if cls._is_active_dish_category(
                    sheet.cell(row=row_number, column=category_headers[key].column).value
                )
            ]
            if not active_categories:
                target_row += 1
                cls._copy_spreadsheet_row(tuple(sheet[row_number]), target, target_row)
                generated_row_count += 1
                continue

            source_row = tuple(sheet[row_number])
            original_code = sheet.cell(row=row_number, column=code_header.column).value
            original_label = sheet.cell(row=row_number, column=label_header.column).value
            base_code = cls._dish_code_base(original_code)
            base_label = cls._dish_label_base(original_label)
            for category_key in active_categories:
                target_row += 1
                cls._copy_spreadsheet_row(source_row, target, target_row)
                for other_key, _, _, _ in cls.DISH_CATEGORY_DEFINITIONS:
                    target.cell(
                        row=target_row,
                        column=category_headers[other_key].column,
                    ).value = None
                target.cell(
                    row=target_row,
                    column=category_headers[category_key].column,
                ).value = "x"
                _, _, code_suffix, label_suffix = definitions[category_key]
                if base_code is not None:
                    target.cell(
                        row=target_row, column=code_header.column
                    ).value = f"{base_code}-{code_suffix}"
                if base_label is not None:
                    target.cell(
                        row=target_row, column=label_header.column
                    ).value = f"{base_label} ({label_suffix})"
            generated_row_count += len(active_categories)

        for merged_range in header_merges:
            target.merge_cells(merged_range)
        cls._resize_sheet_ranges(target, original_max_row, target.max_row)
        workbook.remove(sheet)
        for table in source_tables:
            min_col, min_row, max_col, max_row = range_boundaries(table.ref)
            if max_row == original_max_row:
                table.ref = (
                    f"{get_column_letter(min_col)}{min_row}:"
                    f"{get_column_letter(max_col)}{target.max_row}"
                )
            target.add_table(table)
        target.title = source_title
        workbook.move_sheet(target, offset=source_index - workbook.index(target))
        return len(source_rows), generated_row_count, source_title, header_row

    @staticmethod
    def _copy_worksheet_layout(source: Worksheet, target: Worksheet) -> None:
        target.sheet_format = copy(source.sheet_format)
        target.sheet_properties = copy(source.sheet_properties)
        for attribute in (
            "showGridLines",
            "showRowColHeaders",
            "zoomScale",
            "zoomScaleNormal",
            "rightToLeft",
        ):
            setattr(target.sheet_view, attribute, getattr(source.sheet_view, attribute))
        target.freeze_panes = source.freeze_panes
        target.sheet_state = source.sheet_state
        target.auto_filter = copy(source.auto_filter)
        target.page_margins = copy(source.page_margins)
        target.page_setup = copy(source.page_setup)
        target.print_options = copy(source.print_options)
        target.protection = copy(source.protection)
        target.data_validations = copy(source.data_validations)
        target.conditional_formatting = copy(source.conditional_formatting)
        for key, source_dimension in source.column_dimensions.items():
            target_dimension = target.column_dimensions[key]
            for attribute in (
                "width",
                "hidden",
                "bestFit",
                "outlineLevel",
                "collapsed",
                "min",
                "max",
            ):
                setattr(target_dimension, attribute, getattr(source_dimension, attribute))

    @classmethod
    def _dish_category_header_key(cls, header: str) -> str | None:
        compact = cls._normalize_words(header).replace(" and ", " ").replace(" ", "")
        aliases = {
            "regular": "regular",
            "easytochew": "easy_to_chew",
            "softbite": "soft_and_bite_sized",
            "softbitesized": "soft_and_bite_sized",
            "mincedmoist": "minced_and_moist",
            "pureed": "pureed",
        }
        return aliases.get(compact)

    @staticmethod
    def _is_active_dish_category(value: object) -> bool:
        return isinstance(value, str) and value.strip().casefold() == "x"

    @staticmethod
    def _dish_code_base(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return re.sub(
            r"(?:\s*-\s*(?:7R|7EC|6SB|5MM|4Pu))+$",
            "",
            text,
            flags=re.IGNORECASE,
        ).rstrip()

    @staticmethod
    def _dish_label_base(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        suffixes = (
            r"7R\s*-\s*Regular|7EC\s*-\s*Easy|6SB\s*-\s*Chopped|"
            r"5MM\s*-\s*Minced|4Pu\s*-\s*Pureed"
        )
        text = re.sub(rf"\s*\((?:{suffixes})\)$", "", text, flags=re.IGNORECASE)
        old_suffixes = (
            r"Regular\s*-\s*7R|Easy\s*-\s*7EC|Chopped\s*-\s*6SB|"
            r"Minced\s*-\s*5MM|Pureed\s*-\s*4Pu"
        )
        return re.sub(rf"\s*-\s*(?:{old_suffixes})$", "", text, flags=re.IGNORECASE).rstrip()

    @staticmethod
    def _resize_sheet_ranges(sheet: Worksheet, original_max_row: int, new_max_row: int) -> None:
        if sheet.auto_filter.ref:
            min_col, min_row, max_col, max_row = range_boundaries(sheet.auto_filter.ref)
            if max_row == original_max_row:
                sheet.auto_filter.ref = (
                    f"{get_column_letter(min_col)}{min_row}:"
                    f"{get_column_letter(max_col)}{new_max_row}"
                )
        for table in sheet.tables.values():
            min_col, min_row, max_col, max_row = range_boundaries(table.ref)
            if max_row == original_max_row:
                table.ref = (
                    f"{get_column_letter(min_col)}{min_row}:"
                    f"{get_column_letter(max_col)}{new_max_row}"
                )

    @classmethod
    def filter_column_reference(cls, instruction: str) -> str | None:
        """Extract an explicitly named filter column without using an AI provider."""
        patterns = (
            r"\bfilter(?:s|ed|ing)?\b[^`\"']{0,80}[`\"'](?P<name>[^`\"']+)[`\"']",
            r"\b(?:to|for|on)\s+(?:the\s+)?(?P<name>[a-z0-9][a-z0-9 _*()/-]{0,60}?)\s+column\b",
            r"\bfilter(?:s|ed|ing)?\s+(?:only\s+)?(?:to|for|on\s+)?(?:the\s+)?(?P<name>[a-z0-9][a-z0-9 _*()/-]{0,60}?)\s+column\b",
            r"\b(?:to|for|on)\s+(?:the\s+)?(?P<name>[a-z0-9][a-z0-9 _*()/-]{0,60}?)(?=[.!?,]|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, instruction, flags=re.IGNORECASE)
            if not match:
                continue
            candidate = re.sub(r"\s+", " ", match.group("name")).strip(" .,:;`\"'")
            candidate = re.sub(r"^(?:only|the)\s+", "", candidate, flags=re.IGNORECASE)
            if candidate:
                return candidate
        return None

    @classmethod
    def _apply_column_filter(
        cls, workbook: Workbook, instruction: str
    ) -> tuple[dict[str, str], str]:
        requested_header = cls.filter_column_reference(instruction)
        error_header = requested_header or "requested"
        if requested_header is None:
            raise InvalidDocumentError(
                "I couldn't identify which column should be filtered. Name the column and try again."
            )

        workbook_has_values = False
        matching_sheets: list[tuple[Worksheet, Cell]] = []
        empty_matches: list[Worksheet] = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 25)):
                if not any(cell.value is not None for cell in row):
                    continue
                workbook_has_values = True
                matching_cell = cls._find_requested_header(row, requested_header)
                if matching_cell is None:
                    continue
                has_data = any(
                    any(cell.value is not None for cell in data_row)
                    for data_row in sheet.iter_rows(min_row=matching_cell.row + 1)
                )
                if has_data:
                    matching_sheets.append((sheet, matching_cell))
                else:
                    empty_matches.append(sheet)
                break

        if not workbook_has_values:
            raise InvalidDocumentError("The spreadsheet is empty and has no data to filter.")
        if not matching_sheets and empty_matches:
            raise InvalidDocumentError(
                f"The {error_header} column has a header row but no data rows to filter."
            )
        if not matching_sheets:
            raise InvalidDocumentError(
                f"I couldn't find a {error_header} column in the uploaded Excel file."
            )

        filtered_ranges = {}
        displayed_header = str(matching_sheets[0][1].value)
        for sheet, header_cell in matching_sheets:
            last_data_row = max(
                row_number
                for row_number in range(header_cell.row + 1, sheet.max_row + 1)
                if any(
                    sheet.cell(row=row_number, column=column).value is not None
                    for column in range(1, sheet.max_column + 1)
                )
            )
            column_letter = get_column_letter(header_cell.column)
            filter_range = f"{column_letter}{header_cell.row}:{column_letter}{last_data_row}"
            sheet.auto_filter.ref = filter_range
            filtered_ranges[sheet.title] = filter_range
        return filtered_ranges, displayed_header

    @classmethod
    def _find_requested_header(
        cls, header_row: tuple[Cell, ...], requested_header: str
    ) -> Cell | None:
        requested_key = cls._normalize_words(requested_header).replace(" ", "")
        category_keys = {"category", "itemcategory", "categoryname"}
        matches = []
        for cell in header_row:
            if cell.value is None:
                continue
            header_key = cls._normalize_words(str(cell.value)).replace(" ", "")
            if header_key == requested_key or (
                requested_key in category_keys and header_key in category_keys
            ):
                matches.append(cell)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def is_category_split_request(instruction: str) -> bool:
        normalized_instruction = instruction.casefold()
        asks_for_sheets = any(
            term in normalized_instruction
            for term in (
                "separate sheet",
                "separate worksheet",
                "sheet for each",
                "worksheet for each",
                "category-wise",
                "category wise",
                "split",
                "group into sheet",
                "group into worksheet",
                "one sheet per",
                "one worksheet per",
            )
        )
        asks_for_sheets = asks_for_sheets or (
            "category" in normalized_instruction
            and "group" in normalized_instruction
            and any(term in normalized_instruction for term in ("excel", "workbook"))
        )
        names_a_grouping_column = "category" in normalized_instruction or bool(
            re.search(
                r"\b(?:by|using|use|based on)\s+[a-z0-9][a-z0-9 _*()-]{0,60}",
                normalized_instruction,
            )
        )
        return asks_for_sheets and names_a_grouping_column

    @classmethod
    def _split_sheets_by_category(
        cls, source_workbook: Workbook, normalized_instruction: str
    ) -> tuple[Workbook, int, str]:
        candidates: list[tuple[int, Worksheet, int, tuple[Cell, ...], Cell]] = []
        workbook_has_values = False
        for sheet in source_workbook.worksheets:
            for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 25)):
                if not any(cell.value is not None for cell in row):
                    continue
                workbook_has_values = True
                grouping_cell = cls._find_grouping_header(row, normalized_instruction)
                if grouping_cell is None:
                    continue
                data_row_count = sum(
                    1
                    for data_row in sheet.iter_rows(min_row=grouping_cell.row + 1)
                    if any(cell.value is not None for cell in data_row)
                )
                candidates.append((data_row_count, sheet, grouping_cell.row, row, grouping_cell))
                break

        if not workbook_has_values:
            raise InvalidDocumentError("The spreadsheet is empty and has no item list to split.")
        if not candidates:
            raise InvalidDocumentError(
                "I could not find a Category column in the workbook. Add a header such as "
                "'Category', 'Item Category', or 'Category Name', then try again."
            )

        explicitly_named = [
            candidate
            for candidate in candidates
            if cls._sheet_is_requested(candidate[1].title, normalized_instruction)
        ]
        if len(explicitly_named) == 1:
            selected = explicitly_named[0]
        else:
            candidates.sort(key=lambda candidate: candidate[0], reverse=True)
            if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                names = ", ".join(f"'{candidate[1].title}'" for candidate in candidates[:3])
                raise InvalidDocumentError(
                    f"More than one worksheet could be the item list ({names}). "
                    "Mention the worksheet name in your instruction, for example 'from sheet Items'."
                )
            selected = candidates[0]

        data_row_count, source, header_number, header_row, grouping_cell = selected
        if data_row_count == 0:
            raise InvalidDocumentError(
                f"Worksheet '{source.title}' has a header row but no item rows to split."
            )

        output = Workbook()
        output.remove(output.active)
        targets: dict[str, Worksheet] = {}
        target_rows: dict[str, int] = {}
        for source_row in source.iter_rows(min_row=header_number + 1):
            if not any(cell.value is not None for cell in source_row):
                continue
            raw_category = source_row[grouping_cell.column - 1].value
            category = str(raw_category).strip() if raw_category is not None else ""
            category_key = category.casefold() if category else "\0uncategorized"
            target = targets.get(category_key)
            if target is None:
                target = output.create_sheet(cls._unique_category_sheet_title(output, category))
                targets[category_key] = target
                target_rows[category_key] = 1
                cls._copy_spreadsheet_row(header_row, target, 1)
                for column in range(1, source.max_column + 1):
                    source_width = source.column_dimensions[get_column_letter(column)].width
                    if source_width is not None:
                        target.column_dimensions[get_column_letter(column)].width = source_width
            target_rows[category_key] += 1
            cls._copy_spreadsheet_row(source_row, target, target_rows[category_key])

        if not targets:
            output.close()
            raise InvalidDocumentError(
                f"Worksheet '{source.title}' has no populated item rows to split."
            )
        return output, len(targets), source.title

    @classmethod
    def _find_grouping_header(
        cls, header_row: tuple[Cell, ...], normalized_instruction: str
    ) -> Cell | None:
        instruction_words = cls._normalize_words(normalized_instruction)
        explicit_matches = []
        default_matches = []
        for cell in header_row:
            if cell.value is None:
                continue
            header_words = cls._normalize_words(str(cell.value))
            header_key = header_words.replace(" ", "")
            if any(
                phrase in instruction_words
                for phrase in (
                    f"by {header_words}",
                    f"using {header_words}",
                    f"use {header_words}",
                    f"{header_words} column",
                    f"based on {header_words}",
                )
            ):
                explicit_matches.append(cell)
            if (
                header_key in {"category", "itemcategory", "categoryname"}
                or header_key.startswith("category")
                or header_key.endswith("category")
            ):
                default_matches.append(cell)
        return (explicit_matches or default_matches or [None])[0]

    @staticmethod
    def _sheet_is_requested(sheet_title: str, normalized_instruction: str) -> bool:
        instruction_words = ChatDocumentService._normalize_words(normalized_instruction)
        title_words = ChatDocumentService._normalize_words(sheet_title)
        return any(
            phrase in instruction_words
            for phrase in (
                f"sheet {title_words}",
                f"worksheet {title_words}",
                f"from {title_words}",
            )
        )

    @staticmethod
    def _normalize_words(value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())

    @staticmethod
    def _copy_spreadsheet_row(
        source_row: tuple[Cell, ...], target_sheet: Worksheet, target_row: int
    ) -> None:
        source_row_number = source_row[0].row
        source_height = source_row[0].parent.row_dimensions[source_row_number].height
        if source_height is not None:
            target_sheet.row_dimensions[target_row].height = source_height
        for source_cell in source_row:
            target_cell = target_sheet.cell(row=target_row, column=source_cell.column)
            target_cell.value = source_cell.value
            if source_cell.has_style:
                target_cell.font = copy(source_cell.font)
                target_cell.fill = copy(source_cell.fill)
                target_cell.border = copy(source_cell.border)
                target_cell.alignment = copy(source_cell.alignment)
                target_cell.number_format = source_cell.number_format
                target_cell.protection = copy(source_cell.protection)
            if source_cell.hyperlink:
                target_cell.hyperlink = copy(source_cell.hyperlink)
            if source_cell.comment:
                target_cell.comment = copy(source_cell.comment)

    @staticmethod
    def _unique_category_sheet_title(workbook: Workbook, category: str) -> str:
        cleaned = re.sub(r"[\\/*?:\[\]]+", " - ", category).strip(" '")
        cleaned = " ".join(cleaned.split()) or "Uncategorized"
        existing = {sheet.title.casefold() for sheet in workbook.worksheets}
        base = cleaned[:31].rstrip()
        if base.casefold() not in existing:
            return base
        for number in range(2, 10_000):
            suffix = f" ({number})"
            candidate = f"{cleaned[: 31 - len(suffix)].rstrip()}{suffix}"
            if candidate.casefold() not in existing:
                return candidate
        raise InvalidDocumentError("The workbook contains too many duplicate category names.")

    @staticmethod
    def _format_workbook(
        workbook: Workbook,
        *,
        horizontal: str | None,
        requested_columns: set[int],
        smart_alignment: bool,
        style_headers: bool,
        auto_fit: bool,
        wrap_text: bool,
        add_borders: bool,
        freeze_header: bool,
    ) -> None:
        thin_border = Border(
            left=Side(style="thin", color="D9E2F3"),
            right=Side(style="thin", color="D9E2F3"),
            top=Side(style="thin", color="D9E2F3"),
            bottom=Side(style="thin", color="D9E2F3"),
        )
        for sheet in workbook.worksheets:
            header_row = next(
                (row for row in sheet.iter_rows() if any(cell.value is not None for cell in row)),
                None,
            )
            if header_row is None:
                continue
            header_number = header_row[0].row
            maximum_widths: dict[int, int] = {}
            for row in sheet.iter_rows():
                if not any(cell.value is not None for cell in row):
                    continue
                for cell in row:
                    if cell.value is None:
                        continue
                    maximum_widths[cell.column] = max(
                        maximum_widths.get(cell.column, 0), len(str(cell.value))
                    )
                    alignment = copy(cell.alignment)
                    alignment_requested = not requested_columns or cell.column in requested_columns
                    if alignment_requested and horizontal is not None:
                        alignment.horizontal = horizontal
                    elif alignment_requested and smart_alignment:
                        alignment.horizontal = (
                            "center"
                            if cell.row == header_number
                            else "right"
                            if isinstance(cell.value, (int, float))
                            and not isinstance(cell.value, bool)
                            else "left"
                        )
                    alignment.vertical = "center"
                    if wrap_text:
                        alignment.wrap_text = True
                    cell.alignment = alignment
                    if add_borders:
                        cell.border = thin_border
            if style_headers:
                for cell in header_row:
                    if cell.value is None:
                        continue
                    cell.font = Font(
                        name=cell.font.name or "Calibri",
                        size=cell.font.sz or 11,
                        bold=True,
                        color="FFFFFF",
                    )
                    cell.fill = PatternFill("solid", fgColor="1F4E78")
                    alignment = copy(cell.alignment)
                    alignment.horizontal = horizontal or "center"
                    alignment.vertical = "center"
                    alignment.wrap_text = True
                    cell.alignment = alignment
            if auto_fit:
                for column, maximum in maximum_widths.items():
                    sheet.column_dimensions[get_column_letter(column)].width = min(
                        max(maximum + 2, 10), 60
                    )
            if freeze_header:
                sheet.freeze_panes = f"A{header_number + 1}"
            sheet.auto_filter.ref = (
                f"A{header_number}:{get_column_letter(sheet.max_column)}{sheet.max_row}"
            )

    async def summarize(self, raw_text: str, instruction: str | None) -> str:
        prompt = (
            "Read the uploaded document and respond to the user note using only its supported facts. "
            "If there is no specific request, provide a concise, structured summary. "
            "Preserve relevant headings, subheadings, names, dates, values, and table details. "
            "Summarize the actual subject of the document; do not omit information just because "
            "it is unrelated to nutrition. For health information, do not diagnose.\n\n"
            f"User note: {instruction or 'No additional note.'}\n\n"
            f"Document:\n{self._source_excerpt([raw_text])}"
        )
        return await self._complete(prompt, [])

    async def analyze(self, extracted: ExtractedDocument, instruction: str | None) -> str:
        """Use the shared text-provider chain only after deterministic extraction."""
        return await self.summarize(extracted.text, instruction)

    def extract_tables_to_excel(
        self, extracted: ExtractedDocument, source_filename: str
    ) -> tuple[bytes, str, str]:
        if extracted.document_type.value != "pdf":
            raise InvalidDocumentError("Table-to-Excel extraction requires a PDF document.")
        if not extracted.tables:
            raise InvalidDocumentError(
                "I couldn't find a structured table in this PDF. "
                "Scanned or borderless tables may need OCR-aware table recognition."
            )
        source_stem = Path(source_filename).stem or "document"
        try:
            generated = self.document_generator.generate_excel_from_tables(
                list(extracted.tables), requested_filename=f"{source_stem}-tables.xlsx"
            )
        except ValueError as error:
            raise InvalidDocumentError(str(error)) from error
        return generated.file_data, generated.filename, generated.content_type

    async def generate_content(
        self,
        instruction: str,
        summaries: list[str],
        history: list[ChatHistoryMessage],
        profile: dict | None = None,
    ) -> str:
        source = self._source_excerpt(summaries) or "No uploaded-document summary is available."
        prompt = (
            "Create document-ready content with a clear title, headings, concise sections, and "
            "actionable lists. Use only the supplied facts; flag missing information and do not "
            "provide a medical diagnosis.\n\n"
            f"Requested document: {instruction}\n\nUploaded document summaries:\n{source}"
            f"\n\nNutrition profile:\n{profile or 'No profile available.'}"
        )
        return await self._complete(prompt, history)

    @classmethod
    def _source_excerpt(cls, sources: list[str]) -> str:
        """Bound only AI context; saved source text and direct exports stay complete."""
        total_chars = sum(len(source) for source in sources) + max(0, len(sources) - 1) * 2
        remaining = cls.MAX_DOCUMENT_CONTEXT_CHARS
        parts = []
        for index, source in enumerate(sources):
            if index:
                separator = "\n\n"[:remaining]
                parts.append(separator)
                remaining -= len(separator)
            if remaining == 0:
                break
            excerpt = source[:remaining]
            parts.append(excerpt)
            remaining -= len(excerpt)
        excerpt = "".join(parts)
        if total_chars > cls.MAX_DOCUMENT_CONTEXT_CHARS:
            disclosure = (
                f"[Source excerpt truncated: first {cls.MAX_DOCUMENT_CONTEXT_CHARS:,} "
                f"of {total_chars:,} characters supplied. The remaining source content was not "
                "provided. Tell the user this limitation; do not claim to have read the full "
                "document or invent details from omitted content.]\n\n"
            )
            return disclosure + excerpt
        return excerpt

    async def _complete(self, prompt: str, history: list[ChatHistoryMessage]) -> str:
        # One deadline covers NVIDIA, any Ollama fallback, and all response tokens.
        # Cancelling async HTTP closes the connection instead of leaving a worker
        # thread generating for another five minutes after the UI reports failure.
        try:
            async with asyncio.timeout(settings.DOCUMENT_AI_TIMEOUT_SECONDS):
                async with aclosing(self.chat_service.stream_chat(prompt, history, [])) as stream:
                    chunks = [chunk async for chunk in stream]
        except TimeoutError as error:
            logger.warning("chat.document_ai_timeout")
            raise ChatModelUnavailableError(
                "Document AI generation timed out. Your draft is unchanged. Retry, or select "
                "'Export text without AI' to save your existing text as PDF or Word."
            ) from error
        except ChatModelUnavailableError as error:
            raise ChatModelUnavailableError(
                "Document AI generation could not finish. Your draft is unchanged. Retry, or "
                "select 'Export text without AI' to save your existing text as PDF or Word."
            ) from error
        content = "".join(chunks)
        if not content.strip():
            raise ChatModelUnavailableError(
                "The AI returned no document content. Retry, or use 'Export text without AI'."
            )
        return content

    def render(
        self,
        content: str,
        output_format: str,
        *,
        plain_text: bool = False,
        requested_filename: str | None = None,
    ) -> tuple[bytes, str, str]:
        try:
            generated = self.document_generator.generate(
                content,
                output_format,
                plain_text=plain_text,
                requested_filename=requested_filename,
            )
        except ValueError as error:
            raise InvalidDocumentError(str(error)) from error
        return generated.file_data, generated.filename, generated.content_type

    @staticmethod
    def _decode_text(file_data: bytes) -> str:
        from app.services.document.text_reader import TextDocumentReader

        return TextDocumentReader.decode(file_data)

    def _extract_pdf(self, file_data: bytes) -> str:
        return self.document_reader.pdf_reader.read(file_data).text
