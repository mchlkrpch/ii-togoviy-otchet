"""Import public examples using document selection and target anchors only."""

import copy
import hashlib
import json
from pathlib import Path
import re
import shutil

from .extraction import extract_document
from .formula_prediction import field_metadata, normalized, row_code
from .ingest import now
from .prediction_fields import attach_structure, structure_fields


def available_sets(root):
    result = []
    for path in sorted(Path(root).glob("*/set.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        result.append({"id": item["id"], "name": item["тема"], "year": item["период_показателя"]})
    return result


def read_selection(root, identifier):
    root = Path(root).resolve()
    for path in root.glob("*/set.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item["id"] != identifier:
            continue
        documents = {document["id"]: document for document in [item["итоговый_документ"], *item["первичные_документы"]]}
        paths = {}
        for key, document in documents.items():
            source = (path.parent / document["локальный_файл"]).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError("Файл набора отсутствует или находится вне document_sets")
            paths[key] = source
        anchor = item["итоговая_ячейка"]
        # Operand annotations, numeric answers and formula are deliberately omitted.
        return {"id": item["id"], "name": item["тема"], "year": item["период_показателя"],
                "report": item["итоговый_документ"]["id"],
                "sources": [document["id"] for document in item["первичные_документы"]], "paths": paths,
                "target": {key: anchor[key] for key in ("страница_PDF", "строка_или_показатель", "столбец_или_блок", "единица")}}
    raise ValueError("Набор не найден")


def locate_target(document, model, selection):
    spec = selection["target"]
    page = next(page for page in model["pages"] if page["number"] == spec["страница_PDF"])
    fields = structure_fields(document, {"pages": [page]})
    code = row_code([spec["строка_или_показатель"]])
    column = spec["столбец_или_блок"]
    scenario = "actual" if "исполн" in normalized(column) else "plan" if "план" in normalized(column) else ""
    if code:
        matches = [field for field in fields if field["code"] == code and field["period"] == str(selection["year"])
                   and (not scenario or field["scenario"] == scenario)]
    else:
        monetary = [field for field in fields if "млн" in normalized(field["label"]) and "≥" not in field["label"] and "<" not in field["label"]]
        max_height = max((field["evidence"]["rect"][3] - field["evidence"]["rect"][1] for field in monetary), default=0)
        monetary = [field for field in monetary if field["evidence"]["rect"][3] - field["evidence"]["rect"][1] >= max_height * .75]
        planned = "план" in normalized(spec["строка_или_показатель"])
        if planned:
            captions = [node for node in page["nodes"] if node["kind"] == "text" and "планы на" in normalized(node["label"])]
            if len(captions) != 1:
                raise ValueError("Не удалось однозначно найти блок планового показателя")
            box = captions[0]["bbox"]
            center = [(box[0] + box[2]) / 2 / page["size"][0], box[1] / page["size"][1]]
            def distance(field):
                rect = field["evidence"]["rect"]
                return .1 * ((rect[0] + rect[2]) / 2 - center[0]) ** 2 + 8 * ((rect[1] + rect[3]) / 2 - center[1]) ** 2
            matches = sorted(monetary, key=distance)[:1]
        else:
            # A prominently printed monetary KPI precedes its explanatory caption.
            matches = sorted(monetary, key=lambda field: field["evidence"]["rect"][1])[:1]
    if len(matches) != 1:
        raise ValueError(f"Целевой показатель найден неоднозначно, кандидатов {len(matches)}")
    field = matches[0]
    metadata = field_metadata("goal", spec["строка_или_показатель"],
                              [spec["строка_или_показатель"]], [column], spec["единица"],
                              document_id=document["id"], source_ref=field["id"], period=str(selection["year"]),
                              scenario=scenario or ("plan" if "план" in normalized(spec["строка_или_показатель"]) else "actual" if not code else ""),
                              precision=len(field["value"].split(".")[1]) if "." in field["value"] else 0)
    # A numbered ЦК is a semantic scope, never a numeric operand.
    scope = re.search(r"ЦК\s*№\s*(\d+)", column, re.I)
    if scope:
        metadata["scope"] = scope[1]
    return {"id": "goal", "row": str(selection["year"]), "row_title": str(selection["year"]),
            "code": metadata["code"] or "goal", "title": spec["строка_или_показатель"],
            "unit": spec["единица"], "value": field["value"], "raw": field["evidence"]["original_text"],
            "evidence": field["evidence"], "needs_review": field.get("needs_review", False), "metadata": metadata}


def load_document(store, path):
    from .document_structure import baseline
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = store.root / "financial-cache" / f"{digest}.json"
    if cache.exists():
        payload = json.loads(cache.read_text())
        document, model = payload["document"], payload["model"]
        # Same PDF under a different filename retains case-local evidence IDs.
        if document["name"] == path.name:
            return copy.deepcopy(document), model
    document = extract_document(path, "source")
    model = baseline(store, document, path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    from parse_structure import atomic_json
    atomic_json(cache, {"document": document, "model": model})
    return document, model


def build_case(store, selection, case_id):
    directory = store.files_dir(case_id)
    directory.mkdir(parents=True, exist_ok=True)
    documents, identifiers = [], {}
    for identifier, source in selection["paths"].items():
        document, model = load_document(store, source)
        shutil.copy2(source, directory / source.name)
        document.update(filename=source.name, role="report" if identifier == selection["report"] else "source")
        attach_structure(document, model)
        if document["role"] == "report":
            document["report_fields"] = [locate_target(document, model, selection)]
        identifiers[identifier] = document["id"]
        documents.append(document)
    return {"id": case_id, "name": selection["id"] + " · " + selection["name"], "revision": 1,
            "created_at": now(), "report_id": identifiers[selection["report"]], "documents": documents,
            "prediction_document_ids": [identifiers[key] for key in selection["sources"]],
            "context": {"basis": "published_financial_tables", "contract": "", "start": f"{selection['year']}-01-01",
                        "end": f"{selection['year']}-12-31", "target_year": str(selection["year"]), "tolerance": "0.01"},
            "overrides": {}, "audit": [], "ignored_files": []}


def process_set(store, root, identifier, job):
    try:
        job.update(status="processing", progress=10, current_file=identifier)
        store.save_job(job)
        case = build_case(store, read_selection(root, identifier), job["case_id"])
        store.save_case(case)
        job.update(status="completed", progress=100, current_file=None)
    except Exception as exc:
        job.update(status="failed", error=str(exc), current_file=None)
    store.save_job(job)
