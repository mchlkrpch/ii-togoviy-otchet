"""Local XLSX <-> Univer bridge. The original file is never overwritten."""

import copy
import io
import json
import re
from datetime import date, datetime
from uuid import uuid4
import xml.etree.ElementTree as ET

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter, coordinate_to_tuple
from openpyxl.utils.datetime import to_excel
from .original import color
from .excel import read_workbook

BORDERS = {
    "thin": 1,
    "hair": 2,
    "dotted": 3,
    "dashed": 4,
    "dashDot": 5,
    "dashDotDot": 6,
    "double": 7,
    "medium": 8,
    "mediumDashed": 9,
    "mediumDashDot": 10,
    "mediumDashDotDot": 11,
    "slantDashDot": 12,
    "thick": 13,
}
HORIZONTAL = {"left": 1, "center": 2, "right": 3, "justify": 4, "distributed": 6}
VERTICAL = {"top": 1, "center": 2, "bottom": 3}


def cell_value(cell):
    if cell.get("f"):
        return cell["f"]
    if cell.get("p", {}).get("body", {}).get("dataStream") is not None:
        return cell["p"]["body"]["dataStream"].rstrip("\r\n")
    value = cell.get("v")
    if cell.get("t") == 3 and value is not None:
        return bool(value)
    return value


def iter_cells(snapshot):
    for sheet_id in snapshot["sheetOrder"]:
        sheet = snapshot["sheets"][sheet_id]
        for row, columns in sheet.get("cellData", {}).items():
            for column, cell in columns.items():
                if cell is not None:
                    yield sheet, int(row), int(column), cell


def normalize_snapshot(snapshot, document_id, previous_origins=None):
    if (
        not isinstance(snapshot, dict)
        or len(json.dumps(snapshot, ensure_ascii=False)) > 15_000_000
    ):
        raise ValueError("Книга должна быть объектом размером до 15 МБ")
    order = snapshot.get("sheetOrder")
    sheets = snapshot.get("sheets")
    if (
        not isinstance(order, list)
        or not 1 <= len(order) <= 100
        or not isinstance(sheets, dict)
        or len(set(order)) != len(order)
    ):
        raise ValueError("Неверный список листов книги")
    result = copy.deepcopy(snapshot)
    names, count = set(), 0
    for identifier in order:
        sheet = sheets.get(identifier)
        if (
            not isinstance(sheet, dict)
            or not isinstance(sheet.get("name"), str)
            or not sheet["name"].strip()
            or len(sheet["name"]) > 31
            or re.search(r"[\\/*?:\[\]]", sheet["name"])
        ):
            raise ValueError("Некорректное имя листа")
        if sheet["name"].casefold() in names:
            raise ValueError("Имена листов должны быть уникальными")
        names.add(sheet["name"].casefold())
        rows, cols = sheet.get("rowCount", 1000), sheet.get("columnCount", 26)
        if (
            not isinstance(rows, int)
            or not isinstance(cols, int)
            or not 1 <= rows <= 100000
            or not 1 <= cols <= 16384
            or rows * cols > 2_000_000
        ):
            raise ValueError("Слишком большой лист: максимум 2 000 000 ячеек")
        if not isinstance(sheet.get("cellData", {}), dict):
            raise ValueError("Некорректные ячейки листа")
    preferred = {}
    for sheet, row, column, cell in iter_cells(result):
        origin = (
            (cell.get("custom") or {}).get("auditOrigin")
            if isinstance(cell, dict)
            else None
        )
        if origin:
            location = f"{sheet['name']}!{get_column_letter(column+1)}{row+1}"
            if location == (previous_origins or {}).get(origin, origin):
                preferred[origin] = location
    origins = set()
    for sheet, row, column, cell in iter_cells(result):
        count += 1
        if (
            count > 100000
            or not 0 <= row < sheet.get("rowCount", 1000)
            or not 0 <= column < sheet.get("columnCount", 26)
            or not isinstance(cell, dict)
        ):
            raise ValueError(
                "Недопустимый диапазон или слишком много заполненных ячеек"
            )
        value = cell_value(cell)
        if (
            not isinstance(value, (str, int, float, bool, type(None)))
            or isinstance(value, str)
            and len(value) > 32767
        ):
            raise ValueError("Неподдерживаемое значение ячейки")
        custom = cell.setdefault("custom", {})
        if not isinstance(custom, dict):
            raise ValueError("Некорректные метаданные ячейки")
        origin = custom.get("auditOrigin")
        # Copy/paste may duplicate custom metadata; only the first occurrence
        # retains identity. New cells get independent immutable identities.
        location = f"{sheet['name']}!{get_column_letter(column+1)}{row+1}"
        if (
            not isinstance(origin, str)
            or origin in origins
            or origin in preferred
            and preferred[origin] != location
        ):
            origin = "added_" + uuid4().hex
            custom["auditOrigin"] = origin
        origins.add(origin)
    result["id"] = document_id
    return result


def workbook_snapshot(path, document):
    saved = document.get("workbook_snapshot")
    if saved:
        snapshot = copy.deepcopy(saved)
        for sheet, row, column, cell in iter_cells(snapshot):
            address = f"{get_column_letter(column+1)}{row+1}"
            effective = document["cells"].get(f"{sheet['name']}!{address}")
            if not effective:
                continue
            raw = effective["raw"]
            value = cell_value(cell)
            if str(value) == raw or value is None and raw == "":
                continue
            if raw.startswith("="):
                cell.update(f=raw)
            else:
                cell.pop("f", None)
                cell.pop("p", None)
                try:
                    cell.update(v=float(raw.replace(",", ".")), t=2)
                except ValueError:
                    cell.update(v=raw, t=1)
        return snapshot
    workbook = load_workbook(path)
    try:
        theme = []
        if workbook.loaded_theme:
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            scheme = ET.fromstring(workbook.loaded_theme).find(
                "a:themeElements/a:clrScheme", ns
            )
            if scheme is not None:
                palette = {
                    item.tag.rsplit("}", 1)[-1]: list(item)[0].get(
                        "lastClr", list(item)[0].get("val", "000000")
                    )
                    for item in scheme
                }
                theme = [
                    palette.get(name, "000000")
                    for name in (
                        "lt1",
                        "dk1",
                        "lt2",
                        "dk2",
                        "accent1",
                        "accent2",
                        "accent3",
                        "accent4",
                        "accent5",
                        "accent6",
                        "hlink",
                        "folHlink",
                    )
                ]
        snapshot = {
            "id": document["id"],
            "name": document["name"],
            "appVersion": "1.5.0",
            "locale": "ruRU",
            "styles": {},
            "sheetOrder": [],
            "sheets": {},
        }
        notes = {}
        for index, sheet in enumerate(workbook):
            identifier = f"sheet-{index}"
            snapshot["sheetOrder"].append(identifier)
            data = {
                "id": identifier,
                "name": sheet.title,
                "rowCount": max(1000, sheet.max_row + 100),
                "columnCount": max(26, sheet.max_column + 5),
                "defaultRowHeight": 20,
                "defaultColumnWidth": 96,
                "cellData": {},
                "rowData": {},
                "columnData": {},
                "mergeData": [],
                "showGridlines": int(sheet.sheet_view.showGridLines is not False),
                "zoomRatio": 1,
                "hidden": {"visible": 0, "hidden": 1, "veryHidden": 2}.get(
                    sheet.sheet_state, 0
                ),
            }
            snapshot["sheets"][identifier] = data
            for key, dimension in sheet.row_dimensions.items():
                data["rowData"][str(key - 1)] = {
                    "h": (dimension.height or 15) * 4 / 3,
                    "hd": int(dimension.hidden),
                }
            for dimension in sheet.column_dimensions.values():
                for col in range(
                    dimension.min or 1, (dimension.max or dimension.min or 1) + 1
                ):
                    data["columnData"][str(col - 1)] = {
                        "w": round((dimension.width or 8.43) * 7 + 5),
                        "hd": int(dimension.hidden),
                    }
            for area in sheet.merged_cells.ranges:
                data["mergeData"].append(
                    {
                        "startRow": area.min_row - 1,
                        "endRow": area.max_row - 1,
                        "startColumn": area.min_col - 1,
                        "endColumn": area.max_col - 1,
                    }
                )
            if sheet.freeze_panes:
                row, col = coordinate_to_tuple(str(sheet.freeze_panes))
                data["freeze"] = {
                    "startRow": row - 1,
                    "startColumn": col - 1,
                    "xSplit": col - 1,
                    "ySplit": row - 1,
                }
            for row in sheet:
                for source in row:
                    if source.value is None and not source.has_style:
                        continue
                    style = {
                        "ff": source.font.name or "Arial",
                        "fs": source.font.sz or 11,
                        "bl": int(bool(source.font.b)),
                        "it": int(bool(source.font.i)),
                        "cl": {"rgb": color(source.font.color, "#000000", theme)},
                        "ht": HORIZONTAL.get(source.alignment.horizontal, 0),
                        "vt": VERTICAL.get(source.alignment.vertical, 3),
                        "tb": 3 if source.alignment.wrap_text else 1,
                        "n": {"pattern": source.number_format},
                    }
                    if source.font.u:
                        style["ul"] = {
                            "s": 1,
                            "t": 10 if source.font.u.startswith("double") else 12,
                        }
                    if source.font.strike:
                        style["st"] = {"s": 1}
                    if source.fill.patternType == "solid":
                        style["bg"] = {
                            "rgb": color(source.fill.fgColor, "#ffffff", theme)
                        }
                    borders = {}
                    for short, long in (
                        ("t", "top"),
                        ("b", "bottom"),
                        ("l", "left"),
                        ("r", "right"),
                    ):
                        side = getattr(source.border, long)
                        if side and side.style:
                            borders[short] = {
                                "s": BORDERS.get(side.style, 1),
                                "cl": {"rgb": color(side.color, "#000000", theme)},
                            }
                    if borders:
                        style["bd"] = borders
                    if source.alignment.textRotation:
                        style["tr"] = (
                            {"v": 1, "a": 0}
                            if source.alignment.textRotation == 255
                            else {
                                "a": (
                                    source.alignment.textRotation
                                    if source.alignment.textRotation <= 90
                                    else 90 - source.alignment.textRotation
                                )
                            }
                        )
                    value = source.value
                    cell = {
                        "s": style,
                        "custom": {"auditOrigin": f"{sheet.title}!{source.coordinate}"},
                    }
                    effective = document["cells"].get(
                        f"{sheet.title}!{source.coordinate}"
                    )
                    if effective and str(value) != effective["raw"]:
                        raw = effective["raw"]
                        if raw.startswith("="):
                            value = raw
                        else:
                            try:
                                value = float(raw.replace(",", "."))
                            except ValueError:
                                value = raw
                    if isinstance(value, (datetime, date)):
                        value = to_excel(value, workbook.epoch)
                    if isinstance(value, str) and value.startswith("="):
                        cell.update(
                            f=value,
                            v=(
                                float(effective["value"])
                                if effective and effective.get("value") is not None
                                else None
                            ),
                        )
                    elif isinstance(value, bool):
                        cell.update(v=int(value), t=3)
                    else:
                        cell.update(
                            v=value, t=2 if isinstance(value, (int, float)) else 1
                        )
                    data["cellData"].setdefault(str(source.row - 1), {})[
                        str(source.column - 1)
                    ] = cell
                    if source.comment:
                        notes.setdefault(identifier, {}).setdefault(
                            str(source.row - 1), {}
                        )[str(source.column - 1)] = {
                            "id": uuid4().hex,
                            "row": source.row - 1,
                            "col": source.column - 1,
                            "width": source.comment.width,
                            "height": source.comment.height,
                            "note": source.comment.text,
                            "show": False,
                        }
                    # Excel auto-fits rows without an explicit height. Preserve
                    # manual heights, but do not clip multiline headings.
                    if (
                        sheet.row_dimensions[source.row].height is None
                        and isinstance(value, str)
                        and not value.startswith("=")
                    ):
                        lines = value.splitlines() or [""]
                        area = next(
                            (
                                area
                                for area in sheet.merged_cells.ranges
                                if source.coordinate in area
                            ),
                            None,
                        )
                        last = area.max_col if area else source.column
                        width = sum(
                            data["columnData"]
                            .get(str(col - 1), {})
                            .get("w", data["defaultColumnWidth"])
                            for col in range(source.column, last + 1)
                        )
                        if source.alignment.wrap_text:
                            import math

                            count = sum(
                                max(
                                    1,
                                    math.ceil(
                                        len(line)
                                        * (source.font.sz or 11)
                                        * 0.65
                                        / max(1, width - 6)
                                    ),
                                )
                                for line in lines
                            )
                        else:
                            count = len(lines)
                        height = max(
                            20, round(count * (source.font.sz or 11) * 1.5 + 4)
                        )
                        current = data["rowData"].setdefault(str(source.row - 1), {})
                        current["h"] = max(current.get("h", 20), height)
        if notes:
            snapshot["resources"] = [
                {
                    "name": "SHEET_NOTE_PLUGIN",
                    "data": json.dumps(notes, ensure_ascii=False),
                }
            ]
        return snapshot
    finally:
        workbook.close()


def snapshot_workbook(snapshot):
    workbook = Workbook()
    workbook.remove(workbook.active)
    styles = snapshot.get("styles", {})
    notes = {}
    for resource in snapshot.get("resources", []):
        if resource.get("name") == "SHEET_NOTE_PLUGIN" and resource.get("data"):
            notes = json.loads(resource["data"])
    for identifier in snapshot["sheetOrder"]:
        data = snapshot["sheets"][identifier]
        sheet = workbook.create_sheet(data["name"])
        sheet.sheet_state = {0: "visible", 1: "hidden", 2: "veryHidden"}.get(
            data.get("hidden", 0), "visible"
        )
        sheet.sheet_view.showGridLines = bool(data.get("showGridlines", 1))
        sheet.sheet_view.zoomScale = round(
            max(10, min(400, data.get("zoomRatio", 1) * 100))
        )
        for row, dimension in data.get("rowData", {}).items():
            target = sheet.row_dimensions[int(row) + 1]
            target.height = dimension.get("h", 20) * 0.75
            target.hidden = bool(dimension.get("hd", 0))
        for column, dimension in data.get("columnData", {}).items():
            target = sheet.column_dimensions[get_column_letter(int(column) + 1)]
            target.width = max(0.1, (dimension.get("w", 96) - 5) / 7)
            target.hidden = bool(dimension.get("hd", 0))
        freeze = data.get("freeze", {})
        if freeze.get("xSplit") or freeze.get("ySplit"):
            column = (
                max(0, freeze.get("startColumn", freeze.get("xSplit", 0)))
                if freeze.get("xSplit")
                else 0
            )
            row = (
                max(0, freeze.get("startRow", freeze.get("ySplit", 0)))
                if freeze.get("ySplit")
                else 0
            )
            sheet.freeze_panes = f"{get_column_letter(column+1)}{row+1}"
        for row, columns in data.get("cellData", {}).items():
            for column, cell in columns.items():
                if not cell:
                    continue
                target = sheet.cell(int(row) + 1, int(column) + 1)
                target.value = cell_value(cell)
                style = cell.get("s") or {}
                if isinstance(style, str):
                    style = styles.get(style) or {}

                def rgb(value, default="000000"):
                    value = (value or {}).get("rgb", default)
                    if re.fullmatch(r"#?[0-9a-fA-F]{6,8}", str(value)):
                        return value.lstrip("#")[-6:]
                    return default

                target.font = Font(
                    name=style.get("ff", "Arial"),
                    size=style.get("fs", 11),
                    bold=bool(style.get("bl")),
                    italic=bool(style.get("it")),
                    color=rgb(style.get("cl")),
                    underline=(
                        (
                            "double"
                            if (style.get("ul") or {}).get("t") == 10
                            else "single"
                        )
                        if (style.get("ul") or {}).get("s")
                        else None
                    ),
                    strike=bool((style.get("st") or {}).get("s")),
                )
                if style.get("bg"):
                    target.fill = PatternFill(
                        "solid", fgColor=rgb(style["bg"], "FFFFFF")
                    )
                rotation = style.get("tr") or {}
                angle = rotation.get("a", 0)
                angle = (
                    255
                    if rotation.get("v")
                    else min(180, max(0, angle if angle >= 0 else 90 - angle))
                )
                target.alignment = Alignment(
                    horizontal={v: k for k, v in HORIZONTAL.items()}.get(
                        style.get("ht")
                    ),
                    vertical={v: k for k, v in VERTICAL.items()}.get(style.get("vt")),
                    wrap_text=style.get("tb") == 3,
                    textRotation=angle,
                )
                sides = {}
                for short, long in (
                    ("t", "top"),
                    ("b", "bottom"),
                    ("l", "left"),
                    ("r", "right"),
                ):
                    border = (style.get("bd") or {}).get(short)
                    if border:
                        sides[long] = Side(
                            style={v: k for k, v in BORDERS.items()}.get(
                                border.get("s"), "thin"
                            ),
                            color=rgb(border.get("cl")),
                        )
                target.border = Border(**sides)
                target.number_format = (style.get("n") or {}).get("pattern", "General")
        for row, columns in notes.get(identifier, {}).items():
            for column, note in columns.items():
                target = sheet.cell(int(row) + 1, int(column) + 1)
                target.comment = Comment(note.get("note", ""), "Проверка")
                target.comment.width = note.get("width", 144)
                target.comment.height = note.get("height", 79)
        for area in data.get("mergeData", []):
            sheet.merge_cells(
                start_row=area["startRow"] + 1,
                end_row=area["endRow"] + 1,
                start_column=area["startColumn"] + 1,
                end_column=area["endColumn"] + 1,
            )
    if not any(sheet.sheet_state == "visible" for sheet in workbook):
        raise ValueError("Хотя бы один лист должен быть видимым")
    return workbook


def snapshot_bytes(snapshot):
    workbook = snapshot_workbook(snapshot)
    try:
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()
    finally:
        workbook.close()


def apply_snapshot(document, snapshot):
    """Rebind old fact IDs by immutable cell identity, never by old address."""
    snapshot = copy.deepcopy(snapshot)
    document["workbook_snapshot"] = snapshot
    workbook = snapshot_workbook(snapshot)
    known_blanks = {
        f"{sheet['name']}!{get_column_letter(column+1)}{row+1}"
        for sheet, row, column, cell in iter_cells(snapshot)
        if cell_value(cell) is None
        and cell.get("custom", {}).get("auditOrigin", "").startswith("added_")
    }
    try:
        cells = read_workbook(workbook, known_blanks)
    finally:
        workbook.close()
    origins = {}
    reverse = {}
    for sheet, row, column, cell in iter_cells(snapshot):
        key = f"{sheet['name']}!{get_column_letter(column+1)}{row+1}"
        origin = cell.get("custom", {}).get("auditOrigin")
        if origin:
            origins[origin] = key
            reverse[key] = origin
    document["cell_origins"] = origins
    original_cells = set(document["cells"])
    aliases = {new: old for old, new in origins.items() if old in document["facts"]}
    for fact in document["facts"].values():
        evidence = fact["evidence"]
        if not evidence.get("cell"):
            continue
        old = f"{evidence['sheet']}!{evidence['cell']}"
        new = origins.get(old)
        if new:
            sheet, address = new.rsplit("!", 1)
            evidence.update(sheet=sheet, cell=address, locator=new)
            cell = cells.get(new)
            if fact["key"] in original_cells:
                if cell:
                    fact.update(
                        value=cell["value"],
                        formula=cell["formula"],
                        error=cell["error"],
                        needs_review=bool(cell["error"]),
                        dependencies=[
                            document["id"]
                            + ":"
                            + aliases.get(ref, reverse.get(ref, ref))
                            for ref in cell["dependencies"]
                        ],
                    )
                else:
                    fact.update(
                        value=None,
                        formula=None,
                        error="Исходная ячейка очищена",
                        needs_review=True,
                        dependencies=[],
                    )
        elif old in original_cells:
            fact.update(
                value=None,
                error="Исходная ячейка удалена",
                needs_review=True,
                dependencies=[],
            )
    from .extraction import add_fact

    for key, cell in cells.items():
        identity = aliases.get(key, reverse.get(key, key))
        if identity not in document["facts"] and cell["value"] is not None:
            add_fact(
                document,
                identity,
                cell["address"],
                cell["value"],
                {
                    "sheet": cell["sheet"],
                    "cell": cell["address"],
                    "locator": key,
                    "text": cell["raw"],
                    "method": "xlsx",
                    "confidence": 100,
                },
                dependencies=[
                    document["id"] + ":" + aliases.get(ref, reverse.get(ref, ref))
                    for ref in cell["dependencies"]
                ],
                formula=cell["formula"],
            )
    for field in document["report_fields"]:
        evidence = field["evidence"]
        old = f"{evidence.get('sheet')}!{evidence.get('cell')}"
        new = origins.get(old)
        if new:
            sheet, address = new.rsplit("!", 1)
            evidence.update(sheet=sheet, cell=address, locator=new)
            value = cells.get(new, {})
            field.update(
                value=value.get("value"),
                needs_review=bool(value.get("error")) or value.get("value") is None,
                manual=True,
            )
        elif old in original_cells:
            field.update(value=None, needs_review=True, manual=True)
    document["cells"] = cells
    document["edited"] = True
    # Re-read metadata from the edited rows as well. A changed contract or
    # month must affect source inclusion, not leave stale original metadata.
    rows = {}
    for cell in cells.values():
        if cell["raw"]:
            rows.setdefault((cell["sheet"], cell["row"]), []).append(cell)
    document["blocks"] = [
        {
            "text": " | ".join(
                cell["raw"] for cell in sorted(entries, key=lambda cell: cell["column"])
            ),
            "locator": f"{sheet}, строка {row}",
            "method": "xlsx",
            "confidence": 100,
            "id": f"b{index}",
        }
        for index, ((sheet, row), entries) in enumerate(sorted(rows.items()))
    ]
    document["text"] = "\n".join(block["text"] for block in document["blocks"])
    from .extraction import extract_facts

    candidate = copy.deepcopy(document)
    candidate["facts"] = {}
    candidate["errors"] = []
    extract_facts(candidate)
    for name, fact in document["facts"].items():
        if fact["kind"] == "money" or name in original_cells:
            continue
        replacement = candidate["facts"].get(name)
        if replacement:
            fact.update(
                value=replacement["value"],
                evidence=replacement["evidence"],
                needs_review=replacement["needs_review"],
            )
        elif name in ("contract", "period", "date", "number", "contractor"):
            fact.update(
                value=None,
                needs_review=True,
                error="Реквизит отсутствует в измененной книге",
            )
