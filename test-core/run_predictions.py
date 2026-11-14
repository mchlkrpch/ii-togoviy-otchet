"""Run the document formula predictor from one or more JSON manifests."""

import argparse
import json
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import uuid4

from backend.document_sets import build_case
from backend.engine import calculate
from backend.extraction import SUPPORTED
from backend.ingest import is_reference_name, now, process_files
from backend.prediction_service import suggest
from backend.store import Store
from formula_report import evaluate, render
from parse_structure import atomic_json
from prepare_model import DEFAULT_PATH, FILES

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def load_json(path):
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def input_file(base, value):
    if not isinstance(value, str) or not value:
        raise ValueError("Укажите путь к файлу строкой")
    path = (base / value).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Ожидался файл: {path}")
    return path


def load_sets(manifests):
    sets = {}
    for manifest in manifests:
        manifest = manifest.resolve(strict=True)
        payload = load_json(manifest)
        entries = payload.get("sets") if isinstance(payload, dict) and "sets" in payload else payload
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{manifest}: ожидается набор, список наборов или объект с ключом sets")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"{manifest}: каждый набор должен быть объектом")
            identifier = entry.get("id")
            if not isinstance(identifier, str) or not identifier.strip() or identifier in sets:
                raise ValueError(f"{manifest}: задайте каждому набору уникальный id")
            report = input_file(manifest.parent, entry.get("report"))
            listed = entry.get("sources", [])
            if not isinstance(listed, list):
                raise ValueError(f"{identifier}: sources должен быть списком путей")
            sources = list(dict.fromkeys(input_file(manifest.parent, item) for item in listed))
            if any(path.suffix.lower() not in SUPPORTED or is_reference_name(path.name)
                   for path in [report, *sources]):
                raise ValueError(f"{identifier}: проверьте форматы и уберите файлы с эталонными ответами")
            mode = entry.get("mode", "manual")
            if mode not in ("manual", "published_pdf"):
                raise ValueError(f"{identifier}: допустимы mode manual и published_pdf")
            if mode == "published_pdf" and any(path.suffix.lower() != ".pdf" for path in [report, *sources]):
                raise ValueError(f"{identifier}: published_pdf принимает PDF")
            targets = entry.get("targets", [])
            if not isinstance(targets, list) or not all(isinstance(target, dict) and target for target in targets):
                raise ValueError(f"{identifier}: targets должен быть списком непустых объектов")
            if mode == "published_pdf" and (not targets or not all(
                    {"id", "page", "label", "column", "unit"} <= target.keys() for target in targets)):
                raise ValueError(f"{identifier}: для published_pdf задайте targets с id, page, label, column и unit")
            keys = [target.get("id", f"target-{index}") for index, target in enumerate(targets, 1)]
            if any(not isinstance(key, str) or not key.strip() for key in keys) or len(set(keys)) != len(keys):
                raise ValueError(f"{identifier}: id целей должны быть уникальны")
            paths = list(dict.fromkeys([report, *sources]))
            if len({path.name for path in paths}) != len(paths):
                raise ValueError(f"{identifier}: у разных входных файлов совпадают имена")
            context = entry.get("context", {})
            if not isinstance(context, dict) or set(context) - {"contract", "start", "end", "target_year", "tolerance"}:
                raise ValueError(f"{identifier}: неверные настройки context")
            year = str(entry.get("year", context.get("target_year", "")))
            if mode == "published_pdf" and (len(year) != 4 or not year.isascii() or not year.isdigit()):
                raise ValueError(f"{identifier}: для published_pdf задайте год year")
            sets[identifier] = dict(id=identifier, name=entry.get("name", identifier), mode=mode,
                                    report=report, sources=sources, targets=dict(zip(keys, targets)),
                                    context=context, year=year)
    return sets


def manual_case(store, entry):
    case_id, job_id = uuid4().hex, uuid4().hex
    directory = store.files_dir(case_id)
    directory.mkdir(parents=True)
    for path in dict.fromkeys([entry["report"], *entry["sources"]]):
        shutil.copy2(path, directory / path.name)
    store.save_job(dict(id=job_id, case_id=case_id, status="queued", progress=0, created_at=now()))
    process_files(store, job_id, case_id, entry["name"], entry["report"].name, entry["context"])
    job = store.get_job(job_id)
    if job["status"] != "completed":
        raise ValueError(job["error"])
    return store.get_case(case_id)


def published_case(store, entry, target):
    paths = {path.name: path for path in dict.fromkeys([entry["report"], *entry["sources"]])}
    selection = {
        "id": entry["id"], "name": entry["name"], "year": entry["year"],
        "report": entry["report"].name,
        "sources": [path.name for path in entry["sources"]] or [entry["report"].name],
        "paths": paths,
        "target": {"страница_PDF": target["page"], "строка_или_показатель": target["label"],
                   "столбец_или_блок": target["column"], "единица": target["unit"]},
    }
    return build_case(store, selection, uuid4().hex)


def find_check(checks, selector):
    def matches(node):
        values = {**node, "check_id": node["id"], "page": node["reported_evidence"].get("page"),
                  "label": node["title"], "locator": node["reported_evidence"].get("locator")}
        return all(str(values.get(key)) == str(value) for key, value in selector.items() if key != "id")

    selectors = set(selector) - {"id"}
    if not selectors or selectors - {"check_id", "row", "code", "page", "label", "locator", "column"}:
        raise ValueError(f"Уточните селектор поля: {selector}")
    matched = [node for node in checks if matches(node)]
    if len(matched) != 1:
        raise ValueError(f"По селектору {selector} найдено полей: {len(matched)}. Уточните координаты или check_id")
    return matched[0]["id"]


def attach_paths(inputs, paths):
    return [dict(item, file=str(paths[item["evidence"]["document_id"]]))
            if (item.get("evidence") or {}).get("document_id") in paths else item for item in inputs]


def parsed_reference_fields(case):
    fields = []
    for document in case["documents"]:
        for field in document.get("semantic_fields", []):
            evidence = field["evidence"]
            fields.append({"id": field["id"], "file": document["filename"], "value": field["value"],
                           "raw": evidence["original_text"], "value_type": "decimal",
                           "row_path": field["label"].split(" | "), "column_path": field["column"].split(" | "),
                           "source": {"page": evidence["page"], "bbox": evidence["rect"]}})
    return fields


def predict_target(store, entry, case, key, target, selector=None):
    context = calculate(case)
    node = context["nodes"][target]
    paths_by_name = {path.name: path for path in [entry["report"], *entry["sources"]]}
    paths = {document["id"]: paths_by_name[document["filename"]] for document in case["documents"]}
    response = suggest(case, store, target)
    for proposal in response["suggestions"]:
        proposal["inputs"] = attach_paths(proposal["inputs"], paths)
    return {
        "set": entry["id"], "name": entry["name"], "target_id": key, "check_id": target,
        "report": str(entry["report"]), "page": node["reported_evidence"].get("page"),
        "locator": node["reported_evidence"].get("locator"), "label": node["title"],
        "column": (selector or {}).get("column"), "unit": node["unit"], "reported": node["reported"],
        "abstention": response.get("abstention"), "alternatives": response["suggestions"],
        "warnings": response["warnings"],
        "baseline": {"rule": node["rule"], "expected": node["expected"],
                     "inputs": attach_paths(node["inputs"], paths)} if node["rule"] else None,
        "documents": {document["filename"]: document["hash"] for document in case["documents"]},
        "reference_fields": parsed_reference_fields(case) if entry["mode"] == "published_pdf" else [],
    }


def run_set(store, entry):
    if entry["mode"] == "published_pdf":
        for key, selector in entry["targets"].items():
            case = published_case(store, entry, selector)
            yield predict_target(store, entry, case, key, "goal", selector)
        return
    case = manual_case(store, entry)
    checks = [node for node in calculate(case)["checks"] if node["reported"] is not None]
    targets = {key: find_check(checks, selector) for key, selector in entry["targets"].items()}
    if not targets:
        targets = {node["id"]: node["id"] for node in checks}
    for key, target in targets.items():
        yield predict_target(store, entry, case, key, target)


def write_report(path, content):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", type=Path, nargs="+", help="JSON с наборами и путями к документам")
    parser.add_argument("--reference", type=Path, help="Отдельный JSON с эталоном для precision и recall")
    parser.add_argument("--output-dir", type=Path, default=RESULTS / "latest",
                        help="Подкаталог results внутри этой папки")
    args = parser.parse_args()
    try:
        output = args.output_dir.resolve()
        if not output.is_relative_to(RESULTS) or output == RESULTS:
            raise ValueError(f"Укажите подкаталог внутри {RESULTS}")
        sets = load_sets(args.manifests)
        inputs = {path for entry in sets.values() for path in [entry["report"], *entry["sources"]]}
        inputs.update(path.resolve() for path in args.manifests)
        if args.reference is not None:
            inputs.add(args.reference.resolve(strict=True))
        result_paths = [output / filename for filename in ("predictions.json", "metrics.json", "report.html")]
        if any(path.is_symlink() or path.resolve() in inputs for path in result_paths):
            raise ValueError("Выходной файл совпадает с входным или является символической ссылкой")
        model = Path(os.environ.get("AUDIT_MODEL_DIR", DEFAULT_PATH))
        if any(not (model / name).is_file() for name in FILES):
            raise ValueError("Отсутствуют файлы RuBERT. Выполните python prepare_model.py")
        with TemporaryDirectory(prefix="formula-reproduction-") as temp:
            store = Store(temp)
            predictions = []
            for entry in sets.values():
                print(f"Набор {entry['id']}", flush=True)
                try:
                    predictions.extend(run_set(store, entry))
                except Exception as exc:
                    raise ValueError(f"{entry['id']}: {exc}") from exc
        output.mkdir(parents=True, exist_ok=True)
        atomic_json(result_paths[0], [
            {key: value for key, value in row.items() if key != "reference_fields"} for row in predictions
        ])
        metrics = evaluate(predictions, args.reference.resolve()) if args.reference else {"groups": [], "rows": []}
        atomic_json(result_paths[1], metrics)
        write_report(result_paths[2], render(predictions, metrics, output))
    except (OSError, TypeError, ValueError, KeyError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")
    print(f"Отчёт: {result_paths[2]}")
    if metrics["groups"]:
        print(json.dumps(metrics["groups"][-1], ensure_ascii=False))


if __name__ == "__main__":
    main()
