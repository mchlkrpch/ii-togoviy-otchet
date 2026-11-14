"""Generate financial expressions from metadata. Values never select a formula."""

from collections import defaultdict
from decimal import Decimal
import re

from .money import decimal_value, evaluate_formula
from .semantic_model import encoder


def normalized(text):
    return re.sub(r"\s+", " ", str(text).lower().replace("ё", "е")).strip()


def unit_scale(unit):
    unit = normalized(unit)
    if unit in ("%", "percent") or "процент" in unit:
        return Decimal(1), "percent"
    if unit in ("rub", "₽") or re.search(r"руб|₽", unit):
        return Decimal(1000000 if "млн" in unit else 1000 if "тыс" in unit else 1), "money"
    return Decimal(1), unit


def metadata_text(field):
    return " | ".join(str(field.get(key, "")) for key in
                      ("label", "section", "column", "period", "scenario", "unit"))


def row_code(parts):
    for part in parts:
        match = re.match(r"\s*(\d+(?:\.\d+)*)(?:\.|\s|$)", part)
        if match:
            return match[1].rstrip(".")
    return ""


def field_metadata(identifier, label, row_path=(), column_path=(), unit="RUB", **extra):
    column = " | ".join(column_path)
    section = " | ".join(row_path[:-1])
    context = " ".join([label, section, column, extra.get("page_heading", "")])
    scope = re.search(r"ЦК\s*№?\s*(\d+)", context, re.I)
    years = re.findall(r"\b20\d{2}\b", column)
    if not years:
        years = re.findall(r"\b20\d{2}\b", extra.get("page_heading", ""))
    scenario = "actual" if re.search(r"исполн|отч[её]т|фактич|направлено", column + " " + extra.get("page_heading", ""), re.I) else "plan" if re.search(r"план", column + " " + extra.get("page_heading", ""), re.I) else ""
    result = {"id": identifier, "label": label, "section": section, "column": column,
              "code": row_code(row_path), "unit": unit, "period": years[-1] if years else "",
              "scenario": scenario, "scope": scope[1] if scope else ""}
    result.update(extra)
    return result


def compatible(target, source, previous=False):
    if target.get("contract") and source.get("contract") and target["contract"] != source["contract"]:
        return False
    target_scale, target_dimension = unit_scale(target.get("unit", "RUB"))
    source_scale, source_dimension = unit_scale(source.get("unit", "RUB"))
    if target_dimension != source_dimension:
        return False
    if target.get("scenario") and source.get("scenario") and target["scenario"] != source["scenario"]:
        return False
    if target.get("scope") and source.get("scope") and target["scope"] != source["scope"]:
        return False
    year = str(target.get("period", ""))[:4]
    source_year = str(source.get("period", ""))[:4]
    expected = str(int(year) - 1) if previous and year.isdigit() else year
    return not (expected and source_year and expected != source_year)


def scaled_name(index, source, target):
    source_scale, _ = unit_scale(source.get("unit", "RUB"))
    target_scale, _ = unit_scale(target.get("unit", "RUB"))
    factor = source_scale / target_scale
    name = f"x{index + 1}"
    if factor == 1:
        return name
    if factor < 1:
        return f"{name} / {format(1 / factor, 'f')}"
    return f"{name} * {format(factor, 'f')}"


def predict(target, fields, semantic_encoder=encoder):
    """Only the metadata keys above reach the encoder or candidate ranking."""
    fields = [field for field in fields if field["id"] not in (target["id"], target.get("source_ref")) and field.get("available", True)]
    if not fields:
        return {"suggestions": [], "abstention": "Нет доступных первичных полей", "model": "rubert-tiny2"}
    similarities = semantic_encoder.similarities(metadata_text(target), [metadata_text(field) for field in fields])
    similarity = dict(zip((field["id"] for field in fields), similarities))
    same_period = [field for field in fields if compatible(target, field)]
    candidates = []

    def add(sources, operation, reason, strength, signs=None):
        if not sources or len(sources) > 80:
            return
        sources = list(dict((field["id"], field) for field in sources).values())
        terms = [scaled_name(index, field, target) for index, field in enumerate(sources)]
        if operation == "balance":
            expression = " + ".join(f"({term})" if sign == 1 else f"-({term})" for term, sign in zip(terms, signs))
        elif operation == "ratio":
            expression = f"100 * ({terms[0]}) / ({terms[1]})"
        elif operation == "growth":
            expression = f"100 * (({terms[0]}) - ({terms[1]})) / ({terms[1]})"
        else:
            expression = " + ".join(terms)
        digits = target.get("precision", 2)
        expression = f"ROUND({expression}, {digits})"
        score = strength + .05 * sum(similarity.get(field["id"], 0) for field in sources) / len(sources)
        rule = {"expression": expression, "inputs": [{"name": f"x{index + 1}", "ref": field["id"]} for index, field in enumerate(sources)]}
        identity = (expression, tuple(field["id"] for field in sources))
        if any(candidate["identity"] == identity for candidate in candidates):
            return
        candidates.append({"identity": identity, "rule": rule, "operation": operation,
                           "reason": reason, "score": round(score, 4), "sources": sources})

    code = target.get("code", "")
    # KФЗ categories refer to independent source amounts, never to saved rules.
    category_fields = [field for field in same_period if field.get("category") == code.split(".")[0]]
    keys = {"0200.main": "main", "0200.extra": "additional_reserve"}
    source_key = keys.get(code, "amount")
    category_fields = [field for field in category_fields if field.get("fact_key") == source_key]
    if category_fields and code in {"0100", "0200", "0300", "0800", "0900", *keys}:
        add(category_fields, "sum", "Содержание операций, статья, договор и период согласованы. Дубли исключены.", .95)

    # Immediate numbered children prevent summing an aggregate and its descendants.
    if code:
        children = [field for field in same_period if field.get("code", "").startswith(code + ".")
                    and field["code"].count(".") == code.count(".") + 1]
        if re.fullmatch(r"\d{4}", code):
            prefix = code.rstrip("0")
            children = [field for field in same_period if re.fullmatch(r"\d{4}", field.get("code", ""))
                        and field["code"] != code and field["code"].startswith(prefix)
                        and len(field["code"].rstrip("0")) == len(prefix) + 1]
        groups = defaultdict(list)
        for field in children:
            groups[(field["document_id"], field.get("column", ""))].append(field)
        for sources in groups.values():
            add(sources, "sum", "Сумма непосредственных дочерних строк одной колонки и периода.", .94)

    if target.get("scope") and not code:
        sources = [field for field in same_period if field.get("scope") == target["scope"]
                   and "распределен" in normalized(field.get("column", ""))]
        groups = defaultdict(list)
        for field in sources:
            groups[(field["document_id"], field.get("scenario", ""), field.get("period", ""))].append(field)
        for sources in groups.values():
            add(sources, "sum", "Строки целевого капитала за нужный год. Масштабы единиц приведены к показателю отчета.", .94)

    label = normalized(target["label"])
    if "остаток" in label or code == "6400":
        previous = "начал" in label
        pool = [field for field in fields if compatible(target, field, previous=previous)]
        groups = defaultdict(dict)
        for field in pool:
            text = normalized(field["label"])
            role = "opening" if "остаток" in text and "начал" in text else "income" if re.search(r"поступлен|поступило", text) and field.get("code") in ("1", "6200", "") else "expense" if (field.get("code") in ("2", "6300") or text == "расходы") else None
            if role:
                groups[(field["document_id"], field.get("column", ""))][role] = field
        for roles in groups.values():
            if set(roles) == {"opening", "income", "expense"}:
                sources = [roles[role] for role in ("opening", "income", "expense")]
                expense_sign = 1 if roles["expense"].get("accounting_negative") else -1
                add(sources, "balance", "Баланс периода — начальный остаток, поступления и расходы. Учтены перенос года и бухгалтерские скобки.", .98, [1, 1, expense_sign])

    if unit_scale(target.get("unit", ""))[1] == "percent":
        money_fields = [field for field in fields if unit_scale(field.get("unit", ""))[1] == "money"]
        revenue = [field for field in money_fields if re.search(r"выручк|оборот", normalized(field["label"]))]
        current = [field for field in revenue if str(field.get("period", ""))[:4] == str(target.get("period", ""))[:4]]
        prior = [field for field in revenue if str(field.get("period", ""))[:4].isdigit() and str(target.get("period", ""))[:4].isdigit() and int(str(field["period"])[:4]) == int(str(target["period"])[:4]) - 1]
        if re.search(r"рост|прирост|изменени", label) and len(current) == len(prior) == 1:
            add([current[0], prior[0]], "growth", "Относительное изменение выручки между соседними периодами.", .95)
        numerator = [field for field in money_fields if re.search(r"прибыл", normalized(field["label"]))]
        if re.search(r"марж|рентабельност", label) and len(numerator) == len(current) == 1:
            add([numerator[0], current[0]], "ratio", "Прибыль / выручка с переводом в проценты.", .92)

    if not candidates:
        ranked = sorted(same_period, key=lambda field: similarity[field["id"]], reverse=True)
        if ranked and similarity[ranked[0]["id"]] >= .8 and (len(ranked) == 1 or similarity[ranked[0]["id"]] - similarity[ranked[1]["id"]] > .08):
            add(ranked[:1], "sum", "Семантически близкий показатель того же периода и размерности. Проверьте реквизиты.", .65)
    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    for candidate in candidates:
        candidate.pop("identity")
        candidate["sources"] = [{key: field.get(key) for key in ("id", "document_id", "label", "unit", "period", "scenario", "evidence")} for field in candidate["sources"]]
    ambiguous = len(candidates) > 1 and abs(candidates[0]["score"] - candidates[1]["score"]) < .025
    return {"suggestions": candidates[:3], "model": "cointegrated/rubert-tiny2", "metadata_only": True,
            "ambiguous": ambiguous, "abstention": "Найдены равнозначные связи. Требуется выбор источников." if ambiguous else "Недостаточно метаданных для однозначной формулы" if not candidates else None}


def evaluate_prediction(suggestion, values):
    return format(evaluate_formula(suggestion["rule"]["expression"], {
        item["name"]: values.get(item["ref"]) for item in suggestion["rule"]["inputs"]})[0], "f")
