"""Expose the existing PDF atlas graph inside the verification workbench."""

import json
import re
from collections import defaultdict
from threading import Lock

from structure_edits import EditError, apply_operation, field_text

from .store import RevisionConflict
from .structure_slices import apply_slice, prepare_slice


SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
CACHE_VERSION = 2
_extraction_lock = Lock()


def extract_model(path, cache_dir):
    from parse_structure import parse_image, parse_pdf
    from visualize_structure import build_model

    parser = parse_pdf if path.suffix.lower() == ".pdf" else parse_image
    model = build_model(parser(path, cache_dir=cache_dir))
    for page in model["pages"]:
        # Native PDF text can extend past the visible crop. Match the rendered page.
        def clip(box):
            return [max(0, min(value, page["size"][index % 2])) for index, value in enumerate(box)]

        for node in page["nodes"]:
            for name in ("bbox", "field_bbox", "key_bbox", "value_bbox", "row_bbox"):
                if name in node:
                    node[name] = clip(node[name])
            for name in ("column_header_bboxes", "row_header_bboxes"):
                if name in node:
                    node[name] = [clip(box) for box in node[name]]
        page["roots"].extend(node["id"] for node in page["nodes"] if not node["parent"] and node["kind"] == "text")
    return model


def baseline(store, document, path):
    from parse_structure import atomic_json

    digest = document["hash"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise EditError("Не удалось определить версию исходного документа")
    cache = store.root / "structure-cache"
    index = cache / f"{CACHE_VERSION}-{digest}.json"

    def load():
        try:
            return json.loads(index.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    model = load()
    if model is None:
        # Avoid launching duplicate OCR jobs or saturating the import and preview server.
        with _extraction_lock:
            model = load()
            if model is None:
                model = extract_model(path, cache / "ocr")
                if model["sha256"] != digest:
                    raise EditError("Исходный файл изменился. Импортируйте его повторно.")
                atomic_json(index, model)
    model["name"] = document["name"]
    return model


def read_state(store, case_id, document):
    with store.connect() as connection:
        row = connection.execute(
            "SELECT version, corrections FROM document_structure WHERE case_id=? AND document_id=? AND digest=?",
            (case_id, document["id"], document["hash"]),
        ).fetchone()
    return (row["version"], json.loads(row["corrections"])) if row else (0, [])


def response(version, model):
    pairs = defaultdict(list)
    for page in model["pages"]:
        for node in page["nodes"]:
            if node["kind"] == "key_value":
                pairs[node["key"]].append(node)
    model["key_values"] = {key: items[0]["value"] for key, items in pairs.items() if len(items) == 1}
    model["ambiguous_keys"] = {key: [item["id"] for item in items] for key, items in pairs.items() if len(items) > 1}
    return {"version": version, "model": model}


def get_structure(store, case_id, document, path):
    version, corrections = read_state(store, case_id, document)
    model = corrected_model(store, document, path, corrections, version)
    return response(version, model)


def corrected_model(store, document, path, corrections, version):
    model = baseline(store, document, path)
    for operation in corrections:
        if operation["action"] == "slice":
            apply_slice(model, operation)
        else:
            apply_operation(model, operation, version)
    return model


def update_structure(store, case_id, document, path, change):
    version, corrections = read_state(store, case_id, document)
    conflict = "Структура документа изменилась в другом окне. Откройте её снова."
    if version != change.version:
        raise RevisionConflict(conflict)
    model = corrected_model(store, document, path, corrections, version)
    page = next((page for page in model["pages"] if page["number"] == change.page), None)
    if page is None:
        raise EditError("Страница не найдена")
    node = next((node for node in page["nodes"] if node["id"] == change.node_id), None)
    if getattr(change, "action", None) == "slice":
        cache_dir = store.root / "structure-cache" / "ocr"
        operation = prepare_slice(path, page, node, change, version + 1, cache_dir)
        apply_slice(model, operation)
    else:
        text = change.text.strip()
        if text and node and node.get("field_bbox") and field_text(node) == text:
            return response(version, model)
        operation = {"action": "update", "page": change.page, "ids": [change.node_id], "text": text}
        apply_operation(model, operation, version + 1)
        # A slice consumes the field's then-current text and geometry. Coalesce only edits after the last slice.
        for index in range(len(corrections) - 1, -1, -1):
            previous = corrections[index]
            if previous["action"] == "slice":
                break
            if (previous["page"], previous["ids"]) == (change.page, [change.node_id]):
                del corrections[index]
                break
    corrections.append(operation)
    data = json.dumps(corrections, ensure_ascii=False, separators=(",", ":"))
    key = case_id, document["id"], document["hash"]
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not path.is_file():
            raise RevisionConflict("Исходный файл больше недоступен.")
        if version == 0:
            result = connection.execute(
                "INSERT OR IGNORE INTO document_structure(case_id, document_id, digest, version, corrections) "
                "VALUES(?, ?, ?, 1, ?)", (*key, data),
            )
        else:
            result = connection.execute(
                "UPDATE document_structure SET version=?, corrections=? WHERE case_id=? AND document_id=? "
                "AND digest=? AND version=?", (version + 1, data, *key, version),
            )
        if result.rowcount != 1:
            raise RevisionConflict(conflict)
    return response(version + 1, model)
