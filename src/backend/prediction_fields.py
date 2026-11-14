"""Adapt editor fields and parser graphs to the same financial metadata schema."""

import hashlib
import re

from .extraction import add_fact
from .formula_prediction import field_metadata


def structure_fields(document, model):
    result = []
    for page in model["pages"]:
        size = page.get("size", page.get("size_points"))
        nodes = page.get("nodes", [])
        heading = " ".join(node["label"] for node in nodes if node["kind"] == "text" and node["bbox"][1] < size[1] * .16)
        for node in nodes:
            if node["kind"] != "value" or node.get("value_type") != "decimal":
                continue
            rows, columns = node.get("row_path", []), node.get("column_path", [])
            label = " | ".join(rows) or node.get("metric", "")
            # Header units may be specified in the page caption.
            unit_text = " ".join([*columns, heading])
            unit = "млн руб." if re.search(r"млн", unit_text, re.I) else "тыс. руб." if re.search(r"тыс", unit_text, re.I) else "RUB"
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
