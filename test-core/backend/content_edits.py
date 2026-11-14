import copy
import re
from .money import decimal_value, find_money

def apply_content_edits(document, overrides):
    identifier = document["id"]
    changed_cells = False
    changed_locators = set()
    for key, values in overrides.items():
        if key.startswith(f"cell:{identifier}:"):
            address = key[len(f"cell:{identifier}:"):]
            sheet, cell_address = address.rsplit("!", 1)
            from openpyxl.utils import coordinate_to_tuple
            row, column = coordinate_to_tuple(cell_address)
            previous = document["cells"].get(address, {})
            document["cells"][address] = dict(previous, address=cell_address, sheet=sheet, row=row, column=column,
                raw=values["value"], value=None, formula=None, dependencies=[], error=None, original_raw=previous.get("raw", ""), manual=True)
            changed_cells = True
        elif key.startswith(f"text:{identifier}:"):
            locator = key[len(f"text:{identifier}:"):]
            base, separator, cell = locator.rpartition("#")
            locator = base if separator else locator
            block = next((block for block in document["blocks"] if block.get("locator") == locator), None)
            if block:
                block.setdefault("original_text", block["text"])
                if separator:
                    fields = block["text"].split(" | ")
                    fields[int(cell)] = values["value"]
                    block["text"] = " | ".join(fields)
                else:
                    block["text"] = values["value"]
                block["manual"] = True
                changed_locators.add(locator)
        elif key.startswith(f"pdf_field:{identifier}:"):
            name = key[len(f"pdf_field:{identifier}:"):]
            for field in document.get("pdf_fields", []):
                if field["name"] == name:
                    field["original_value"] = field.get("value", "")
                    field.update(value=values["value"], manual=True)
                    for fact in document["facts"].values():
                        if fact["evidence"].get("pdf_field") == name:
                            fact["original_value"] = fact["value"]
                            try: fact.update(value=format(decimal_value(values["value"]), "f"), needs_review=False, manual=True)
                            except ValueError: fact.update(value=None, needs_review=True, manual=True)
    if changed_locators:
        from .extraction import extract_facts
        candidate = copy.deepcopy(document)
        candidate["facts"] = {}
        candidate["text"] = "\n".join(block["text"] for block in document["blocks"])
        extract_facts(candidate)
        document["text"] = candidate["text"]
        if candidate.get("category"):
            document["category"] = candidate["category"]
            document["category_reason"] = candidate.get("category_reason", "")
        for name, fact in document["facts"].items():
            if fact["evidence"].get("locator") not in changed_locators:
                continue
            replacement = candidate["facts"].get(name)
            fact.update(original_value=fact["value"], manual=True)
            if replacement and replacement["evidence"].get("locator") == fact["evidence"].get("locator"):
                fact.update(value=replacement["value"], needs_review=replacement["needs_review"])
            else:
                fact.update(value=None, needs_review=True)
    document["edited"] = bool(document.get("workbook_snapshot") or document.get("rich_document_snapshot")) or changed_cells or bool(changed_locators) or any(key.startswith((f"fact:{identifier}:", f"pdf_field:{identifier}:")) for key in overrides)
    return changed_cells, changed_locators

def validate_content_edit(result, kind, target, values):
    if set(values) != {"value"} or not isinstance(values["value"], str) or len(values["value"]) > 20000:
        raise ValueError("Введите значение поля длиной до 20 000 символов")
    identifier, separator, locator = target.partition(":")
    document = next((document for document in result["documents"] if document["id"] == identifier), None)
    if not separator or not document:
        raise ValueError("Документ поля не найден")
    if kind == "cell":
        if document.get("format") != "xlsx" or "!" not in locator:
            raise ValueError("Выберите ячейку Excel")
        sheet, address = locator.rsplit("!", 1)
        from openpyxl.utils import coordinate_to_tuple
        if sheet not in {cell["sheet"] for cell in document["cells"].values()} or not re.fullmatch(r"[A-Z]{1,3}[1-9]\d{0,4}", address):
            raise ValueError("Лист или ячейка отсутствует")
        row, column = coordinate_to_tuple(address)
        maximum_row = max([row] + [cell["row"] for cell in document["cells"].values() if cell["sheet"] == sheet])
        maximum_column = max([column] + [cell["column"] for cell in document["cells"].values() if cell["sheet"] == sheet])
        if maximum_row * maximum_column > 500000:
            raise ValueError("Размер листа превышает 500 000 ячеек")
    elif kind == "text":
        if document.get("format") not in ("docx", "txt"):
            raise ValueError("Текстовый редактор доступен для Word и TXT")
        base, separator, cell = locator.rpartition("#")
        block = next((block for block in document["blocks"] if block.get("locator") == (base if separator else locator)), None)
        if not block or separator and (not cell.isdigit() or int(cell) >= len(block["text"].split(" | "))):
            raise ValueError("Фрагмент текста не найден")
    elif kind == "pdf_field":
        if not any(field["name"] == locator for field in document.get("pdf_fields", [])):
            raise ValueError("Поле PDF не найдено")

def variable_still_present(variable, document, changed_locators):
    locator = variable["evidence"].get("locator")
    if locator not in changed_locators:
        return True
    text = next((block["text"] for block in document["blocks"] if block.get("locator") == locator), "")
    numbers = [decimal_value(value) for _, value in find_money(text)]
    numbers += [decimal_value(match.group()) for match in re.finditer(r"(?<![\d/.,])\d+(?![\d/.,])", text)]
    return decimal_value(variable["value"]) in numbers
