"""Apply appended rows to the original XLSX package without rewriting old cells."""

import posixpath
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

from lxml import etree
from openpyxl.utils.cell import column_index_from_string

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _xml(data: bytes):
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def _sheet_path(archive: ZipFile, name: str) -> str:
    workbook = _xml(archive.read("xl/workbook.xml"))
    relation = next(sheet for sheet in workbook.find(MAIN + "sheets") if sheet.get("name") == name)
    relationships = _xml(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(
        item.get("Target") for item in relationships if item.get("Id") == relation.get(REL + "id")
    )
    return target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)


def preserve_original_package(
    original_data: bytes, generated_data: bytes, sheet_name: str, start_row: int, end_row: int
) -> bytes:
    """Keep original parts, styles, shared strings and exact numeric XML values.

    openpyxl supplies validated new cells with inline strings and translated formulas.
    Existing cells are kept verbatim in the XML tree, including their cached values.
    """
    with ZipFile(BytesIO(original_data)) as original, ZipFile(BytesIO(generated_data)) as generated:
        path = _sheet_path(original, sheet_name)
        root = _xml(original.read(path))
        new_root = _xml(generated.read(_sheet_path(generated, sheet_name)))
        sheet_data = root.find(MAIN + "sheetData")
        original_rows = {int(row.get("r")): row for row in sheet_data}
        template = original_rows.get(start_row - 1)
        template_cells = (
            {cell.get("r").rstrip("0123456789"): cell for cell in template}
            if template is not None
            else {}
        )

        for new_row in new_root.find(MAIN + "sheetData"):
            index = int(new_row.get("r"))
            if not start_row <= index <= end_row:
                continue
            row = original_rows.get(index)
            if row is None:
                row = etree.Element(MAIN + "row", attrib=dict(new_row.attrib))
                sheet_data.append(row)
            elif row.get("ht") is None and new_row.get("ht") is not None:
                row.set("ht", new_row.get("ht"))
                row.set("customHeight", "1")
            cells = {cell.get("r"): cell for cell in row}
            for new_cell in new_row:
                coordinate = new_cell.get("r")
                old_cell = cells.get(coordinate)
                if old_cell is not None:
                    value = old_cell.find(MAIN + "v")
                    if (
                        old_cell.find(MAIN + "f") is not None
                        or (value is not None and value.text is not None)
                        or any(text.text for text in old_cell.iter(MAIN + "t"))
                    ):
                        continue
                copied = deepcopy(new_cell)
                # New cells inherit only styles that already exist in the original package.
                exemplar = template_cells.get(coordinate.rstrip("0123456789"))
                style = old_cell.get("s") if old_cell is not None else None
                if style in (None, "0") and exemplar is not None:
                    style = exemplar.get("s")
                if style is None:
                    copied.attrib.pop("s", None)
                else:
                    copied.set("s", style)
                if old_cell is not None:
                    row.replace(old_cell, copied)
                else:
                    row.append(copied)
            row[:] = sorted(
                row, key=lambda cell: column_index_from_string(cell.get("r").rstrip("0123456789"))
            )
        sheet_data[:] = sorted(sheet_data, key=lambda row: int(row.get("r")))

        # These existing ranges can expand to cover new records. Keep all other sheet features.
        for tag in ("dimension", "autoFilter", "dataValidations"):
            old_node, new_node = root.find(MAIN + tag), new_root.find(MAIN + tag)
            if old_node is not None and new_node is not None:
                root.replace(old_node, deepcopy(new_node))
        replacements = {path: etree.tostring(root, encoding="utf-8", xml_declaration=True)}
        generated_tables = {}
        for name in generated.namelist():
            if name.startswith("xl/tables/") and name.endswith(".xml"):
                table = _xml(generated.read(name))
                generated_tables[table.get("name")] = table
        for name in original.namelist():
            if name.startswith("xl/tables/") and name.endswith(".xml"):
                table = _xml(original.read(name))
                updated = generated_tables.get(table.get("name"))
                if updated is not None and updated.get("ref") != table.get("ref"):
                    table.set("ref", updated.get("ref"))
                    old_filter, new_filter = (
                        table.find(MAIN + "autoFilter"),
                        updated.find(MAIN + "autoFilter"),
                    )
                    if old_filter is not None and new_filter is not None:
                        old_filter.set("ref", new_filter.get("ref"))
                    replacements[name] = etree.tostring(
                        table, encoding="utf-8", xml_declaration=True
                    )
        workbook = _xml(original.read("xl/workbook.xml"))
        calculation = workbook.find(MAIN + "calcPr")
        if calculation is not None:
            calculation.set("fullCalcOnLoad", "1")
            replacements["xl/workbook.xml"] = etree.tostring(
                workbook, encoding="utf-8", xml_declaration=True
            )
        output = BytesIO()
        with ZipFile(output, "w") as archive:
            archive.comment = original.comment
            for info in original.infolist():
                archive.writestr(
                    info, replacements.get(info.filename, original.read(info.filename))
                )
        return output.getvalue()
