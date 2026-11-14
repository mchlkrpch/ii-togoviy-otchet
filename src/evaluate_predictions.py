"""Reproduce metadata-only formula predictions, then evaluate against answer keys."""

import argparse
import copy
import json
from pathlib import Path
import statistics
import tempfile
from uuid import uuid4

from backend.document_sets import available_sets, build_case, read_selection
from backend.engine import calculate
from backend.ingest import prepare_demo, process_files
from backend.money import decimal_value
from backend.prediction_service import suggest
from backend.store import Store
from evaluate import evaluate
from batch_document_sets import match_reference
from prepare_model import REVISION
from backend.ingest import now


def reference_sources(case, truth, selection):
    resolved = []
    for reference in truth["исходные_ячейки"]:
        filename = selection["paths"][reference["документ"]].name
        document = next(document for document in case["documents"] if document["name"] == filename)
        facts = []
        for fact in document["facts"].values():
            metadata = fact.get("metadata")
            if metadata:
                facts.append({"id": fact["id"], "value": fact["value"], "raw": fact["evidence"].get("original_text", ""),
                    "value_type": "decimal", "row_path": metadata["label"].split(" | "),
                    "column_path": metadata["column"].split(" | "),
                    "source": {"page": fact["evidence"]["page"], "bbox": fact["evidence"]["rect"]}})
        graph = {"facts": facts, "pages": [{"number": number, "numeric_fields": []} for number in range(1, document["page_count"] + 1)]}
        match = match_reference(graph, reference)
        if match.get("fact_id"):
            resolved.append(match["fact_id"])
    return resolved


def run(inputs, sets, output, cache=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Cached parser graphs are an optional acceleration. They contain no answers.
    with tempfile.TemporaryDirectory(prefix="formula-baseline-") as directory:
        store = Store(directory)
        if cache:
            import shutil
            for name in ("structure-cache", "financial-cache"):
                source = Path(cache) / name
                if source.is_dir():
                    shutil.copytree(source, store.root / name, dirs_exist_ok=True)
        job, report_name = prepare_demo(store, inputs)
        process_files(store, job["id"], job["case_id"], "Baseline КФЗ", report_name)
        if store.get_job(job["id"])["status"] != "completed":
            raise ValueError(store.get_job(job["id"]).get("error"))
        case = store.get_case(job["case_id"])
        result = calculate(case)
        targets = [node for node in result["checks"] if node["row"] == result["context"]["target_year"]
                   and node["code"] in ("0200", "0300", "0800", "0900")]
        predictions = []
        edited = copy.deepcopy(case)
        for target in targets:
            response = suggest(case, store, target["id"])
            best = response["suggestions"][0] if response["suggestions"] else None
            predictions.append({"set": "КФЗ", "code": target["code"], **response})
            if best and not response["ambiguous"]:
                edited["overrides"]["check:" + target["id"]] = {"rule": best["rule"]}
        evaluated_case = calculate(edited)
        set_cases = []
        for entry in available_sets(sets):
            selection = read_selection(sets, entry["id"])
            external = build_case(store, selection, uuid4().hex)
            response = suggest(external, store, "goal")
            predictions.append({"set": entry["id"], **response})
            set_cases.append((entry["id"], external))
        # Save immutable predictions before reading any numeric reference values.
        prediction_path = output / "predictions.json"
        prediction_path.write_text(json.dumps(predictions, ensure_ascii=False, indent=2) + "\n")
        truth_paths = sorted(Path(inputs).glob("!*xlsx"))
        metrics = evaluate(evaluated_case, truth_paths[0]) if truth_paths else None
        external_metrics = []
        for identifier, external in set_cases:
            truth_path = next(path for path in Path(sets).glob("*/set.json") if json.loads(path.read_text())["id"] == identifier)
            truth = json.loads(truth_path.read_text())
            response = next(item for item in predictions if item["set"] == identifier)
            best = response["suggestions"][0] if response["suggestions"] else None
            target = calculate(external)["nodes"]["goal"]
            expected = decimal_value(truth["расчетный_результат"])
            # Parenthesized accounting expenses are negative in source PDFs.
            if decimal_value(target["reported"]) < 0:
                expected = -abs(expected)
            source_refs = [source["ref"] for source in best["rule"]["inputs"]] if best else []
            fields = {fact["id"]: fact for document in external["documents"] for fact in document["facts"].values()}
            annotated = reference_sources(external, truth, read_selection(sets, identifier))
            extras = set(source_refs) - set(annotated)
            correct_sources = len(annotated) == len(truth["исходные_ячейки"]) and set(annotated).issubset(source_refs) and all(decimal_value(fields[ref]["value"]) == 0 for ref in extras)
            external_metrics.append({"set": identifier, "reported": target["reported"],
                "expected": best["expected"] if best else None, "reference_expected": str(expected),
                "value_exact": bool(best and decimal_value(best["expected"]) == expected),
                "report_exact": abs(decimal_value(target["reported"])) == abs(decimal_value(truth["итоговая_ячейка"]["значение"])),
                "source_count": len(source_refs), "zero_source_count": sum(decimal_value(fields[ref]["value"]) == 0 for ref in source_refs),
                "sources_match": correct_sources, "reference_sources": annotated, "predicted_sources": source_refs,
                "elapsed_ms": response["elapsed_ms"], "ambiguous": response["ambiguous"]})
        latencies = sorted(item["elapsed_ms"] for item in predictions)
        all_cases = [case, *(external for _, external in set_cases)]
        input_hashes = {document["name"]: document["hash"] for item in all_cases for document in item["documents"]}
        summary = {"schema": "formula-baseline/1", "created_at": now(), "model": "cointegrated/rubert-tiny2",
            "model_revision": REVISION, "input_sha256": input_hashes,
            "ranking": "semantic embeddings and deterministic financial templates",
            "selection_inputs": "Documents and target anchor only. Source-cell annotations and reference formulas are read after predictions are saved.",
            "kfz": metrics, "document_sets": external_metrics,
            "document_sets_exact": sum(item["value_exact"] and item["report_exact"] for item in external_metrics),
            "document_sets_count": len(external_metrics),
            "document_sets_sources_match": sum(item["sources_match"] for item in external_metrics),
            "latency_ms": {"median": statistics.median(latencies), "p95": latencies[max(0, int(len(latencies) * .95) - 1)]},
            "limitations": ["Known document templates and explicit target anchors. This is a development-set baseline.",
                             "The parser classifies financial categories and table hierarchy with rules. RuBERT ranks metadata similarity.",
                             "Zero-valued child rows are retained. Arithmetic equality alone does not establish legal validity."]}
        (output / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if cache:
            for name in ("structure-cache", "financial-cache"):
                source = store.root / name
                if source.is_dir():
                    shutil.copytree(source, Path(cache) / name, dirs_exist_ok=True)
        return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=Path("../вводные"))
    parser.add_argument("--sets", type=Path, default=Path("../document_sets"))
    parser.add_argument("--output", type=Path, default=Path("../output/baseline"))
    parser.add_argument("--cache", type=Path, default=Path(".data"))
    args = parser.parse_args()
    run(args.inputs, args.sets, args.output, args.cache)
