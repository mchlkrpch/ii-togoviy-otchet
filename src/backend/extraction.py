import csv
import hashlib
import io
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pymupdf
from openpyxl import load_workbook
from PIL import Image, ImageOps

from .excel import cell_key, read_workbook
from .money import decimal_value, find_money, money


SUPPORTED = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt"}
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма[йя]": 5,
          "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
          "ноябр": 11, "декабр": 12}
REPORT_DATE = r"(?:\d{1,2}\.\d{1,2}\.20\d{2}|\d{1,2}\s+(?:" + "|".join(MONTHS) + r")[а-яё]*\s+20\d{2})\b"
STAGE_BOUNDARY = re.compile(
    rf"\b(?P<label>начало|окончание|конец)\s+(?:(?P<stage>\d+)\s*[-–]?\s*(?:го\s+)?)?"
    rf"(?P<scope>этапа|отч[её]тного\s+периода)\s*[:—–-]?\s*(?P<date>{REPORT_DATE})", re.I,
)
REPORT_PERIOD = re.compile(
    rf"\bотч[её]тный\s+период\s*[:—–-]?\s*(?:с\s+)?({REPORT_DATE})"
    rf"\s*(?:г\.?\s*)?(?:по|до|[-–—])\s*({REPORT_DATE})", re.I,
)
REPORT_COLUMNS = {
    3: ("labor", "Трудоемкость", "чел.-мес."),
    4: ("0100", "Материальные затраты", "RUB"),
    5: ("0200", "Оплата труда — всего", "RUB"),
    6: ("0200.salary", "Заработная плата без резерва", "RUB"),
    7: ("0200.main", "Основная заработная плата", "RUB"),
    8: ("0200.additional", "Дополнительная заработная плата", "RUB"),
    9: ("0200.reserve", "Резерв отпусков", "RUB"),
    10: ("0300", "Страховые взносы — всего", "RUB"),
    11: ("0300.salary", "Взносы с заработной платы", "RUB"),
    12: ("0300.main", "Взносы с основной зарплаты", "RUB"),
    13: ("0300.additional", "Взносы с дополнительной зарплаты", "RUB"),
    14: ("0300.reserve", "Взносы с резерва отпусков", "RUB"),
    15: ("0800", "Общепроизводственные затраты", "RUB"),
    16: ("0900", "Общехозяйственные затраты", "RUB"),
    17: ("total", "Итого по этапу", "RUB"),
}


def normalize(text):
    return " ".join(text.replace("\x00", "").split())


def ocr_image(image, language="rus+eng", psm=6):
    if not shutil.which("tesseract"):
        raise ValueError("Для сканов нужен Tesseract с языками rus и eng. См. инструкцию запуска.")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", language, "--psm", str(psm), "tsv"],
        input=buffer.getvalue(), capture_output=True, timeout=90,
    )
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode or "Failed loading language" in stderr:
        raise ValueError(f"Ошибка OCR: {stderr.strip()}")
    groups = defaultdict(list)
    for word in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
        if not word.get("text", "").strip() or float(word["conf"]) < 0:
            continue
        groups[(word["block_num"], word["par_num"], word["line_num"])].append(word)
    blocks = []
    width, height = image.size
    for words in groups.values():
        left = min(int(word["left"]) for word in words)
        top = min(int(word["top"]) for word in words)
        right = max(int(word["left"]) + int(word["width"]) for word in words)
        bottom = max(int(word["top"]) + int(word["height"]) for word in words)
        blocks.append({
            "text": normalize(" ".join(word["text"] for word in words)),
            "rect": [left / width, top / height, right / width, bottom / height],
            "confidence": round(sum(float(word["conf"]) for word in words) / len(words), 1),
            "method": "ocr",
            "words": [{"text": word["text"],
                       "rect": [int(word["left"]) / width, int(word["top"]) / height,
                                (int(word["left"]) + int(word["width"])) / width,
                                (int(word["top"]) + int(word["height"])) / height]} for word in words],
        })
    return blocks


def docx_blocks(path):
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find("w:body", namespace)
    blocks, paragraph, table = [], 0, 0
    for element in body:
        if element.tag.endswith("}p"):
            paragraph += 1
            text = "".join(node.text or "" for node in element.findall(".//w:t", namespace))
            if text.strip():
                blocks.append({"text": text, "locator": f"Абзац {paragraph}", "method": "docx", "confidence": 100})
        elif element.tag.endswith("}tbl"):
            table += 1
            for row_index, row in enumerate(element.findall("w:tr", namespace), 1):
                values = [normalize(" ".join(node.text or "" for node in cell.findall(".//w:t", namespace)))
                          for cell in row.findall("w:tc", namespace)]
                blocks.append({"text": " | ".join(values), "locator": f"Таблица {table}, строка {row_index}",
                               "method": "docx", "confidence": 100})
    return blocks


def cluster_positions(positions):
    groups = []
    for value in positions:
        if not groups or value > groups[-1][-1] + 2:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [round(sum(group) / len(group)) for group in groups]


def read_numeric_cell(image):
    candidates = []
    variants = [(None, 0), (140, 0), (None, 1), (140, 1)]
    for threshold, inset in variants:
        crop = image.crop((0, inset, image.width, image.height))
        if threshold is not None:
            crop = crop.point(lambda pixel: 255 if pixel > threshold else 0)
        crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.LANCZOS)
        crop = ImageOps.expand(crop, 20, fill="white")
        blocks = ocr_image(crop, language="eng", psm=7)
        raw = " ".join(block["text"] for block in blocks)
        clean = raw.strip(" ;:_«»~|")
        confidence = min((block["confidence"] for block in blocks), default=0)
        if re.fullmatch(r"[-−]?\d[\d \u00a0\u202f]*[,.]\d{2}", clean):
            candidates.append({"value": money(clean), "raw": raw, "confidence": confidence})
            if confidence >= 90 and threshold is None:
                return candidates[-1], candidates, False
    if not candidates:
        return {"value": None, "raw": raw, "confidence": confidence}, [], True
    votes = Counter(candidate["value"] for candidate in candidates)
    selected = max(candidates, key=lambda candidate: (votes[candidate["value"]], candidate["confidence"]))
    needs_review = selected["confidence"] < 70 or votes[selected["value"]] < 2
    if len(votes) > 1:
        needs_review = True
    return selected, candidates, needs_review


def read_report_grid(image, page_number):
    """Read the printed 17-column KФЗ form from detected grid lines, not fixed pixels."""
    grayscale = image.convert("L")
    mask = grayscale.point(lambda pixel: 255 if pixel < 155 else 0)
    width, height = image.size
    horizontal = cluster_positions([
        y for y in range(height)
        if mask.crop((0, y, width, y + 1)).histogram()[255] > width * 0.65
    ])
    if len(horizontal) < 6:
        return []
    # The lower edge of the multi-line header follows the tallest header interval.
    header_index = max(range(1, len(horizontal)), key=lambda index: horizontal[index] - horizontal[index - 1])
    header_bottom = horizontal[header_index]
    band_height = max(30, round(height * 0.04))
    vertical = cluster_positions([
        x for x in range(width)
        if mask.crop((x, header_bottom - band_height, x + 1, header_bottom - 2)).histogram()[255] > band_height * 0.75
    ])
    line = mask.crop((0, horizontal[0], width, horizontal[0] + 1))
    endpoints = line.getbbox()
    if endpoints and vertical and endpoints[2] - vertical[-1] > width * 0.025:
        vertical.append(endpoints[2] - 1)
    if len(vertical) != 18:
        return []
    fields = []
    for row_index in range(header_index + 1, len(horizontal) - 1):
        top, bottom = horizontal[row_index], horizontal[row_index + 1]
        if bottom - top < 8:
            continue
        label_crop = grayscale.crop((vertical[0] + 4, top + 2, vertical[2] - 4, bottom - 1))
        label_crop = ImageOps.expand(label_crop.resize((label_crop.width * 3, label_crop.height * 3)), 16, fill="white")
        label = " ".join(block["text"] for block in ocr_image(label_crop, psm=7))
        year = re.search(r"20\d{2}", label)
        if "всего" in label.lower():
            row_key, row_title = "all", "Всего за 3 этап"
        elif year:
            row_key = year.group()
            row_title = f"3 этап, {row_key} г."
        else:
            continue
        # The reference row belongs to a different stage.
        if re.match(r"\s*2\s*этап", label, re.I):
            continue
        for column, (code, title, unit) in REPORT_COLUMNS.items():
            left, right = vertical[column - 1], vertical[column]
            inset = max(3, int((right - left) * 0.07))

            def local_boundary(approximate):
                candidates = range(max(0, approximate - 7), min(height, approximate + 8))
                return max(candidates, key=lambda y: mask.crop((left + inset, y, right - inset, y + 1)).histogram()[255])

            local_top, local_bottom = local_boundary(top), local_boundary(bottom)
            box = (left + 4, local_top + 4, right - 4, local_bottom)
            crop = grayscale.crop(box)
            selected, candidates, needs_review = read_numeric_cell(crop)
            raw, value, confidence = selected["raw"], selected["value"], selected["confidence"]
            evidence = {
                "page": page_number, "rect": [box[0]/width, box[1]/height, box[2]/width, box[3]/height],
                "text": raw, "locator": f"{row_title}, графа {column}", "method": "ocr-cell",
                "confidence": confidence, "ocr_candidates": candidates,
            }
            fields.append({"id": f"r{row_key}_c{column}", "row": row_key, "row_title": row_title,
                           "column": column, "code": code, "title": title, "unit": unit,
                           "value": value, "raw": raw, "evidence": evidence,
                           "needs_review": needs_review})
    return fields


def extract_document(path, role="source"):
    path = Path(path)
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    document_id = hashlib.sha256((path.name + digest).encode()).hexdigest()[:16]
    document = {
        "id": document_id, "name": path.name, "hash": digest, "role": role,
        "format": path.suffix.lower()[1:], "size": len(content), "blocks": [], "cells": {},
        "facts": {}, "report_fields": [], "errors": [], "page_count": 1,
    }
    try:
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED:
            raise ValueError(f"Формат {suffix} пока не поддерживается")
        if suffix == ".pdf":
            with pymupdf.open(path) as pdf:
                if pdf.is_encrypted:
                    raise ValueError("PDF защищен паролем")
                if len(pdf) > 150:
                    raise ValueError("Один PDF может содержать до 150 страниц")
                document["page_count"] = len(pdf)
                from .edited_files import pdf_fields
                document["pdf_fields"] = pdf_fields(path)
                for page in pdf:
                    text = page.get_text().strip()
                    if len(text) >= 30:
                        blocks = []
                        for block in page.get_text("dict")["blocks"]:
                            for line in block.get("lines", []):
                                raw = normalize("".join(span["text"] for span in line["spans"]))
                                if raw:
                                    rect = line["bbox"]
                                    blocks.append({"text": raw, "rect": [rect[0]/page.rect.width, rect[1]/page.rect.height,
                                                                         rect[2]/page.rect.width, rect[3]/page.rect.height],
                                                   "method": "pdf-text", "confidence": 100})
                    else:
                        pixmap = page.get_pixmap(dpi=300)
                        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
                        blocks = ocr_image(image)
                        if role == "report":
                            document["report_fields"].extend(read_report_grid(image, page.number + 1))
                    for block in blocks:
                        block.update(page=page.number + 1, locator=f"Страница {page.number + 1}")
                    document["blocks"].extend(blocks)
        elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                document["blocks"] = [dict(block, page=1, locator="Изображение") for block in ocr_image(image)]
                if role == "report":
                    document["report_fields"] = read_report_grid(image, 1)
        elif suffix == ".docx":
            document["blocks"] = docx_blocks(path)
        elif suffix == ".xlsx":
            workbook = load_workbook(path, data_only=False)
            try:
                document["cells"] = read_workbook(workbook)
            finally:
                workbook.close()
            rows = defaultdict(list)
            for cell in document["cells"].values():
                rows[(cell["sheet"], cell["row"])].append(cell)
            for (sheet, row), cells in rows.items():
                document["blocks"].append({"text": " | ".join(cell["raw"] for cell in cells),
                                           "locator": f"{sheet}, строка {row}", "method": "xlsx", "confidence": 100})
        elif suffix == ".txt":
            document["blocks"] = [{"text": line, "locator": f"Строка {index}", "method": "text", "confidence": 100}
                                  for index, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1) if line.strip()]
        for index, block in enumerate(document["blocks"]):
            block["id"] = f"b{index}"
        document["text"] = "\n".join(block["text"] for block in document["blocks"])
        extract_facts(document)
        for index, field in enumerate(document.get("pdf_fields", [])):
            try:
                value=decimal_value(field["value"])
            except ValueError:
                continue
            form_fact=add_fact(document,f"pdf_form_{index}",field["name"],format(value,'f'),{"page":field["page"],"rect":field["rect"],"text":field["value"],"locator":field["name"],"pdf_field":field["name"],"method":"pdf-form","confidence":100})
            if re.search(r"amount|total|сумм|итого",field["name"],re.I) and document["facts"].get("amount",{}).get("value") is None:
                add_fact(document,"amount","Сумма документа",format(value,'f'),form_fact["evidence"],dependencies=[form_fact["id"]])
        if role == "report" and not document["report_fields"]:
            generic_report_fields(document)
        if not document["blocks"]:
            document["errors"].append("Не удалось извлечь текст")
    except Exception as exc:
        document["errors"].append(f"{type(exc).__name__}: {exc}")
        document.setdefault("text", "")
    return document


def add_fact(document, key, label, value, evidence, kind="money", dependencies=None, formula=None):
    identifier = f"{document['id']}:{key}"
    document["facts"][key] = {
        "id": identifier, "key": key, "label": label, "value": value, "kind": kind,
        "document_id": document["id"], "evidence": dict(evidence, document_id=document["id"]),
        "dependencies": dependencies or [], "formula": formula,
        "needs_review": value is None or evidence.get("confidence", 100) < 70,
    }
    return document["facts"][key]


def extract_facts(document):
    text = document["text"]
    blocks = document["blocks"]
    patterns = {
        "contract": ("Договор", r"(?<!\d)\d{2}/\d{2}/\d{2}-\d{2}(?!\d)"),
        "date": ("Дата документа", r"\b\d{2}\.\d{2}\.20\d{2}\b"),
        "number": ("Номер документа", r"(?:№\s*|Документ\s*:\s*|Служебная записка\s+|расходах ИТ\s+)([A-Za-zА-Яа-яЁё0-9]+(?:[-/][A-Za-zА-Яа-яЁё0-9]+)*)"),
        "contractor": ("Исполнитель / поставщик", r"(?:Исполнитель|Поставщик)\s*:?\s*(ООО[^\n|]+)"),
    }
    for key, (label, pattern) in patterns.items():
        found = []
        for block in blocks:
            match = re.search(pattern, block["text"], re.I)
            if match:
                value = normalize(match[1] if match.lastindex else match[0])
                if key == "date":
                    try:
                        value = datetime.strptime(value, "%d.%m.%Y").date().isoformat()
                    except ValueError:
                        continue
                found.append((value, block))
        if found:
            value, block = found[0]
            fact = add_fact(document, key, label, value, block, "text")
            if len({item[0] for item in found}) > 1 and key == "contract":
                fact["needs_review"] = True
                document["errors"].append("В документе встречаются разные номера договоров")
    if "number" not in document["facts"]:
        heading = next((block for block in blocks if "передаточный документ" in block["text"].lower()), None)
        if heading:
            match = re.search(r"документ\s+([A-Za-zА-Яа-я0-9/-]+)", heading["text"], re.I)
            if match:
                add_fact(document, "number", "Номер документа", match[1], heading, "text")
    for block in blocks:
        if "период" not in block["text"].lower():
            continue
        period = parse_month(block["text"])
        if period:
            add_fact(document, "period", "Период документа", period, block, "text")
            break
    if "date" not in document["facts"] and "period" in document["facts"]:
        period = document["facts"]["period"]
        add_fact(document, "date", "Дата по месяцу документа", period["value"] + "-01", period["evidence"], "text")

    if document["cells"]:
        extract_spreadsheet_facts(document)
    else:
        candidates = []
        for block in blocks:
            matches = find_money(block["text"])
            if matches:
                priority = bool(re.search(r"сумм|итого|к оплате|стоимост|расчетный период|руб", block["text"], re.I))
                candidates.append((priority, block, matches))
        totals = [item for item in candidates if item[0]] or candidates
        if totals:
            selected = totals[-1]
            if len(selected[2]) == 1:
                fact = add_fact(document, "amount", "Сумма документа", selected[2][0][1], selected[1])
                amounts = {match[1] for _, _, matches in totals for match in matches}
                if len(amounts) > 1:
                    fact["needs_review"] = True

    lowered = text.lower().replace("ё", "е")
    category, rationale = classify_document(lowered)
    document["category"] = category
    document["category_reason"] = rationale
    if document["role"] == "source":
        for key, label in (("contract", "Договор"), ("date", "Дата документа"),
                           ("number", "Номер документа"), ("amount", "Сумма документа")):
            if key not in document["facts"]:
                add_fact(document, key, label, None,
                         {"locator": "Реквизит не найден", "text": "", "method": "missing", "confidence": 0},
                         "money" if key == "amount" else "text")


def report_period(report):
    """Return an unambiguous range stated in the report's own text."""
    text = "\n".join(block["text"] for block in report.get("blocks", []))

    def parse(value):
        try:
            if "." in value:
                return datetime.strptime(value, "%d.%m.%Y").date()
            return date.fromisoformat(f"{parse_month(value)}-{int(value.split()[0]):02}")
        except ValueError:
            return None

    ranges = set()
    for start, end in REPORT_PERIOD.findall(text):
        ranges.add((parse(start), parse(end)))
    boundaries = defaultdict(lambda: defaultdict(set))
    for match in STAGE_BOUNDARY.finditer(text):
        scope = "period" if match["scope"].lower() != "этапа" else match["stage"] or "stage"
        key = "start" if match["label"].lower() == "начало" else "end"
        boundaries[scope][key].add(parse(match["date"]))
    for bounds in boundaries.values():
        if len(bounds["start"]) == len(bounds["end"]) == 1:
            ranges.add((next(iter(bounds["start"])), next(iter(bounds["end"]))))
        elif bounds["start"] and bounds["end"]:
            return {}
    if len(ranges) == 1:
        start, end = next(iter(ranges))
        if start and end and start <= end:
            return {"start": start.isoformat(), "end": end.isoformat()}
    return {}


def parse_month(text):
    year = re.search(r"20\d{2}", text)
    if not year:
        return None
    for pattern, month in MONTHS.items():
        if re.search(pattern, text, re.I):
            return f"{year.group()}-{month:02}"
    return None


def classify_document(text):
    if "расчетная ведомость" in text or "ведомость по оплате труда" in text:
        return "0200", "Расчетная ведомость по оплате труда, коды начислений 0201/0202"
    if "расчет страховых взносов" in text:
        return "0300", "Расчет начисленных страховых взносов, код статьи 0300"
    administrative = re.search(r"административ|общехозяйствен|организационно[\s-]*управлен|управления договором|организационное сопровождение", text)
    production = re.search(r"производствен|лаборатор|испытательн|калибровк|измерительн|специализированного.*оборудован", text)
    if administrative:
        return "0900", f"В содержании найден признак общехозяйственных расходов «{administrative[0]}»"
    if production:
        return "0800", f"В содержании найден признак производственных расходов «{production[0]}»"
    return None, "Содержание требует ручного сопоставления со статьей отчета"


def extract_spreadsheet_facts(document):
    rows = defaultdict(list)
    for key, cell in document["cells"].items():
        rows[(cell["sheet"], cell["row"])].append((key, cell))
        if cell["value"] is not None or cell["formula"]:
            evidence = {"locator": f"{cell['sheet']}!{cell['address']}", "sheet": cell["sheet"],
                        "cell": cell["address"], "text": cell["raw"], "method": "xlsx", "confidence": 100}
            fact = add_fact(document, key, evidence["locator"], cell["value"], evidence,
                            dependencies=[f"{document['id']}:{dependency}" for dependency in cell["dependencies"]],
                            formula=cell["formula"])
            if cell["error"]:
                fact["needs_review"] = True
                fact["error"] = cell["error"]
    text = document["text"].lower()
    if "ведомость" in text:
        for entries in rows.values():
            entries.sort(key=lambda entry: entry[1]["column"])
            strings = [cell["raw"] for _, cell in entries]
            code = next((value for value in strings if value in ("0201", "0202")), None)
            numeric = [(key, cell) for key, cell in entries if cell["value"] is not None]
            if code and numeric:
                key, cell = numeric[0]
                add_fact(document, "main" if code == "0201" else "additional_reserve",
                         "Основная зарплата" if code == "0201" else "Дополнительная зарплата и резерв",
                         cell["value"], document["facts"][key]["evidence"], dependencies=[f"{document['id']}:{key}"])
            if any(value.strip().upper() in ("ИТОГО", "ВСЕГО") for value in strings) and numeric:
                key, cell = numeric[-1]
                add_fact(document, "amount", "Итого по ведомости", cell["value"], document["facts"][key]["evidence"],
                         dependencies=[f"{document['id']}:{key}"])
        if "amount" not in document["facts"]:
            components = [document["facts"].get(key) for key in ("main", "additional_reserve")]
            if all(components):
                add_fact(document, "amount", "Сумма начислений", money(sum(decimal_value(item["value"]) for item in components)),
                         components[0]["evidence"], dependencies=[item["id"] for item in components], formula="main + additional_reserve")
    elif "страховых взносов" in text:
        for entries in rows.values():
            if not any(cell["raw"] == "0300" for _, cell in entries):
                continue
            numeric = sorted([(key, cell) for key, cell in entries if cell["value"] is not None], key=lambda item: item[1]["column"])
            if len(numeric) >= 2:
                for name, label, (key, cell) in zip(("base", "amount"), ("База взносов", "Начислено взносов"), numeric[:2]):
                    add_fact(document, name, label, cell["value"], document["facts"][key]["evidence"], dependencies=[f"{document['id']}:{key}"])


def generic_report_fields(document):
    for key, fact in document["facts"].items():
        if fact["kind"] != "money" or key in {"amount", "main", "additional_reserve", "base"}:
            continue
        document["report_fields"].append({"id": "cell_" + re.sub(r"\W", "_", key), "row": "custom", "row_title": "Показатели отчета",
                                          "code": key, "title": fact["label"], "unit": "RUB", "value": fact["value"],
                                          "raw": fact["evidence"]["text"], "evidence": fact["evidence"], "needs_review": fact["needs_review"]})
    if document["report_fields"]:
        return
    for index, block in enumerate(document["blocks"]):
        for match_index, (raw, value) in enumerate(find_money(block["text"])):
            document["report_fields"].append({"id": f"p{index}_{match_index}", "row": "custom", "row_title": "Показатели отчета",
                                              "code": f"P{index + 1}.{match_index + 1}", "title": block["text"][:100],
                                              "unit": "RUB", "value": value, "raw": raw, "evidence": block,
                                              "needs_review": block.get("confidence", 100) < 70})
