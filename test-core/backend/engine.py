import copy
import calendar
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from openpyxl import Workbook

from .excel import read_workbook
from .extraction import report_period
from .money import decimal_value, evaluate_formula, formula_references, money


STATUS_LABELS = {"verified": "Подтверждено", "mismatch": "Потенциальное расхождение", "unverified": "Недостаточно данных",
                 "review": "Требует проверки", "excluded": "Исключено", "error": "Ошибка обработки"}


def fact_value(document, key):
    return document.get("facts", {}).get(key, {}).get("value")


def effective_documents(case):
    documents = copy.deepcopy(case["documents"])
    overrides = case.get("overrides", {})
    multiple_reports = sum(document["role"] == "report" for document in documents) > 1
    for document in documents:
        snapshot = overrides.get("workbook:" + document["id"], {}).get("snapshot")
        if snapshot:
            from .spreadsheet_editor import apply_snapshot
            apply_snapshot(document, snapshot)
        rich_snapshot = overrides.get("rich_document:" + document["id"], {}).get("snapshot")
        rich_changes = set()
        if rich_snapshot:
            from .rich_document import apply_snapshot as apply_rich_snapshot
            rich_changes = apply_rich_snapshot(document, rich_snapshot)
        from .content_edits import apply_content_edits, variable_still_present
        changed_cells, changed_locators = apply_content_edits(document, overrides)
        changed_locators.update(rich_changes)
        for variable in case.get("variables", {}).values():
            if variable["document_id"] == document["id"]:
                from .extraction import add_fact
                evidence = copy.deepcopy(variable["evidence"])
                origin = evidence.get("audit_origin") or f"{evidence.get('sheet')}!{evidence.get('cell')}"
                moved = document.get("cell_origins", {}).get(origin)
                if moved:
                    moved_sheet, moved_cell = moved.rsplit("!", 1)
                    evidence.update(sheet=moved_sheet, cell=moved_cell, locator=moved)
                source = next((fact for fact in document["facts"].values() if fact["key"] != variable["key"] and ((evidence.get("pdf_field") and fact["evidence"].get("pdf_field") == evidence["pdf_field"]) or (evidence.get("cell") and fact["evidence"].get("cell") == evidence["cell"] and fact["evidence"].get("sheet") == evidence.get("sheet")))), None)
                add_fact(document, variable["key"], "Выбранное число", variable["value"], evidence, "money", dependencies=[source["id"]] if source else [])
                document["facts"][variable["key"]].update(manual=True, needs_review=False, selection_alias=bool(source))
                if not variable_still_present(variable, document, changed_locators):
                    document["facts"][variable["key"]].update(value=None, needs_review=True)
        document.update(overrides.get("document:" + document["id"], {}))
        for key, fact in document["facts"].items():
            change = overrides.get("fact:" + fact["id"])
            if change:
                fact["original_value"] = fact["value"]
                fact.update(change, needs_review=False, manual=True)
                if fact["evidence"].get("pdf_field"):
                    for field in document.get("pdf_fields", []):
                        if field["name"] == fact["evidence"]["pdf_field"]:
                            field.update(original_value=field["value"],value=change["value"],manual=True)
                if key in document["cells"]:
                    document["cells"][key]["raw"] = str(change["value"])
                    changed_cells = True
        if changed_cells:
            workbook = Workbook()
            workbook.remove(workbook.active)
            for cell in document["cells"].values():
                sheet = workbook[cell["sheet"]] if cell["sheet"] in workbook.sheetnames else workbook.create_sheet(cell["sheet"])
                raw = cell["raw"]
                if not raw.startswith("="):
                    try:
                        raw = decimal_value(raw)
                    except ValueError:
                        pass
                sheet[cell["address"]] = raw
            recalculated = read_workbook(workbook)
            workbook.close()
            document["cells"] = recalculated
            for key, cell in recalculated.items():
                if key in document["facts"]:
                    document["facts"][key].update(value=cell["value"], error=cell["error"], needs_review=bool(cell["error"]), formula=cell["formula"], dependencies=[document["id"]+":"+ref for ref in cell["dependencies"]])
                elif cell["value"] is not None:
                    from .extraction import add_fact
                    add_fact(document, key, cell["address"], cell["value"], {"sheet":cell["sheet"],"cell":cell["address"],"locator":key,"text":cell["raw"],"method":"xlsx","confidence":100}, dependencies=[document["id"]+":"+ref for ref in cell["dependencies"]], formula=cell["formula"])
        for field in document["report_fields"]:
            if multiple_reports and document["role"] == "report":
                field["id"] = document["id"] + "::" + field["id"]
            cell_key = f"{field['evidence'].get('sheet')}!{field['evidence'].get('cell')}"
            if changed_cells and cell_key in document["cells"]:
                field["original_value"] = field["value"]
                field.update(value=document["cells"][cell_key]["value"], needs_review=bool(document["cells"][cell_key]["error"]), manual=True)
            change = overrides.get("reported:" + field["id"])
            if change:
                field["original_value"] = field["value"]
                field.update(value=change["value"], needs_review=False, manual=True, reported_override=True)
                document["edited"] = True
        if document.get("format") in ("docx", "txt"):
            from .money import find_money
            for fact in document["facts"].values():
                if not fact.get("manual") or fact.get("original_value") is None or fact.get("value") is None or fact["kind"] != "money":
                    continue
                block = next((block for block in document["blocks"] if block.get("locator") == fact["evidence"].get("locator")), None)
                if block:
                    matches = [raw for raw,value in find_money(block["text"]) if decimal_value(value) == decimal_value(fact["original_value"])]
                    if len(matches) == 1:
                        block.setdefault("original_text", block["text"])
                        block.update(text=block["text"].replace(matches[0], display_amount(fact["value"]), 1), manual=True)
    return documents


def document_decisions(documents, context):
    seen_hash, seen_operations = {}, {}
    issues = []
    ordered = sorted(documents, key=lambda document: ("копия" in document["name"].lower() or "(1)" in document["name"],
                                                       len(document["name"]), document["name"]))
    for document in ordered:
        reasons, warnings = [], []
        duplicate = None
        if document["hash"] in seen_hash:
            duplicate = seen_hash[document["hash"]]
        else:
            seen_hash[document["hash"]] = document["id"]
        number, contract = fact_value(document, "number"), fact_value(document, "contract")
        operation = tuple(fact_value(document, key) for key in ("number", "contract", "date", "amount", "contractor"))
        if document["role"] == "source" and all(operation):
            if operation in seen_operations:
                duplicate = seen_operations[operation]
            else:
                seen_operations[operation] = document["id"]
        if duplicate:
            reasons.append("duplicate")
            document["duplicate_of"] = duplicate
        if document["role"] != "source":
            reasons.append("report" if document["role"] == "report" else "reference")
        elif document["errors"]:
            reasons.append("extraction_error")
        if document["role"] == "source":
            published_tables = context.get("basis") == "published_financial_tables"
            if not context.get("contract") and not published_tables:
                warnings.append("missing_context")
            if not contract and not published_tables:
                warnings.append("missing_contract")
            elif context.get("contract") and contract != context["contract"]:
                reasons.append("wrong_contract")
            document_date = fact_value(document, "date")
            if not document_date and not published_tables:
                warnings.append("missing_date")
            elif document_date and context.get("start") and context.get("end") and not published_tables:
                period = fact_value(document, "period")
                if period:
                    year, month = map(int, period.split("-"))
                    period_start = f"{year}-{month:02}-01"
                    period_end = f"{year}-{month:02}-{calendar.monthrange(year, month)[1]}"
                    if period_end < context["start"] or period_start > context["end"]:
                        reasons.append("outside_period")
                    elif period_start < context["start"] or period_end > context["end"]:
                        warnings.append("partial_period")
                elif not context["start"] <= document_date <= context["end"]:
                    reasons.append("outside_period")
            elif not published_tables:
                warnings.append("missing_context")
            if not document.get("category") and not published_tables:
                warnings.append("unmapped_category")
            if fact_value(document, "amount") is None and not published_tables:
                warnings.append("missing_amount")
            for key in ("contract", "date", "amount"):
                if document["facts"].get(key, {}).get("needs_review") and not published_tables:
                    warnings.append("uncertain_extraction")
        included = not reasons
        if "include" in document:
            included = document["include"]
            document["manual_inclusion"] = True
        document["included"] = included
        document["decision_reasons"] = reasons
        document["warnings"] = sorted(set(warnings))
        document["state"] = "review" if included and warnings else "included" if included else "excluded"
        if document.get("reviewed"):
            document["state"] = "included" if included else "excluded"
        labels = {
            "duplicate": "Повторный документ", "wrong_contract": "Другой договор",
            "outside_period": "Документ вне периода", "extraction_error": "Ошибка извлечения",
            "missing_contract": "Не найден номер договора", "missing_date": "Не найдена дата",
            "unmapped_category": "Не определена статья", "missing_amount": "Не найдена сумма",
            "uncertain_extraction": "OCR или реквизиты требуют проверки", "missing_context": "Не задан период проверки",
            "partial_period": "Месяц документа частично выходит за период проверки",
        }
        for reason in reasons + warnings:
            if reason in labels:
                issues.append({"id": f"{document['id']}:{reason}", "type": reason, "title": labels[reason],
                               "document_id": document["id"], "document_name": document["name"],
                               "severity": "warning" if included else "info", "included": included,
                               "duplicate_of": duplicate})
    return issues


def source_rules(documents, year=None):
    mapping = defaultdict(list)
    for document in documents:
        if not document["included"]:
            continue
        document_period = fact_value(document, "period") or fact_value(document, "date")
        if year and document_period and not document_period.startswith(year):
            continue
        category = document.get("category")
        if category and "amount" in document["facts"]:
            mapping[category].append(document["facts"]["amount"]["id"])
        if category == "0200":
            for key, code in (("main", "0200.main"), ("additional_reserve", "0200.extra")):
                if key in document["facts"]:
                    mapping[code].append(document["facts"][key]["id"])
    return mapping


def formula_rule(references):
    return {"expression": " + ".join(f"x{index + 1}" for index in range(len(references))),
            "inputs": [{"name": f"x{index + 1}", "ref": reference} for index, reference in enumerate(references)]}


def display_amount(value):
    return format(decimal_value(value), ",.2f").replace(",", " ").replace(".", ",")


def scope_issues(context):
    issues = []
    if not context.get("contract") and context.get("basis") != "published_financial_tables":
        issues.append("Не указан договор")
    if not context.get("start") or not context.get("end"):
        issues.append("Не задан период проверки. В сумму могут попасть документы других месяцев")
    return issues


def create_checks(report, documents, context, namespace=""):
    checks, by_row_code = {}, {}
    target_year = context.get("target_year", context.get("end", "")[:4])
    mapping = source_rules(documents, target_year)
    required_months = set()
    if context.get("start") and context.get("end"):
        start, end = date.fromisoformat(context["start"]), date.fromisoformat(context["end"])
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            required_months.add(f"{year}-{month:02}")
            month += 1
            if month == 13:
                year, month = year + 1, 1
            if len(required_months) > 1200:
                raise ValueError("Период проверки превышает 100 лет")
    for field in report["report_fields"]:
        identifier = field["id"]
        evidence = dict(field["evidence"], document_id=report["id"])
        check = {
            "id": identifier, "kind": "check", "code": field["code"], "title": field["title"],
            "row": field["row"], "row_title": field["row_title"], "column": field.get("column"), "unit": field["unit"],
            "reported": field["value"], "original_reported": field.get("original_value", field["value"]),
            "reported_evidence": evidence, "reported_needs_review": field["needs_review"],
            "rule": None, "structure": [], "structural_groups": [], "category": "missing_evidence",
            "note": "Для показателя не найдено отдельного подтверждения в первичных документах.",
            "manual": field.get("manual", False), "coverage_issues": [],
            "source_ref": field.get("metadata", {}).get("source_ref"),
        }
        row_mapping = source_rules(documents, field["row"]) if re.fullmatch(r"20\d{2}", field["row"]) else {}
        if field["code"] in row_mapping:
            check["rule"] = formula_rule(row_mapping[field["code"]])
            check["note"] = "Сумма документов той же статьи, договора и периода после исключения дублей."
            category = field["code"].split(".")[0]
            if category in ("0200", "0300"):
                supplied = {fact_value(document, "period") for document in documents
                            if document["included"] and document.get("category") == category}
                missing = sorted({month for month in required_months if month.startswith(field["row"])} - supplied)
                if missing:
                    check["coverage_issues"].append("Не найдены месяцы: " + ", ".join(missing))
                if not required_months:
                    check["coverage_issues"].append("Не задан полный период проверки")
            damaged = [document["name"] for document in documents
                       if document["role"] == "source" and document["errors"]
                       and document.get("category") in (None, category)]
            if damaged:
                check["coverage_issues"].append("Не извлечены возможные источники: " + ", ".join(damaged))
        checks[identifier] = check
        by_row_code[(field["row"], field["code"])] = identifier
    relationships = {"0200": ["0200.salary", "0200.reserve"],
                     "0200.salary": ["0200.main", "0200.additional"],
                     "0300": ["0300.salary", "0300.reserve"],
                     "0300.salary": ["0300.main", "0300.additional"],
                     "total": ["0100", "0200", "0300", "0800", "0900"]}
    rows = {field["row"] for field in report["report_fields"]}
    for row in rows:
        for code, dependencies in relationships.items():
            identifier = by_row_code.get((row, code))
            if identifier and all((row, dependency) in by_row_code for dependency in dependencies):
                references = [by_row_code[(row, dependency)] for dependency in dependencies]
                checks[identifier]["structure"].extend(references)
                checks[identifier]["structural_groups"].append({"label": "Сумма граф отчета", "refs": references})
        if row == "all":
            years = sorted(value for value in rows if re.fullmatch(r"20\d{2}", value))
            for (row_key, code), identifier in by_row_code.items():
                if row_key != "all":
                    continue
                references = [by_row_code[(year, code)] for year in years if (year, code) in by_row_code]
                if references:
                    checks[identifier]["structure"].extend(references)
                    checks[identifier]["structural_groups"].append({"label": "Сумма годов этапа", "refs": references})
                    checks[identifier]["rule"] = formula_rule(references)
                    checks[identifier]["note"] = "Сумма подтвержденных значений годовых строк. Сомнения в каждой строке передаются в итог этапа."
        total_id = by_row_code.get((row, "total"))
        if total_id and re.fullmatch(r"20\d{2}", row):
            references = [by_row_code[(row, code)] for code in ("0200", "0300", "0800", "0900") if (row, code) in by_row_code]
            if len(references) == 4:
                checks[total_id]["rule"] = formula_rule(references)
                checks[total_id]["note"] = "Подтвержденные суммы по статьям 0200, 0300, 0800 и 0900. Материальные затраты проверяются отдельно."

    extra_parts = [by_row_code.get((target_year, code)) for code in ("0200.additional", "0200.reserve")]
    if all(extra_parts) and mapping.get("0200.extra"):
        parts = [checks[identifier] for identifier in extra_parts]
        reported = money(sum(decimal_value(part["reported"]) for part in parts)) if all(part["reported"] is not None for part in parts) else None
        checks[namespace + "additional_reserve"] = {
            "id": namespace + "additional_reserve", "kind": "check", "code": "0200.extra", "title": "Дополнительная зарплата + резерв",
            "row": target_year, "row_title": f"3 этап, {target_year} г.", "unit": "RUB", "reported": reported,
            "original_reported": reported, "reported_evidence": parts[0]["reported_evidence"],
            "reported_parts": extra_parts, "reported_needs_review": any(part["reported_needs_review"] for part in parts),
            "rule": formula_rule(mapping["0200.extra"]), "structure": [], "structural_groups": [],
            "note": "В ведомостях эти начисления объединены. Можно подтвердить их сумму, отдельные составляющие требуют документов.",
            "category": "aggregate_match",
            "coverage_issues": checks.get(by_row_code.get((target_year, "0200")), {}).get("coverage_issues", []),
        }
    payroll_by_period = defaultdict(list)
    for document in documents:
        if document["included"] and document.get("category") == "0200" and "amount" in document["facts"]:
            payroll_by_period[fact_value(document, "period")].append(document["facts"]["amount"]["id"])
    for document in documents:
        period = fact_value(document, "period")
        if not document["included"] or document.get("category") != "0300" or "base" not in document["facts"] or period not in payroll_by_period:
            continue
        base = document["facts"]["base"]
        identifier = namespace + "base_" + document["id"]
        checks[identifier] = {
            "id": identifier, "kind": "check", "code": f"BASE.{period}", "title": f"База страховых взносов · {period}",
            "row": "primary", "row_title": "Сверка первичных документов", "unit": "RUB",
            "reported": base["value"], "original_reported": base["value"], "reported_fact": base["id"],
            "reported_evidence": base["evidence"], "reported_needs_review": base["needs_review"],
            "rule": formula_rule(payroll_by_period[period]), "structure": [], "structural_groups": [],
            "note": "Сопоставление базы начислений в расчете взносов с итогом ведомости зарплаты за тот же месяц. Тариф взносов не предполагается.",
            "category": "cross_document",
        }
    return checks


def calculate(case):
    context = dict(case["context"], **case.get("overrides", {}).get("context:settings", {}))
    documents = effective_documents(case)
    report = next((document for document in documents if document["id"] == case["report_id"]), None)
    if report is None:
        raise ValueError("Итоговый отчет не найден")
    if not context.get("start") or not context.get("end"):
        inferred = report_period(report)
        start = context.get("start") or inferred.get("start")
        end = context.get("end") or inferred.get("end")
        if start and end and start <= end:
            context.update(start=start, end=end)
    context_issues = scope_issues(context)
    issues = document_decisions(documents, context)
    reports = [report] + [document for document in documents if document["role"] == "report" and document["id"] != report["id"]]
    checks = {}
    for item in reports:
        checks.update(create_checks(item, documents, context, item["id"] + "::" if len(reports) > 1 else ""))
    overrides = case.get("overrides", {})
    for identifier, check in checks.items():
        change = overrides.get("check:" + identifier)
        if change:
            check["rule"] = change["rule"]
            check["rule_manual"] = True
            check["note"] = "Формула выбрана пользователем. Ссылки и арифметика проверены при сохранении."
        if context.get("basis") == "published_financial_tables":
            check["note"] += " Источники — опубликованные финансовые таблицы. Проверяется согласованность показателей."
    facts, document_map = {}, {document["id"]: document for document in documents}
    for document in documents:
        for fact in document["facts"].values():
            if fact["kind"] == "money":
                facts[fact["id"]] = dict(fact, kind="fact")
    nodes = {**facts, **checks}
    visiting, resolved = set(), set()

    def resolve(identifier):
        if identifier not in nodes:
            raise ValueError(f"Источник {identifier} не найден")
        if identifier in visiting:
            raise ValueError(f"Циклическая зависимость у {identifier}")
        node = nodes[identifier]
        if identifier in resolved:
            return node
        visiting.add(identifier)
        dependencies = []
        if node["kind"] == "fact":
            dependencies = [resolve(reference) for reference in node.get("dependencies", [])]
            if len(dependencies) == 1 and (not node.get("manual") or node.get("selection_alias")) and not node.get("formula"):
                node["value"] = dependencies[0].get("expected")
            node["expected"] = node["value"]
            document = document_map[node["document_id"]]
            doubtful = (node["needs_review"] and not document.get("reviewed")) or node["value"] is None or any(dependency["status"] != "verified" for dependency in dependencies)
            if document["state"] == "review" and not document.get("reviewed"):
                doubtful = True
            node["status"] = "review" if doubtful else "verified"
            if not document["included"] and document["role"] != "report":
                node["status"] = "excluded"
            node["direct_status"] = node["status"]
            node["inputs"] = [{"name": f"x{index + 1}", "ref": dependency["id"], "value": dependency["expected"], "label": dependency.get("label", "")}
                              for index, dependency in enumerate(dependencies)]
            node["blocked_by"] = [dependency["id"] for dependency in dependencies if dependency["status"] != "verified"]
        else:
            rule = node["rule"]
            inputs, input_nodes, formula_error, expected = [], [], None, None
            if rule:
                for item in rule["inputs"]:
                    source = resolve(item["ref"])
                    input_nodes.append(source)
                    inputs.append(dict(item, value=source.get("expected") if source["status"] != "excluded" else None,
                                       label=source.get("title", source.get("label", "")),
                                       evidence=source.get("evidence", source.get("reported_evidence"))))
                try:
                    value, references = evaluate_formula(rule["expression"], {item["name"]: item["value"] for item in inputs})
                    unused = {item["name"] for item in inputs} - set(references)
                    if unused:
                        raise ValueError("Формула не использует все выбранные источники")
                    expected = money(value)
                except ValueError as exc:
                    formula_error = str(exc)
            node.update(expected=expected, inputs=inputs, formula_error=formula_error)
            node["delta"] = money(decimal_value(node["reported"]) - decimal_value(expected)) if node["reported"] is not None and expected is not None else None
            tolerance = decimal_value(context.get("tolerance", "0.01"))
            if node["delta"] is None:
                numeric_status = "unverified"
            else:
                numeric_status = "verified" if abs(decimal_value(node["delta"])) <= tolerance else "mismatch"
            node["numeric_status"] = numeric_status
            if node.get("coverage_issues"):
                direct = "unverified"
            else:
                direct = numeric_status
            structure_nodes = [resolve(reference) for reference in node["structure"]]
            blocked = [source["id"] for source in input_nodes + structure_nodes if source["status"] != "verified"]
            node["arithmetic"] = []
            for group in node["structural_groups"]:
                parts = [nodes[reference] for reference in group["refs"]]
                if all(part["reported"] is not None for part in parts) and node["reported"] is not None:
                    value = money(sum(decimal_value(part["reported"]) for part in parts))
                    delta = money(decimal_value(node["reported"]) - decimal_value(value))
                    state = "verified" if abs(decimal_value(delta)) <= tolerance else "mismatch"
                    if node["reported_needs_review"] or any(part["reported_needs_review"] for part in parts):
                        state = "review"
                else:
                    value, delta, state = None, None, "unverified"
                node["arithmetic"].append(dict(group, expected=value, delta=delta, status=state))
            arithmetic_mismatch = any(item["status"] == "mismatch" for item in node["arithmetic"])
            uncertain_inputs = any(source["status"] in ("review", "unverified", "excluded") for source in input_nodes)
            if direct == "mismatch" and (node["reported_needs_review"] or uncertain_inputs):
                status = "review"
            elif direct == "mismatch" or arithmetic_mismatch:
                status = "mismatch"
            elif direct == "verified" and (blocked or node["reported_needs_review"]):
                status = "review"
            else:
                status = direct
            node.update(direct_status=direct, status=status, blocked_by=list(dict.fromkeys(blocked)))
            node["explanation"] = []
            if numeric_status == "verified":
                node["explanation"].append("Значение отчета и сумма выбранных источников численно совпадают.")
            elif numeric_status == "mismatch":
                direction = "выше" if decimal_value(node["delta"]) > 0 else "ниже"
                node["explanation"].append(
                    f"В отчете {display_amount(node['reported'])}, по источникам {display_amount(expected)}. "
                    f"Отчет {direction} на {display_amount(abs(decimal_value(node['delta'])))}."
                )
            else:
                node["explanation"].append("Ожидаемое значение пока не установлено по первичным документам.")
                if any(source["status"] == "excluded" for source in input_nodes):
                    node["explanation"].append("Формула ссылается на исключённые документы. Проверьте источники формулы.")
            node["explanation"].extend(context_issues)
            node["explanation"].extend(node.get("coverage_issues", []))
            if blocked and not context_issues:
                labels = [nodes[reference].get("title", nodes[reference].get("label", reference)) for reference in dict.fromkeys(blocked)]
                node["explanation"].append("Требуют проверки исходные значения: " + "; ".join(labels) + ".")
            if node["reported_needs_review"]:
                node["explanation"].append("Число в отчете требует визуальной сверки с оригиналом после OCR.")
            node["substitution"] = None
            if rule and all(item["value"] is not None for item in inputs):
                values = {item["name"]: display_amount(item["value"]) for item in inputs}
                node["substitution"] = re.sub(r"\bx[1-9]\d*\b", lambda match: values[match[0]], rule["expression"])
            node["category"] = "amount_mismatch" if status == "mismatch" and direct == "mismatch" else "arithmetic_mismatch" if arithmetic_mismatch else "verified" if status == "verified" else "missing_evidence" if direct == "unverified" else "uncertain_link"
            dependencies = input_nodes + structure_nodes
        node["dependencies"] = list(dict.fromkeys(source["id"] for source in dependencies))
        visiting.remove(identifier)
        resolved.add(identifier)
        return node

    for identifier in nodes:
        resolve(identifier)
    for document in documents:
        for fact in document["facts"].values():
            if fact["id"] in nodes:
                fact["value"] = nodes[fact["id"]]["expected"]
    for node in nodes.values():
        node["dependents"] = []
    for identifier, node in nodes.items():
        for reference in node["dependencies"]:
            nodes[reference]["dependents"].append(identifier)
    target_checks = [check for check in checks.values() if check["row"] == context.get("target_year")]
    counts = Counter(check["status"] for check in checks.values())
    direct_counts = Counter(check["direct_status"] for check in target_checks)
    numeric_counts = Counter(check["numeric_status"] for check in target_checks)
    findings = [check["id"] for check in target_checks if check["numeric_status"] == "mismatch" and check["code"] != "total"]
    findings.sort(key=lambda identifier: checks[identifier]["status"] != "mismatch")
    total = next((check for check in target_checks if check["code"] == "total"), None)
    mismatch_count = sum(check["status"] == "mismatch" for check in target_checks)
    unknown_count = sum(check["status"] in ("review", "unverified") for check in target_checks)
    conclusion = f"Проверено {len(checks)} показателей."
    if context_issues:
        conclusion += " Проверка предварительная. " + ". ".join(context_issues) + "."
    if mismatch_count:
        conclusion += f" Потенциальных расхождений в целевой строке: {mismatch_count}."
    if total and total["delta"] is not None:
        conclusion += f" Разница итога и суммы документов в расчете составляет {total['delta']} руб."
    if unknown_count:
        conclusion += f" Требуют уточнения или дополнительных документов: {unknown_count}."
    return {
        "id": case["id"], "name": case["name"], "revision": case["revision"],
        "created_at": case["created_at"], "context": context, "report_id": report["id"],
        "report_ids": [item["id"] for item in reports],
        "documents": documents, "nodes": nodes, "checks": list(checks.values()), "issues": issues,
        "variables": list(case.get("variables", {}).values()),
        "audit": case.get("audit", []), "summary": {
            "counts": dict(counts), "target_counts": dict(direct_counts), "numeric_counts": dict(numeric_counts),
            "scope_issues": context_issues, "context_complete": not context_issues,
            "findings": findings, "check_count": len(checks),
            "source_count": sum(document["role"] == "source" for document in documents),
            "included_count": sum(document["included"] for document in documents),
            "total_reported": total["reported"] if total else None,
            "total_expected": total["expected"] if total else None,
            "total_delta": total["delta"] if total else None,
            "conclusion": conclusion,
        },
    }


def validate_change(case, kind, target, values):
    case = copy.deepcopy(case)
    values = copy.deepcopy(values)
    if kind == "check" and "variables" in values:
        variables = values.pop("variables")
        if not isinstance(variables, list) or len(variables) > 100:
            raise ValueError("Слишком много новых переменных")
        current = calculate(case)
        documents = {document["id"]: document for document in current["documents"]}
        for variable in variables:
            if not isinstance(variable, dict) or set(variable) != {"key", "document_id", "value", "evidence"}:
                raise ValueError("Некорректная переменная")
            key = variable["key"]
            document = documents.get(variable["document_id"])
            if not isinstance(key, str) or not re.fullmatch(r"manual_[a-f0-9]{32}", key):
                raise ValueError("Некорректный идентификатор переменной")
            if not document or not (document["role"] == "source" and document["included"] or document["role"] == "report" and current["context"].get("basis") == "published_financial_tables"):
                raise ValueError("Переменная должна ссылаться на включенный первичный документ")
            evidence = variable["evidence"]
            if not isinstance(evidence, dict) or evidence.get("document_id") != document["id"]:
                raise ValueError("Не указан документ переменной")
            value = decimal_value(variable["value"])
            if decimal_value(evidence.get("text", "")) != value:
                raise ValueError("Выделите одно число без названия поля")
            if evidence.get("cell"):
                cell = document["cells"].get(f"{evidence.get('sheet')}!{evidence['cell']}")
                if not cell or cell["value"] is None or cell.get("formula"):
                    raise ValueError("Для ячейки Excel используйте ее существующую переменную")
                if decimal_value(cell["value"]) != value:
                    raise ValueError("Значение не совпадает с ячейкой Excel")
            elif document.get("format") == "docx":
                if evidence.get("locator") not in {block.get("locator") for block in document["blocks"]}:
                    raise ValueError("Фрагмент Word не найден")
            else:
                page, rect = evidence.get("page"), evidence.get("rect")
                if type(page) is not int or not 1 <= page <= document.get("page_count", 1):
                    raise ValueError("Страница переменной отсутствует")
                if not isinstance(rect, list) or len(rect) != 4 or not all(type(item) in (int, float) and 0 <= item <= 1 for item in rect) or rect[0] >= rect[2] or rect[1] >= rect[3]:
                    raise ValueError("Укажите область выделенного числа")
            identifier = document["id"] + ":" + key
            stored = dict(variable, value=format(value, "f"), evidence=dict(evidence, method="manual-selection", confidence=100), id=identifier, check_id=target)
            previous = case.setdefault("variables", {}).get(identifier)
            if previous and previous != stored:
                raise ValueError("Сохраненную переменную нельзя незаметно перепривязать")
            case["variables"][identifier] = stored
        if variables:
            rule = values.get("rule")
            if not isinstance(rule, dict) or not isinstance(rule.get("inputs"), list):
                raise ValueError("Укажите формулу новой переменной")
            references = {item.get("ref") for item in rule["inputs"] if isinstance(item, dict)}
            if not {item["document_id"] + ":" + item["key"] for item in variables}.issubset(references):
                raise ValueError("Новые переменные должны использоваться в формуле")
    result = calculate(case)
    if kind == "context":
        allowed = {"contract", "start", "end", "target_year", "tolerance"}
        if target != "settings" or set(values) - allowed:
            raise ValueError("Неизвестная настройка проверки")
        merged = dict(result["context"], **values)
        for field in ("start", "end"):
            date.fromisoformat(merged[field])
        if merged["start"] > merged["end"]:
            raise ValueError("Начало периода должно предшествовать окончанию")
        if decimal_value(merged.get("tolerance", "0.01")) < 0:
            raise ValueError("Допуск должен быть неотрицательным")
    elif kind == "document":
        if target not in {document["id"] for document in result["documents"]} or set(values) - {"include", "category", "reviewed"}:
            raise ValueError("Неизвестный документ или свойство")
        if "category" in values and values["category"] not in (None, "0100", "0200", "0300", "0800", "0900"):
            raise ValueError("Неизвестная статья")
        for key in ("include", "reviewed"):
            if key in values and type(values[key]) is not bool:
                raise ValueError(f"{key} должно быть логическим значением")
    elif kind == "rich_document":
        document = next((document for document in result["documents"] if document["id"] == target), None)
        if not document or document.get("format") != "docx" or set(values) != {"snapshot"}:
            raise ValueError("Выберите документ Word")
        from .rich_document import normalize_snapshot
        values["snapshot"] = normalize_snapshot(values["snapshot"])
        for key in list(case.get("overrides", {})):
            if key.startswith(f"text:{target}:"):
                del case["overrides"][key]
            elif key.startswith(f"fact:{target}:"):
                fact_key = key[len(f"fact:{target}:"):]
                fact = document["facts"].get(fact_key)
                if fact and fact.get("kind") == "money" and fact["evidence"].get("locator"):
                    del case["overrides"][key]
    elif kind == "workbook":
        document = next((document for document in result["documents"] if document["id"] == target), None)
        if not document or document.get("format") != "xlsx" or set(values) != {"snapshot"}:
            raise ValueError("Выберите книгу Excel")
        from .spreadsheet_editor import normalize_snapshot
        values["snapshot"] = normalize_snapshot(values["snapshot"], target, document.get("cell_origins"))
        # The snapshot already contains previously corrected cell values.
        # Remove superseded scalar edits so they cannot overwrite the book.
        original = next(document for document in case["documents"] if document["id"] == target)
        for key in list(case.get("overrides", {})):
            if key.startswith(f"cell:{target}:") or key.startswith(f"fact:{target}:") and key[len(f"fact:{target}:"):] in original["cells"] or key.startswith("reported:") and key[len("reported:"):] in {field["id"] for field in document["report_fields"]}:
                del case["overrides"][key]
    elif kind in ("cell", "text", "pdf_field"):
        from .content_edits import validate_content_edit
        validate_content_edit(result, kind, target, values)
    elif kind == "fact":
        facts = {fact["id"]: fact for document in result["documents"] for fact in document["facts"].values()}
        if target not in facts or set(values) != {"value"}:
            raise ValueError("Неизвестный реквизит")
        fact = facts[target]
        if fact["kind"] == "money":
            if fact.get("formula") or fact.get("dependencies"):
                raise ValueError("Значение вычисляется из исходных ячеек. Исправьте ячейку-источник, чтобы сохранить проверяемую формулу.")
            values["value"] = money(values["value"])
        elif fact["key"] == "date":
            date.fromisoformat(values["value"])
        elif not isinstance(values["value"], str) or not values["value"].strip():
            raise ValueError("Введите значение реквизита")
    elif kind == "reported":
        if target not in {field["id"] for document in result["documents"] for field in document["report_fields"]} or set(values) != {"value"}:
            raise ValueError("Неизвестная ячейка отчета")
        values["value"] = money(values["value"])
    elif kind == "check":
        if target not in result["nodes"] or result["nodes"][target]["kind"] != "check" or set(values) != {"rule"}:
            raise ValueError("Неизвестная проверка")
        rule = values["rule"]
        if not isinstance(rule, dict) or set(rule) != {"expression", "inputs"} or not isinstance(rule["inputs"], list):
            raise ValueError("Укажите формулу и список источников")
        names = []
        for item in rule["inputs"]:
            if set(item) != {"name", "ref"} or not re.fullmatch(r"x[1-9]\d*", item["name"]) or item["ref"] not in result["nodes"]:
                raise ValueError("Проверьте имена x1, x2 и ссылки на источники")
            source = result["nodes"][item["ref"]]
            if item["ref"] == result["nodes"][target].get("source_ref"):
                raise ValueError("Показатель нельзя подтверждать его собственным значением")
            if source["kind"] == "fact":
                document = next(document for document in result["documents"] if document["id"] == source["document_id"])
                report_evidence = result["nodes"][target]["reported_evidence"]
                same_report = (document.get("structured_report") and document["id"] == report_evidence["document_id"]
                               and source["evidence"].get("method") == "structured-field")
                if not (document["role"] == "source" and document["included"] or document["role"] == "report" and
                        (result["context"].get("basis") == "published_financial_tables" or same_report)):
                    raise ValueError("Для формулы выберите включенный первичный документ или проверяемый показатель")
            names.append(item["name"])
        if not names or len(names) != len(set(names)):
            raise ValueError("Выберите источники с уникальными именами")
        references = formula_references(rule["expression"])
        if set(references) != set(names):
            raise ValueError("Формула должна использовать все выбранные источники")
    else:
        raise ValueError("Неизвестный тип исправления")
    candidate = copy.deepcopy(case)
    key = f"{kind}:{target}"
    candidate.setdefault("overrides", {})[key] = dict(candidate["overrides"].get(key, {}), **values)
    updated = calculate(candidate)  # Includes dependency-cycle validation before saving.
    if kind == "cell" and values["value"].startswith("="):
        identifier, address = target.split(":", 1)
        document = next(document for document in updated["documents"] if document["id"] == identifier)
        error = document["cells"][address].get("error")
        if error:
            raise ValueError(error)
    return candidate
