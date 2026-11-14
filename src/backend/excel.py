import re
from decimal import Decimal

from openpyxl.formula import Tokenizer
from openpyxl.utils.cell import range_boundaries, get_column_letter

from .money import decimal_value, evaluate_formula, money


def cell_key(sheet, address):
    return f"{sheet}!{address.replace('$', '').upper()}"


def range_cells(reference, current_sheet):
    if "!" in reference:
        sheet, address = reference.rsplit("!", 1)
        sheet = sheet.strip("'").replace("''", "'")
    else:
        sheet, address = current_sheet, reference
    if not re.fullmatch(r"\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?", address):
        raise ValueError(f"Неподдерживаемая ссылка {reference}")
    left, top, right, bottom = range_boundaries(address)
    if (right - left + 1) * (bottom - top + 1) > 10000:
        raise ValueError("Диапазон формулы превышает 10 000 ячеек")
    return [cell_key(sheet, f"{get_column_letter(column)}{row}")
            for row in range(top, bottom + 1) for column in range(left, right + 1)]


def read_workbook(workbook, known_blanks=None):
    cells = {}
    for sheet in workbook:
        if sheet.max_row * sheet.max_column > 500000:
            raise ValueError(f"Лист {sheet.title} превышает 500 000 ячеек")
        for row in sheet:
            for cell in row:
                if cell.value is None:
                    continue
                key = cell_key(sheet.title, cell.coordinate)
                cells[key] = {
                    "address": cell.coordinate, "sheet": sheet.title,
                    "row": cell.row, "column": cell.column,
                    "raw": str(cell.value), "value": None, "formula": None,
                    "dependencies": [], "error": None,
                }
                if cell.data_type == "f":
                    cells[key]["formula"] = cell.value
                elif type(cell.value) in (int, float, Decimal):
                    cells[key]["value"] = format(decimal_value(cell.value), "f")

    for key in known_blanks or []:
        if key not in cells:
            sheet, address = key.rsplit("!", 1)
            from openpyxl.utils import coordinate_to_tuple
            row, column = coordinate_to_tuple(address)
            cells[key] = {"address":address,"sheet":sheet,"row":row,"column":column,"raw":"","value":"0","formula":None,"dependencies":[],"error":None,"explicit_blank":True}

    resolving = set()

    def resolve(key):
        if key not in cells:
            # A blank input is unresolved, so missing documents cannot become zeros.
            raise ValueError(f"Пустая или отсутствующая ячейка {key}")
        cell = cells[key]
        if cell["error"]:
            raise ValueError(cell["error"])
        if not cell["formula"]:
            if cell["value"] is None:
                raise ValueError(f"Ячейка {key} содержит текст")
            return decimal_value(cell["value"])
        if key in resolving:
            raise ValueError(f"Циклическая ссылка на {key}")
        resolving.add(key)
        try:
            parts, inputs, dependencies = [], {}, []
            for token in Tokenizer(cell["formula"]).items:
                if token.type == "OPERAND" and token.subtype == "RANGE":
                    references = range_cells(token.value, cell["sheet"])
                    names = []
                    for reference in references:
                        alias = f"v{len(inputs)}"
                        inputs[alias] = resolve(reference)
                        names.append(alias)
                        dependencies.append(reference)
                    parts.append(",".join(names))
                elif token.type == "OPERAND" and token.subtype == "NUMBER":
                    parts.append(token.value)
                elif token.type in ("FUNC", "PAREN", "OPERATOR-PREFIX", "OPERATOR-INFIX", "SEP", "WHITE-SPACE"):
                    parts.append(token.value)
                elif token.type == "OPERATOR-POSTFIX" and token.value == "%":
                    parts.append("/100")
                else:
                    raise ValueError(f"Неподдерживаемый элемент формулы {token.value}")
            value, _ = evaluate_formula("".join(parts), inputs)
            cell["value"] = format(value, "f")
            cell["dependencies"] = list(dict.fromkeys(dependencies))
            return value
        except ValueError as exc:
            cell["error"] = str(exc)
            raise
        finally:
            resolving.remove(key)

    for key, cell in cells.items():
        if cell["formula"]:
            try:
                resolve(key)
            except ValueError:
                pass
    return cells
