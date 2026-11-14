"""Compare saved predictions with reference annotations and render a local report."""

from collections import Counter
from decimal import Decimal
from html import escape
import json
import os
from pathlib import Path
from urllib.parse import quote

from openpyxl import load_workbook

from backend.money import decimal_value, evaluate_formula
from batch_document_sets import match_reference


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def match_operand(item, spec):
    evidence = item.get("evidence") or {}
    if "filename" in spec and (not item.get("file") or Path(item["file"]).name != spec["filename"]):
        return False
    if any(item.get(key) != value for key, value in spec.items() if key in ("file", "label")):
        return False
    if any(evidence.get(key) != value for key, value in spec.items() if key in ("page", "locator", "sheet", "cell")):
        return False
    return "value" not in spec or decimal_value(item["value"]) == decimal_value(spec["value"])


def check_published(row, proposal, truth):
    references = truth["исходные_ячейки"]
    items = proposal["inputs"] if proposal else []
    documents = {doc["id"]: doc["локальный_файл"] for doc in
                 [truth["итоговый_документ"], *truth["первичные_документы"]]}
    # The evaluator resolves annotations against the full parsed table, independently of the selected variables.
    facts = row["reference_fields"]
    matched = set()
    for reference in references:
        filename = documents[reference["документ"]]
        fields = [field for field in facts if field["file"] == filename]
        graph = {"facts": fields, "pages": [{"number": reference["страница_PDF"], "numeric_fields": []}]}
        match = match_reference(graph, reference)
        if match["status"] == "matched":
            matched.add(match["fact_id"])
    refs = {item["ref"] for item in items}
    common = len(matched & refs)
    extra_values = [item["value"] for item in items if item["ref"] not in matched]
    correct_sources = common == len(references) and all(decimal_value(value) == 0 for value in extra_values)
    expected = decimal_value(truth["расчетный_результат"])
    if decimal_value(row["reported"]) < 0:
        expected = -abs(expected)
    return expected, correct_sources, (common, len(items), len(references))


def group_summary(name, counts):
    tp, errors, misses = (counts[key] for key in ("верно", "ошибка", "пропуск"))
    precision = tp / (tp + errors) if tp + errors else None
    recall = tp / (tp + errors + misses) if tp + errors + misses else None
    denominator = 2 * tp + 2 * errors + misses
    return {"name": name, "tp": tp, "fp": errors, "fn": errors + misses, "misses": misses,
            "precision": precision, "recall": recall, "f1": 2 * tp / denominator if denominator else None}


def expected_value(entry, directory):
    if "workbook" not in entry:
        return decimal_value(entry["value"])
    path = (directory / entry["workbook"]).resolve(strict=True)
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        return decimal_value(str(workbook.worksheets[0][entry["cell"]].value))
    finally:
        workbook.close()


def evaluate(predictions, reference):
    gold = read_json(reference)
    known = {(row["set"], row["target_id"]): row for row in predictions}
    marks = []
    groups = {}
    strict = [0, 0, 0]
    identifiers = set()
    for entry in gold["targets"]:
        key = entry["set"], entry["target"]
        if key in identifiers:
            raise ValueError(f"Повтор эталона {key}")
        identifiers.add(key)
        if key not in known:
            continue
        row = known[key]
        group = entry.get("group", key[0])
        counter = groups.setdefault(group, Counter())
        proposal = next(iter(row["alternatives"]), None)
        if "set_manifest" in entry:
            truth = read_json((reference.parent / entry["set_manifest"]).resolve(strict=True))
            expected, sources_ok, counts = check_published(row, proposal, truth)
            strict = [a + b for a, b in zip(strict, counts)]
        else:
            expected = expected_value(entry, reference.parent)
            inputs = proposal["inputs"] if proposal else []
            fields = entry.get("operands")
            sources_ok = fields is None or (len(fields) == len(inputs) and all(
                sum(match_operand(item, spec) for spec in fields) == 1 for item in inputs) and all(
                sum(match_operand(item, spec) for item in inputs) == 1 for spec in fields))
        if proposal is None:
            state = "пропуск"
        else:
            value = proposal.get("expected")
            reproduced = None
            if value is not None:
                reproduced, _ = evaluate_formula(proposal["rule"]["expression"],
                                                 {item["name"]: item["value"] for item in proposal["inputs"]})
            state = "верно" if reproduced == expected and sources_ok else "ошибка"
        counter[state] += 1
        marks.append({"set": key[0], "target_id": key[1], "status": state, "expected": str(expected),
                      "sources_match": sources_ok})
    total = sum(groups.values(), Counter())
    return {"groups": [group_summary(name, count) for name, count in groups.items()] +
                      ([group_summary("Всего", total)] if total else []),
            "rows": marks,
            "strict_sources": {"matched": strict[0], "predicted": strict[1], "reference": strict[2]},
            "unscored": len(known) - len(marks)}


def local_link(filename, label, directory, page=None):
    path = Path(filename)
    try:
        url = quote(Path(os.path.relpath(path, directory)).as_posix(), safe="/")
    except ValueError:
        url = path.as_uri()
    if page and path.suffix.lower() == ".pdf":
        url += f"#page={page}"
    return f'<a href="{escape(url, quote=True)}" title="{escape(path.name, quote=True)}">{escape(str(label))}</a>'


def render(predictions, metrics, directory):
    marks = {(row["set"], row["target_id"]): row for row in metrics["rows"]}
    def fmt(value):
        return "—" if value is None else f"{value:.2%}".replace(".00%", "%").replace(".", ",")

    summary_rows = []
    for group in metrics["groups"]:
        values = [group["name"], group["tp"], group["fp"], group["misses"], *(fmt(group[key]) for key in ("precision", "recall", "f1"))]
        summary_rows.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in values) + "</tr>")
    strict = metrics.get("strict_sources", {})
    strict_note = ""
    if strict.get("reference"):
        recall = fmt(strict["matched"] / strict["reference"])
        precision = fmt(strict["matched"] / strict["predicted"] if strict["predicted"] else None)
        strict_note = (f"<p>Строгая сверка отдельных ссылок в наборах с аннотациями PDF: precision {precision} "
                       f"({strict['matched']}/{strict['predicted']}), recall {recall} "
                       f"({strict['matched']}/{strict['reference']}). В основной оценке допустимы дополнительные "
                       "нулевые строки нужного раздела.</p>")
    summary = (
        "<h2>Precision и recall</h2><table><thead><tr><th>Набор</th><th>Верно</th><th>Ошибки</th><th>Пропуски</th>"
        "<th>Precision</th><th>Recall</th><th>F1</th></tr></thead><tbody>" + "".join(summary_rows) + "</tbody></table>"
        "<p>Единица оценки — целевая ячейка с эталоном. Сравнивается первое предложение.</p>" + strict_note
        if summary_rows else "<p>Для расчёта precision и recall укажите эталон через <code>--reference</code>.</p>"
    )
    if metrics.get("unscored") and summary_rows:
        summary += f"<p>Ячеек без эталона: {metrics['unscored']}.</p>"
    detail = []
    for row in predictions:
        mark = marks.get((row["set"], row["target_id"]))
        proposal = next(iter(row["alternatives"]), None)
        badge = mark["status"] if mark else ("предложение" if proposal else "пропуск")
        style = {"верно": "correct", "ошибка": "incorrect", "пропуск": "incorrect"}.get(badge, "")
        document = local_link(row["report"], Path(row["report"]).name, directory, row["page"])
        target = escape(row["label"])
        locator = " · ".join(str(value) for value in (row["locator"], row["column"]) if value)
        contents = []
        selected = proposal or row.get("baseline")
        if proposal is None:
            contents.append(f"<p>{escape(row.get('abstention') or 'Предложения нет')}</p>")
        if selected:
            if proposal is None:
                contents.append("<p>Штатный расчёт</p>")
            contents.append(f"<code>{escape(selected['rule']['expression'])}</code>")
            operands = []
            for item in selected["inputs"]:
                evidence = item.get("evidence") or {}
                label = item.get("label") or item["ref"]
                name = f"<code>{escape(item['name'])}</code>"
                source = local_link(item["file"], label, directory, evidence.get("page")) if item.get("file") else escape(label)
                filename = Path(item["file"]).name if item.get("file") else ""
                where = " · ".join(str(value) for value in (filename, evidence.get("locator")) if value)
                operands.append(f"<li>{name} {source} <strong>{escape(str(item['value']))}</strong>"
                                f"<small>{escape(where)}</small></li>")
            contents.append("<ul>" + "".join(operands) + "</ul>")
        output = []
        if row["reported"] is not None:
            output.append(f"В отчёте {escape(str(row['reported']))}")
        if selected and selected["expected"] is not None:
            output.append(f"По формуле {escape(str(selected['expected']))} {escape(str(row['unit']))}")
        if proposal and proposal.get("delta") is not None and decimal_value(proposal["delta"]):
            output.append(f"Расхождение {escape(str(proposal['delta']))}")
        detail.append(f"<tr><th scope='row'>{escape(row['set'])}</th><td>{document}</td>"
                      f"<td>{target}<small>{escape(locator)}</small></td><td>{''.join(contents)}</td>"
                      f"<td><strong class='{style}'>{escape(badge)}</strong><small>{'<br>'.join(output)}</small></td></tr>")

    return """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Проверка прогнозов формул</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;margin:clamp(16px,4vw,48px);color:#16304b}
table{border-collapse:collapse;text-align:left;width:100%}
th,td{padding:.7rem;vertical-align:top;border:1px solid #d5e0ec}
thead{background:#eaf1f9} tbody tr:nth-child(even){background:#f6f9fc}
small{display:block;color:#536981}
a{color:#155da7;overflow-wrap:anywhere} code{overflow-wrap:anywhere}
ul{margin:.4rem 0 0;padding-left:1.25rem}
li{padding:.23rem 0}
.correct{color:#19633a;background:#e7f6ec;padding:.18rem .4rem}
.incorrect{color:#92243b;background:#ffebed;padding:.18rem .4rem}
.scroll{overflow:auto}.scroll table{min-width:850px}td:nth-child(4){width:43%}
</style><h1>Проверка прогнозов формул</h1>""" + summary + """
<h2>Формулы и источники</h2><div class="scroll"><table><thead><tr>
<th>Сет</th><th>Итоговый документ</th><th>Проверяемое поле</th>
<th>Формула и переменные</th><th>Результат</th></tr></thead><tbody>""" + "".join(detail) + "</tbody></table></div></html>"
