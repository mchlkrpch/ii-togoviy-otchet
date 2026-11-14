"""Read-only formula proposals for the existing editor and saved projects."""

import copy
import hashlib
import time

from .engine import calculate, validate_change
from .formula_prediction import predict
from .prediction_fields import attach_structure, case_fields, target_field


def suggest(case, store, target, document_ids=None):
    started = time.perf_counter()
    original_result = calculate(case)
    node = original_result["nodes"].get(target)
    if not node or node["kind"] != "check":
        raise ValueError("Выберите показатель отчета")
    candidate = copy.deepcopy(case)
    # Existing parsers expose PDF, Word and Excel fields. PDF structure is lazy.
    for document in candidate["documents"]:
        if document.get("format") not in ("pdf", "png", "jpg", "jpeg", "tif", "tiff") or document.get("semantic_fields"):
            continue
        if document_ids and document["id"] not in document_ids and document["id"] != node["reported_evidence"].get("document_id"):
            continue
        if document.get("category") and document.get("facts", {}).get("amount", {}).get("value") is not None:
            continue
        from .document_structure import get_structure
        path = store.files_dir(case["id"]) / document["filename"]
        if path.is_file() and not document["errors"]:
            attach_structure(document, get_structure(store, case["id"], document, path)["model"])
    result = calculate(candidate)
    report = next(document for document in result["documents"] if document["id"] == node["reported_evidence"].get("document_id", result["report_id"]))
    goal = target_field(node, report)
    fields = case_fields(result)
    selected_documents = document_ids or case.get("prediction_document_ids")
    if selected_documents:
        fields = [field for field in fields if field["document_id"] in selected_documents]
    else:
        fields = [field for field in fields if field["document_id"] != report["id"]]
    response = predict(goal, fields)
    warnings = []
    suggestions = []
    for proposal in response["suggestions"]:
        variables = []
        sources = {field["id"]: field for field in fields}
        for item in proposal["rule"]["inputs"]:
            if item["ref"] not in original_result["nodes"]:
                field = sources[item["ref"]]
                key = "manual_" + hashlib.sha256(field["id"].encode()).hexdigest()[:32]
                evidence = dict(field["evidence"], text=field["value"])
                variable = {"key": key, "document_id": field["document_id"], "value": field["value"], "evidence": evidence}
                variables.append(variable)
                item["ref"] = field["document_id"] + ":" + key
        try:
            preview_case = validate_change(case, "check", target, {"rule": proposal["rule"], "variables": variables})
            preview = calculate(preview_case)["nodes"][target]
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        proposal.update(variables=variables, expected=preview["expected"], delta=preview["delta"],
                        status=preview["status"], warnings=preview.get("coverage_issues", []),
                        inputs=preview["inputs"])
        suggestions.append(proposal)
    response.update(suggestions=suggestions, target=target, revision=case["revision"],
                    elapsed_ms=round((time.perf_counter() - started) * 1000), warnings=warnings,
                    fields_considered=len(fields))
    if not suggestions and not response.get("abstention"):
        response["abstention"] = "Предложения не прошли проверку ссылок и зависимостей"
    return response
