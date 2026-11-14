"""Adapt editor fields and parser graphs to the same financial metadata schema."""

import hashlib
import re

from .extraction import add_fact
from .formula_prediction import field_metadata, operation_text


def structure_fields(document, model):
    result = []
    inherited_unit = document.get("default_unit", "RUB")
    for page in model["pages"]:
        size = page.get("size", page.get("size_points"))
        nodes = page.get("nodes", [])
        heading = " ".join(node["label"] for node in nodes if node["bbox"][1] < size[1] * .12
                           and re.search(r"план|отч[её]т|тыс|млн", node["label"], re.I))
        units = " ".join(node["label"] for node in nodes if re.search(r"единиц.*измерени|тыс\.?\s*руб", node["label"], re.I))
        if re.search(r"тыс", units, re.I):
            inherited_unit = "тыс. руб."
        for node in nodes:
            if node["kind"] != "value" or node.get("value_type") != "decimal":
                continue
            rows, columns = node.get("row_path", []), node.get("column_path", [])
            label = " | ".join(rows) or node.get("metric", "")
            if not label:
                continue
            # Header units may be specified in the page caption.
            unit_text = " ".join([*columns, heading])
            unit = "млн руб." if re.search(r"млн", unit_text, re.I) else "тыс. руб." if re.search(r"тыс", unit_text, re.I) else inherited_unit
            rect = [coordinate / size[index % 2] for index, coordinate in enumerate(node["field_bbox"])]
            key = "semantic_" + hashlib.sha256(node["id"].encode()).hexdigest()[:24]
            evidence = {"document_id": document["id"], "page": page["number"], "rect": rect,
                        "text": node["label"], "original_text": node.get("raw", node["label"]),
                        "locator": f"Страница {page['number']} · {node['id']}",
                        "method": "structured-field", "confidence": node.get("confidence", 100) or 100}
            field = field_metadata(f"{document['id']}:{key}", label, rows, columns, unit,
                                   document_id=document["id"], value=node["label"], evidence=evidence,
                                   page_heading=heading, accounting_negative="(" in node.get("raw", ""),
                                   needs_review=node.get("needs_review", False), key=key)
            result.append(field)
    return result


def attach_structure(document, model):
    fields = structure_fields(document, model)
    for field in fields:
        fact = add_fact(document, field["key"], field["label"], field["value"], field["evidence"])
        fact.update(metadata={key: value for key, value in field.items() if key not in ("value", "evidence")},
                    needs_review=field["needs_review"])
    document["semantic_fields"] = fields
    return fields


def narrative_metadata(field, page, size):
    """Identify money in running text and use an adjacent caption when present."""
    context = field["label"]
    raw = re.escape(field["evidence"]["original_text"])
    amount = re.search(rf"(?<![\d.,]){raw}\s*(?P<scale>млн\.?|тыс\.?)?\s*(?P<currency>руб\.?|₽)?(?=\W|$)", context, re.I)
    if not amount or not (amount["scale"] or amount["currency"]):
        return None
    scale = (amount["scale"] or "").rstrip(".").lower()
    width, height = size
    left, top, right, bottom = [value * size[index % 2] for index, value in enumerate(field["evidence"]["rect"])]
    text = sorted((node for node in page["nodes"] if node["kind"] == "text"), key=lambda node: node["bbox"][1])
    line_height = bottom - top
    caption = []
    if re.fullmatch(r"[\s\d,.+-]*(?:тыс|млн)?\.?\s*(?:руб\.?|₽)?\s*", context, re.I):
        plan_labels = [node for node in text if re.fullmatch(r"планы на", node["label"], re.I)
                       and node["bbox"][0] > right and abs(node["bbox"][1] - top) <= line_height]
        for plan in plan_labels:
            x, y, _, y1 = plan["bbox"]
            years = [node["label"] for node in text if re.fullmatch(r"20\d{2}\s*год", node["label"], re.I)
                     and abs(node["bbox"][1] - y) <= line_height and node["bbox"][3] <= y1 + line_height
                     and abs(node["bbox"][2] - x) <= line_height]
            if len(years) == 1:
                caption = [plan["label"], years[0]]
                break
        if not caption:
            for node in text:
                x0, y0, x1, y1 = node["bbox"]
                if y0 < top + line_height / 2 or x1 < left or x0 > right or x1 - x0 > width * .36:
                    continue
                if y0 > bottom + line_height * .65:
                    break
                line = node["label"]
                if re.match(r"\s*[*†]", line):
                    break
                bottom = max(bottom, y1)
                if re.search(r"[А-Яа-яЁё]{2}", line) and not re.fullmatch(r"\s*руб\.?\s*", line, re.I):
                    caption.append(re.sub(r"^[^А-Яа-яЁё]+", "", line))
    label = " ".join(caption) or context
    years = set(re.findall(r"\b20\d{2}\b", label))
    period = years.pop() if len(years) == 1 else ""
    scenario = "plan" if re.search(r"\bплан\w*", label, re.I) else "actual" if re.search(r"\bнаправлен\w*", label, re.I) else ""
    # Some reports print the capital number as a badge split over two lines.
    heading = " ".join(node["label"] for node in text if node["bbox"][1] < height * .12)
    scopes = set(re.findall(r"\bЦК\s*№\s*(\d+)", heading, re.I))
    scope = scopes.pop() if len(scopes) == 1 and scenario and period else ""
    return dict(field, label=label, unit={"млн": "млн руб.", "тыс": "тыс. руб."}.get(scale, "RUB"),
                code="", period=period, scenario=scenario, scope=scope)


def structured_report_fields(document, model):
    checks = []
    pages = {page["number"]: page for page in model["pages"]}
    for field in attach_structure(document, model):
        evidence = field["evidence"]
        page = pages[evidence["page"]]
        if field["column"] == "Числовой показатель текста":
            field = narrative_metadata(field, page, page["size"])
            if field is None:
                continue
        metadata = dict(field, source_ref=field["id"], precision=len(field["value"].partition(".")[2]))
        checks.append({"id": field["key"], "row": field["period"] or "custom",
                       "row_title": f"Страница {evidence['page']}", "code": field["code"] or f"Стр. {evidence['page']}",
                       "title": field["label"], "unit": field["unit"], "value": field["value"],
                       "raw": evidence["original_text"], "evidence": evidence,
                       "needs_review": field["needs_review"], "metadata": metadata})
    return checks


def case_fields(result):
    documents = {document["id"]: document for document in result["documents"]}
    fields = []
    for node in result["nodes"].values():
        if node["kind"] != "fact":
            continue
        document = documents[node["document_id"]]
        if document["role"] not in ("source", "report") or document["role"] == "source" and not document["included"]:
            continue
        period = document["facts"].get("period", {}).get("value") or document["facts"].get("date", {}).get("value", "")
        metadata = node.get("metadata")
        field = dict(metadata) if metadata else field_metadata(node["id"], node["label"], unit="RUB",
            document_id=document["id"], period=period or "", fact_key=node["key"], category=document.get("category"),
            contract=document["facts"].get("contract", {}).get("value"))
        if node["key"] == "amount" and not document.get("cells"):
            field["operation_text"] = operation_text(document["blocks"])
        field.update(id=node["id"], value=node.get("expected"), evidence=node["evidence"], available=node.get("expected") is not None)
        fields.append(field)
    return fields


def target_field(node, document):
    # Imported structural report fields retain their complete metadata.
    original = next((field for field in document.get("report_fields", []) if field["id"] in (node["id"], node["id"].split("::")[-1])), {})
    if original.get("metadata"):
        field = dict(original["metadata"])
    else:
        field = field_metadata(node["id"], node["title"], unit=node.get("unit", "RUB"),
                               code=node.get("code", ""), period=node.get("row", ""),
                               document_id=document["id"], contract=document["facts"].get("contract", {}).get("value"))
    field.update(id=node["id"], value=node.get("reported"), evidence=node["reported_evidence"])
    return field


def report_sum_refs(target, checks, model):
    """Find direct children of a total using report geometry and parsed header paths."""
    evidence = target["reported_evidence"]
    page = next((page for page in model["pages"] if page["number"] == evidence.get("page", 1)), None)
    if not page:
        return []
    width, height = page["size"]
    cells = [node for node in page["nodes"] if node["kind"] == "value" and node.get("column_path")]
    bindings = {}
    for check in checks:
        source = check["reported_evidence"]
        if source.get("document_id") != evidence.get("document_id") or source.get("page", 1) != page["number"]:
            continue
        rect = source.get("rect")
        if not rect or not check.get("column"):
            continue
        x, y = (rect[0] + rect[2]) * width / 2, (rect[1] + rect[3]) * height / 2
        for cell in cells:
            left, top, right, bottom = cell["field_bbox"]
            if cell["column"] == check["column"] and left <= x <= right and top <= y <= bottom:
                bindings[check["id"]] = cell
                break
    selected = bindings.pop(target["id"], None)
    if not selected:
        return []
    table = selected["id"].split("/")[0]
    peers = [(ref, cell) for ref, cell in bindings.items() if cell["id"].split("/")[0] == table]
    scope = selected.get("row_context", {})
    if scope.get("aggregation") == "total" and scope.get("stage") is not None:
        years = {}
        for ref, cell in peers:
            row = cell.get("row_context", {})
            if (cell["column"] == selected["column"] and row.get("stage") == scope["stage"]
                    and row.get("aggregation") == "detail" and not row.get("reference")
                    and row.get("year") and not row.get("month")):
                years.setdefault(row["year"], []).append(ref)
        if len(years) >= 2 and all(len(refs) == 1 for refs in years.values()):
            return [years[year][0] for year in sorted(years)]
    columns = {cell["column"]: (ref, cell) for ref, cell in peers if cell["row_path"] == selected["row_path"]}
    label = selected["column_path"][-1]
    sum_columns = re.search(r"(\d+)\s*\+\s*[^\d]{0,8}(\d+)", label)
    if sum_columns:
        indices = tuple(map(int, sum_columns.groups()))
        if indices[0] != indices[1] and all(index in columns for index in indices):
            return [columns[index][0] for index in indices]
    if label.strip().lower() in {"всего", "итого"} and len(selected["column_path"]) > 1:
        siblings = [ref for _, (ref, cell) in sorted(columns.items())
                    if cell["column_path"][:-1] == selected["column_path"][:-1]
                    and cell["column_path"][-1].strip().lower() not in {"всего", "итого"}]
        if len(siblings) >= 2:
            return siblings
    return []
