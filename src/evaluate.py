#!/usr/bin/env python3
"""Evaluate an already computed case against an explicitly supplied answer key."""

import argparse
import json
from pathlib import Path
import re

from openpyxl import load_workbook

from backend.engine import calculate
from backend.money import decimal_value, money
from backend.store import Store


def evaluate(result, truth_path):
    workbook = load_workbook(truth_path, data_only=True, read_only=True)
    target = {check["code"]: check for check in result["checks"] if check["row"] == result["context"]["target_year"]}
    rows, excluded = [], []
    try:
        sheet = workbook.worksheets[0]
        for row in sheet.iter_rows(values_only=True):
            if not row:
                continue
            title = str(row[0] or "")
            match = re.search(r"\((\d{4})\)", title)
            code = match[1] if match else "total" if title.upper().startswith("ИТОГО") else None
            if code and len(row) >= 5 and type(row[1]) in (int, float) and type(row[2]) in (int, float):
                expected_status = "mismatch" if abs(decimal_value(row[1]) - decimal_value(row[2])) > decimal_value(result["context"]["tolerance"]) else "verified"
                check = target.get(code)
                rows.append({"code": code, "truth_reported": money(row[1]), "truth_expected": money(row[2]),
                             "truth_status": expected_status, "reported": check["reported"] if check else None,
                             "expected": check["expected"] if check else None,
                             "prediction": check["direct_status"] if check else "unverified",
                             "final_status": check["status"] if check else "unverified"})
            if title in ("Дубликат", "Вне периода", "Другой договор") and row[1]:
                issue_type = {"Дубликат": "duplicate", "Вне периода": "outside_period", "Другой договор": "wrong_contract"}[title]
                found = any(issue["type"] == issue_type and issue["document_name"] == row[1] for issue in result["issues"])
                excluded.append({"document": row[1], "type": issue_type, "found": found})
    finally:
        workbook.close()
    if not rows:
        raise ValueError("В книге эталона не найдены размеченные показатели")
    true_positive = sum(row["truth_status"] == row["prediction"] == "mismatch" for row in rows)
    false_positive = sum(row["truth_status"] != "mismatch" and row["prediction"] == "mismatch" for row in rows)
    false_negative = sum(row["truth_status"] == "mismatch" and row["prediction"] != "mismatch" for row in rows)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else None
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    return {
        "case_id": result["id"], "revision": result["revision"], "truth_file": Path(truth_path).name,
        "scope": "Количественные расхождения пяти размеченных строк данного набора. Связанные агрегаты считаются отдельными строками. Итоговые статусы дополнительно учитывают сомнения в зависимостях.",
        "sample_size": len(rows), "true_positive": true_positive, "false_positive": false_positive,
        "false_negative": false_negative, "precision": precision, "recall": recall, "f1": f1,
        "report_extraction_exact": sum(row["reported"] == row["truth_reported"] for row in rows),
        "expected_value_exact": sum(row["expected"] == row["truth_expected"] for row in rows),
        "classification_exact": sum(row["prediction"] == row["truth_status"] for row in rows),
        "rows": rows, "document_exclusions": excluded,
        "limitations": "Один небольшой открытый набор. Метрики не оценивают качество на новых формах документов или всех 51 проверках. Книга эталона читается только этой отдельной командой.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="идентификатор сохраненной проверки; по умолчанию последняя")
    parser.add_argument("--truth", type=Path, required=True, help="явный путь к книге эталонных ответов")
    parser.add_argument("--output", type=Path, help="путь к JSON с метриками")
    args = parser.parse_args()
    store = Store()
    cases = store.list_cases()
    if not cases:
        parser.error("Сначала создайте проверку через интерфейс")
    case = store.get_case(args.case) if args.case else cases[0]
    metrics = evaluate(calculate(case), args.truth)
    content = json.dumps(metrics, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")


if __name__ == "__main__":
    main()
