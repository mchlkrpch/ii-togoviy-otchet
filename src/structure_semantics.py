"""Deterministic, provenance-preserving keys and two-axis PDF facts.

Rules recognize explicit punctuation, geometric gaps, stage/year/month labels
and printed units. They do not infer values from accounting identities.
"""
import re
import unicodedata
from collections import defaultdict


def key(text):
    text = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[^\W_]+", text, re.UNICODE))


def bbox_union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def extract_pairs(blocks, page_number):
    fields = []
    for index, block in enumerate(blocks):
        text = block["text"]
        words = block.get("words", [])
        split = next((i for i, w in enumerate(words) if ":" in w["text"]), None)
        method = "colon"
        if ":" in text:
            label, value = text.split(":", 1)
            left = words[:split+1] if split is not None else []
            right = words[split+1:] if split is not None else []
        else:
            # A visibly separated label/value pair, e.g. Начало 3 этапа  16 августа.
            gaps = [(words[i+1]["bbox"][0]-words[i]["bbox"][2], i) for i in range(len(words)-1)]
            if not gaps:
                continue
            gap, split = max(gaps)
            height = sum(w["bbox"][3]-w["bbox"][1] for w in words)/len(words)
            if gap < max(3, height*0.9):
                continue
            left, right = words[:split+1], words[split+1:]
            label, value = " ".join(w["text"] for w in left), " ".join(w["text"] for w in right)
            method = "aligned_gap"
        label, value = label.strip(), value.strip()
        if not label or not value or not re.search(r"[А-Яа-яA-Za-z]", label):
            continue
        fields.append({"id": f"p{page_number}.kv{index+1}", "key": key(label), "label": label,
                       "block_index": index,
                       "value": value, "value_type": "string", "method": method,
                       "bbox": block["bbox"],
                       "key_bbox": bbox_union([w["bbox"] for w in left]) if left else block["bbox"],
                       "value_bbox": bbox_union([w["bbox"] for w in right]) if right else block["bbox"],
                       "source": {"page": page_number, "text": text},
                       "needs_review": block.get("confidence", 100) < 75})
    return fields


MONTHS = ["январ", "феврал", "март", "апрел", "май", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]


def row_context(path):
    text = " / ".join(path)
    stage = re.search(r"(?:(\d+)\s*этап|этап\s*(\d+))", text, re.I)
    years = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", text)))
    context = {"aggregation": "total" if re.search(r"\b(?:всего|итого)\b", text, re.I) else "detail",
               "reference": bool(re.search(r"справоч", text, re.I))}
    if stage:
        context["stage"] = int(stage[1] or stage[2])
    if len(years) == 1:
        context["year"] = int(years[0])
    elif years:
        context["year_candidates"] = [int(y) for y in years]
    normalized = key(text)
    for month, name in enumerate(MONTHS, 1):
        if any(token.startswith(name) for token in normalized.split()):
            context["month"] = month
    return context


def column_semantics(path):
    first = path[0] if path else ""
    metric = re.sub(r"\s*\([^)]*\)\s*", " ", first).strip(" :")
    units = re.findall(r"\(([^()]*)\)", " ".join(path))
    unit = next((u for u in units if re.search(r"человеко|чел[.-]|руб|час|процент|%|кг|штук", u, re.I)), None)
    return {"metric": metric, "metric_key": key(metric), "unit": unit}


def enrich_document(document):
    pairs, facts = [], []
    for page in document["pages"]:
        page["key_value_fields"] = extract_pairs(page["text_blocks"], page["number"])
        pairs.extend(page["key_value_fields"])
        for table in page["tables"]:
            cell_map = {c["id"]: c for c in table["cells"]}
            row_tree = []
            for row in table["rows"]:
                context = row_context(row["row_path"])
                row["context"] = context
                # Labels with explicit stage/year get a reusable horizontal hierarchy.
                path = []
                if "stage" in context:
                    path.append(f"{context['stage']} этап")
                if context["aggregation"] == "total":
                    path.append("Всего")
                elif "year" in context:
                    path.append(f"{context['year']} г.")
                    if "month" in context:
                        path.append(MONTHS[context["month"]-1])
                if context["reference"]:
                    path.append("Справочно")
                row["hierarchy_path"] = path or row["row_path"]
                field_values = defaultdict(list)
                branch = row_tree
                for label in row["hierarchy_path"]:
                    node = next((n for n in branch if n["label"] == label), None)
                    if node is None:
                        node = {"label": label, "children": [], "grid_rows": []}
                        branch.append(node)
                    if row["grid_row"] not in node["grid_rows"]:
                        node["grid_rows"].append(row["grid_row"])
                    branch = node["children"]
                for value in row["values"]:
                    column = table["columns"][value["column_number"]-1]
                    semantics = column_semantics(value["column_path"])
                    # JSON Pointer escaping gives a unique full-path key without
                    # conflating repeated leaf labels such as 'Всего'.
                    field_key = "/" + "/".join(s.replace("~", "~0").replace("/", "~1") for s in value["column_path"])
                    value["field_key"] = field_key
                    field_values[field_key].append(value["value"])
                    source = {"page": page["number"], "table_id": table["id"],
                              "cell_id": value["cell_id"], "bbox": cell_map[value["cell_id"]]["bbox"],
                              "column_header_cell_ids": column["header_cell_ids"],
                              "row_header_cell_ids": row["row_header_cell_ids"]}
                    fact = {"id": f"{table['id']}/{value['cell_id']}", "field_key": field_key, **semantics,
                            "row_context": context, "row_path": row["row_path"],
                            "row_hierarchy": row["hierarchy_path"], "column_path": value["column_path"],
                            "value": value["value"], "value_type": value["value_type"],
                            "raw": value["raw"], "needs_review": value["needs_review"], "source": source}
                    facts.append(fact)
                    value["fact_id"] = fact["id"]
                row["key_values"] = {k: v[0] for k, v in field_values.items() if len(v) == 1}
                row["ambiguous_field_keys"] = [k for k, v in field_values.items() if len(v) > 1]
            table["row_tree"] = row_tree
    grouped = defaultdict(list)
    for pair in pairs:
        grouped[pair["key"]].append(pair)
    document["key_values"] = {k: v[0]["value"] for k, v in grouped.items() if len(v) == 1}
    document["ambiguous_keys"] = {k: [v["id"] for v in values] for k, values in grouped.items() if len(values) > 1}
    document["key_value_fields"] = pairs
    document["facts"] = facts
    return document


def get_key_value(document, name):
    matches = [f for f in document["key_value_fields"] if f["key"] == key(name)]
    if len(matches) != 1:
        raise LookupError(f"Expected one key/value field, found {len(matches)}: {name}")
    return matches[0]


def find_facts(document, *, metric=None, stage=None, year=None, month=None, aggregation=None,
               reference=None, unit=None, query=None, column_path=None):
    """Exact normalized filters plus optional deterministic token-prefix search.

    Query supports phrases such as 'трудоемкость за 3 этап 2026'. This is
    a rule-based Russian stage/year grammar, not a universal language model.
    All query terms must match; ties remain multiple candidates.
    """
    tokens = []
    if query:
        context = row_context([query])
        for name, explicit in (("stage", stage), ("year", year), ("month", month)):
            if explicit is not None and name in context and context[name] != explicit:
                return []
        stage = stage if stage is not None else context.get("stage")
        year = year if year is not None else context.get("year")
        month = month if month is not None else context.get("month")
        cleaned = re.sub(r"\b\d+\s*этап\w*|этап\s*\d+|\b(?:19|20)\d{2}\b", " ", query, flags=re.I)
        stop = {"за", "в", "на", "по", "г", "год", "году", "значение", "показатель"}
        tokens = [t for t in key(cleaned).split() if t not in stop]
    matches = []
    for fact in document["facts"]:
        context = fact["row_context"]
        if any(expected is not None and context.get(name) != expected for name, expected in
               (("stage", stage), ("year", year), ("month", month), ("aggregation", aggregation), ("reference", reference))):
            continue
        if metric is not None and key(metric) != fact["metric_key"]:
            continue
        if unit is not None and key(unit) != key(fact["unit"] or ""):
            continue
        if column_path is not None and [key(p) for p in column_path] != [key(p) for p in fact["column_path"]]:
            continue
        haystack = key(" ".join([*fact["column_path"], *fact["row_path"], fact["unit"] or "", fact["raw"]])).split()
        if any(not any(t == h or len(t) >= 4 and h.startswith(t) for h in haystack) for t in tokens):
            continue
        matches.append(fact)
    return matches


def get_fact(document, **filters):
    matches = find_facts(document, **filters)
    if len(matches) != 1:
        raise LookupError(f"Expected one fact, found {len(matches)}: {filters}")
    return matches[0]
