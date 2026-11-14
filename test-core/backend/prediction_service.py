"""Read-only formula proposals for the existing editor and saved projects."""

import copy
import hashlib
import time

from .engine import calculate, formula_rule, validate_change
from .formula_prediction import predict
from .money import evaluate_formula, money
from .prediction_fields import attach_structure, case_fields, report_sum_refs, target_field


def suggest(case, store, target, document_ids=None):
    started = time.perf_counter()
    original_result = calculate(case)
    node = original_result["nodes"].get(target)
    if not node or node["kind"] != "check":
        raise ValueError("Выберите показатель отчета")
    candidate = copy.deepcopy(case)
    report_id = node["reported_evidence"].get("document_id", case["report_id"])
    structured_target = node["reported_evidence"].get("method") == "structured-field"
    selected_documents = document_ids or case.get("prediction_document_ids")
    report_model = None
    # Existing parsers expose PDF, Word and Excel fields. PDF structure is lazy.
    for document in candidate["documents"]:
        if document.get("format") not in ("pdf", "png", "jpg", "jpeg", "tif", "tiff"):
            continue
        is_target = document["id"] == report_id
        if document["role"] == "reference" or (selected_documents and document["id"] not in selected_documents and not is_target):
            continue
        typed_source = document.get("category") and "amount" in document.get("facts", {})
        if not is_target and (document.get("semantic_fields") or (typed_source and not structured_target)):
            continue
        from .document_structure import get_structure
        path = store.files_dir(case["id"]) / document["filename"]
        if path.is_file() and not document["errors"]:
            model = get_structure(store, case["id"], document, path)["model"]
            if is_target:
                report_model = model
            elif document["role"] == "source":
                attach_structure(document, model)
    result = calculate(candidate)
    report = next(document for document in result["documents"] if document["id"] == report_id)
    goal = target_field(node, report)
    fields = case_fields(result)
    if report.get("structured_report"):
        fields = [field for field in fields if field["document_id"] != report_id or
                  field["evidence"]["method"] == "structured-field" and field["column"] != "Числовой показатель текста"]
        if not selected_documents and not any(document["role"] == "source" and document["included"]
                                              for document in result["documents"]):
            selected_documents = [report_id]
    if selected_documents:
        fields = [field for field in fields if field["document_id"] in selected_documents]
    else:
        fields = [field for field in fields if field["document_id"] != report["id"]]
    refs = report_sum_refs(node, result["checks"], report_model) if report_model and (
        not document_ids or report_id in document_ids
    ) else []
    response = predict(goal, fields)
    if refs:
        proposal = {"rule": formula_rule(refs), "operation": "sum", "structure": True,
                    "reason": "Сумма связанных полей по заголовкам и структуре итогового отчета."}
        # Prefer independent primary evidence when it supplies the same subtotal.
        if not response["suggestions"]:
            response["suggestions"].append(proposal)
            response.update(abstention=None, model="Структура отчета")
        else:
            response["suggestions"].append(proposal)
    warnings = []
    suggestions = []
    for proposal in response["suggestions"]:
        variables = []
        sources = {field["id"]: field for field in fields}
        for item in proposal["rule"]["inputs"]:
            if item["ref"] not in original_result["nodes"]:
                field = sources[item["ref"]]
                key = "manual_" + hashlib.sha256(f"{target}\0{field['id']}".encode()).hexdigest()[:32]
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
        if proposal.get("structure") and preview["expected"] is None:
            reported = {item["name"]: result["nodes"][item["ref"]]["reported"] for item in proposal["inputs"]}
            try:
                proposal["reported_preview"] = money(evaluate_formula(proposal["rule"]["expression"], reported)[0])
            except ValueError:
                pass
        suggestions.append(proposal)
    response.update(suggestions=suggestions, target=target, revision=case["revision"],
                    elapsed_ms=round((time.perf_counter() - started) * 1000), warnings=warnings,
                    fields_considered=len(fields))
    if not suggestions and not response.get("abstention"):
        response["abstention"] = "Предложения не прошли проверку ссылок и зависимостей"
    return response
