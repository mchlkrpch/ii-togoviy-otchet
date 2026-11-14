"""Read original spreadsheet layout without changing the source workbook."""
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re
import xml.etree.ElementTree as ET

from openpyxl import load_workbook
from openpyxl.styles.colors import COLOR_INDEX
from openpyxl.styles.numbers import is_date_format
from openpyxl.utils import get_column_letter

from .excel import read_workbook
from .money import decimal_value

def readable_color(foreground, background):
    def luminance(hex_color):
        rgb = [int(hex_color[index:index+2], 16) / 255 for index in (1,3,5)]
        channels = [channel/12.92 if channel <= .04045 else ((channel+.055)/1.055)**2.4 for channel in rgb]
        return .2126*channels[0]+.7152*channels[1]+.0722*channels[2]
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    if (light+.05)/(dark+.05) >= 4.5:
        return foreground
    return "#111111" if luminance(background) > .18 else "#ffffff"


def color(value, default=None, theme=None):
    if not value:
        return default
    if value.type == "rgb" and isinstance(value.rgb, str):
        return "#" + value.rgb[-6:]
    if value.type == "indexed" and 0 <= value.indexed < len(COLOR_INDEX):
        return "#" + COLOR_INDEX[value.indexed][-6:]
    if value.type == "theme" and theme and 0 <= value.theme < len(theme):
        rgb = theme[value.theme]
        if value.tint:
            channels = [int(rgb[index:index + 2], 16) for index in (0, 2, 4)]
            channels = [round(channel * (1 + value.tint) if value.tint < 0 else channel * (1 - value.tint) + 255 * value.tint) for channel in channels]
            rgb = "".join(f"{channel:02x}" for channel in channels)
        return "#" + rgb
    return default


def display_value(value, number_format):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if not isinstance(value, (int, float, Decimal)):
        return str(value)
    if number_format == "General" or is_date_format(number_format):
        return str(value)
    pattern = number_format.split(";")[0]
    decimal = re.search(r"\.([0#]+)", pattern)
    places = len(decimal[1]) if decimal else 0
    number = Decimal(str(value)) * (100 if "%" in pattern else 1)
    with localcontext() as context:
        context.prec = max(64, len(str(number)) + places + 5)
        number = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    formatted = format(number, f",.{places}f" if "," in pattern else f".{places}f")
    formatted = formatted.replace(",", "\u00a0").replace(".", ",")
    return formatted + ("%" if "%" in pattern else "")


def workbook_layout(path, sheet_name=None, row=1, column=1, cells=None, report_fields=None):
    workbook = load_workbook(path, data_only=False)
    try:
        for cell in (cells or {}).values():
            if cell["sheet"] in workbook.sheetnames:
                raw = cell["raw"]
                if str(workbook[cell["sheet"]][cell["address"]].value) == raw:continue
                try: value = decimal_value(raw) if not raw.startswith("=") else raw
                except ValueError: value = raw
                workbook[cell["sheet"]][cell["address"]] = value
        for field in report_fields or []:
            evidence = field["evidence"]
            if field.get("reported_override") and evidence.get("sheet") in workbook.sheetnames and evidence.get("cell"):
                workbook[evidence["sheet"]][evidence["cell"]] = decimal_value(field["value"]) if field["value"] is not None else None
        theme = []
        if workbook.loaded_theme:
            namespace = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            scheme = ET.fromstring(workbook.loaded_theme).find("a:themeElements/a:clrScheme", namespace)
            if scheme is not None:
                palette = {item.tag.rsplit("}",1)[-1]: list(item)[0].get("lastClr", list(item)[0].get("val", "000000")) for item in scheme}
                theme = [palette.get(name, "FFFFFF" if name.startswith("lt") else "000000") for name in
                         ("lt1","dk1","lt2","dk2","accent1","accent2","accent3","accent4","accent5","accent6","hlink","folHlink")]
        sheet = workbook[sheet_name] if sheet_name in workbook.sheetnames else workbook.active
        if sheet.max_row * sheet.max_column > 500000:
            raise ValueError("Лист превышает 500 000 ячеек")
        values = read_workbook(workbook)
        end_row = min(sheet.max_row, row + 199)
        end_column = min(sheet.max_column, column + 99)
        merges = list(sheet.merged_cells.ranges)
        hidden_columns = {index for dimension in sheet.column_dimensions.values() if dimension.hidden
                          for index in range(dimension.min or 1, (dimension.max or dimension.min or 1) + 1)}
        def column_width(index):
            dimension = next((item for item in sheet.column_dimensions.values() if (item.min or 1) <= index <= (item.max or item.min or 1)), None)
            return round((dimension.width if dimension else sheet.sheet_format.defaultColWidth or 8.43) * 7 + 5)
        columns = [{"index": index, "name": get_column_letter(index),
                    "width": column_width(index)}
                   for index in range(column, end_column + 1) if index not in hidden_columns]
        rows = []
        for row_index in range(row, end_row + 1):
            if sheet.row_dimensions[row_index].hidden:
                continue
            cells = []
            for col in columns:
                cell = sheet.cell(row_index, col["index"])
                merged = next((area for area in merges if cell.coordinate in area), None)
                if merged and (cell.row != merged.min_row or cell.column != merged.min_col):
                    continue
                value = cell.value
                if cell.data_type == "f":
                    value = values.get(f"{sheet.title}!{cell.coordinate}", {}).get("value")
                    value = Decimal(value) if value is not None else cell.value
                style = {"fontFamily": cell.font.name or "Calibri", "fontSize": f"{cell.font.sz or 11}pt",
                         "fontWeight": "bold" if cell.font.b else "normal", "fontStyle": "italic" if cell.font.i else "normal",
                         "color": color(cell.font.color, "#111111", theme), "backgroundColor": color(cell.fill.fgColor, "#ffffff", theme) if cell.fill.patternType else "#ffffff",
                         "textAlign": cell.alignment.horizontal if cell.alignment.horizontal in {"left", "center", "right", "justify"} else "right" if isinstance(value, (int, float, Decimal)) else "left",
                         "verticalAlign": {"center": "middle"}.get(cell.alignment.vertical, cell.alignment.vertical or "bottom"),
                         "whiteSpace": "pre-wrap" if cell.alignment.wrap_text else "pre",
                         "textDecoration": "underline" if cell.font.u else "none"}
                style["color"] = readable_color(style["color"], style["backgroundColor"])
                for edge in ("top", "right", "bottom", "left"):
                    side = getattr(cell.border, edge)
                    if side and side.style:
                        weight = 2 if side.style in {"medium", "thick", "double"} else 1
                        stroke = "dashed" if "dash" in side.style.lower() else "dotted" if side.style == "dotted" else "solid"
                        style["border" + edge.title()] = f"{weight}px {stroke} {color(side.color, '#333333', theme)}"
                cells.append({"address": cell.coordinate, "text": display_value(value, cell.number_format), "style": style,
                              "rowSpan": min(merged.max_row, end_row) - row_index + 1 if merged else 1,
                              "colSpan": sum(1 for c in columns if merged.min_col <= c["index"] <= merged.max_col) if merged else 1,
                              "formula": cell.value if cell.data_type == "f" else None})
            rows.append({"index": row_index, "height": (sheet.row_dimensions[row_index].height or sheet.sheet_format.defaultRowHeight or 15) * 4 / 3, "cells": cells})
        return {"sheet": sheet.title, "sheets": workbook.sheetnames, "columns": columns, "rows": rows,
                "lastRow": sheet.max_row, "lastColumn": sheet.max_column, "rowStart": row, "columnStart": column}
    finally:
        workbook.close()
