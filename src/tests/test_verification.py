import copy
from decimal import Decimal
import tempfile
from pathlib import Path
import unittest

from openpyxl import Workbook

from backend.engine import calculate, validate_change
from backend.excel import read_workbook
from backend.extraction import REPORT_COLUMNS, add_fact, extract_document
from backend.money import decimal_value, evaluate_formula, find_money
from backend.ingest import process_files
from backend.store import RevisionConflict, Store


def source(identifier, category, amount, contract="24/03/15-01", document_date="2026-01-15"):
    document = {"id": identifier, "name": identifier + ".pdf", "hash": identifier,
                "role": "source", "facts": {}, "cells": {}, "report_fields": [], "errors": [],
                "category": category, "category_reason": "Тестовое содержание", "blocks": []}
    evidence = {"text": "Исходный фрагмент", "locator": "Страница 1", "page": 1, "method": "pdf-text", "confidence": 100}
    for key, value in (("contract", contract), ("date", document_date), ("number", identifier), ("amount", amount)):
        add_fact(document, key, key, value, evidence, "money" if key == "amount" else "text")
    if category in ("0200", "0300"):
        add_fact(document, "period", "Месяц", document_date[:7], evidence, "text")
    return document


def example_case():
    values = {3: "1.00", 4: "0.00", 5: "110.00", 6: "100.00", 7: "100.00", 8: "0.00", 9: "10.00",
              10: "33.00", 11: "33.00", 12: "33.00", 13: "0.00", 14: "0.00", 15: "20.00", 16: "30.00", 17: "193.00"}
    report = source("report", None, "193.00")
    report["role"] = "report"
    for column, (code, title, unit) in REPORT_COLUMNS.items():
        report["report_fields"].append({"id": f"r2026_c{column}", "row": "2026", "row_title": "2026",
                                        "code": code, "title": title, "unit": unit, "value": values[column],
                                        "evidence": {"text": values[column], "locator": f"Графа {column}", "confidence": 100}, "needs_review": False})
    payroll = source("payroll", "0200", "110.00")
    add_fact(payroll, "main", "Основная зарплата", "100.00", payroll["facts"]["amount"]["evidence"])
    add_fact(payroll, "additional_reserve", "Доплаты и резерв", "10.00", payroll["facts"]["amount"]["evidence"])
    insurance = source("insurance", "0300", "33.00")
    add_fact(insurance, "base", "База", "110.00", insurance["facts"]["amount"]["evidence"])
    return {"id": "case", "name": "Проверка", "revision": 1, "created_at": "2026-11-13T00:00:00+03:00",
            "report_id": report["id"], "documents": [report, payroll, insurance, source("production", "0800", "20.00"), source("admin", "0900", "30.00")],
            "context": {"contract": "24/03/15-01", "start": "2026-01-01", "end": "2026-01-31", "target_year": "2026", "tolerance": "0.01"},
            "overrides": {}, "audit": []}


class VerificationTests(unittest.TestCase):
    def test_money_uses_decimal_and_explicit_rounding(self):
        value, _ = evaluate_formula("ROUND(x1 + x2, 2)", {"x1": "0.10", "x2": "0.20"})
        self.assertEqual(value, Decimal("0.30"))
        self.assertEqual(evaluate_formula("ROUND(1.005, 2)", {})[0], Decimal("1.01"))
        with self.assertRaises(ValueError):
            decimal_value("NaN")

    def test_date_cannot_be_misread_as_a_monetary_amount(self):
        self.assertEqual(find_money("Дата 10.04.2026. Сумма 270 000,00 руб."), [("270 000,00", "270000.00")])

    def test_calculated_source_total_cannot_override_its_operands(self):
        case = example_case()
        fact = case["documents"][1]["facts"]["amount"]
        fact["dependencies"] = [case["documents"][1]["facts"]["main"]["id"]]
        with self.assertRaisesRegex(ValueError, "исходных ячеек"):
            validate_change(case, "fact", fact["id"], {"value": "999.00"})

    def test_formula_rejects_code_execution_and_division_by_zero(self):
        for expression in ('__import__("os").system("id")', "x1 ** 100000", "1 / 0"):
            with self.assertRaises(ValueError):
                evaluate_formula(expression, {"x1": 1})

    def test_xlsx_recalculates_formula_without_cached_values(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Данные"
        sheet["C6"], sheet["C7"], sheet["C8"] = 100.11, 10.22, "=SUM(C6:C7)"
        second = workbook.create_sheet("Итог")
        second["A1"] = "='Данные'!C8 * 2"
        cells = read_workbook(workbook)
        self.assertEqual(cells["Данные!C8"]["value"], "110.33")
        self.assertEqual(cells["Итог!A1"]["value"], "220.66")
        self.assertEqual(cells["Данные!C8"]["dependencies"], ["Данные!C6", "Данные!C7"])
        workbook.close()

    def test_xlsx_does_not_round_rates_or_hide_cycles_and_missing_cells(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet["A1"], sheet["A2"] = 0.12345, "=A1*10000"
        sheet["B1"], sheet["B2"] = "=B2", "=B1"
        sheet["C1"] = "=D1+1"
        cells = read_workbook(workbook)
        self.assertEqual(cells["Sheet!A2"]["value"], "1234.50000")
        self.assertIn("Циклическая", cells["Sheet!B1"]["error"])
        self.assertIsNone(cells["Sheet!C1"]["value"])
        self.assertTrue(cells["Sheet!C1"]["error"])
        workbook.close()

    def test_zero_without_documents_is_not_confirmed(self):
        result = calculate(example_case())
        self.assertEqual(result["nodes"]["r2026_c4"]["status"], "unverified")
        self.assertIsNone(result["nodes"]["r2026_c4"]["expected"])

    def test_document_deduplication_contract_and_period(self):
        case = example_case()
        duplicate = copy.deepcopy(case["documents"][-1])
        duplicate.update(id="duplicate", name="duplicate.pdf")
        for fact in duplicate["facts"].values():
            fact["id"] = "duplicate:" + fact["key"]
            fact["document_id"] = "duplicate"
        case["documents"].extend([duplicate, source("other_contract", "0900", "900.00", contract="99/99/99-99"),
                                  source("late", "0900", "800.00", document_date="2026-02-01")])
        result = calculate(case)
        self.assertEqual(result["nodes"]["r2026_c16"]["expected"], "30.00")
        self.assertTrue({"duplicate", "wrong_contract", "outside_period"}.issubset({issue["type"] for issue in result["issues"]}))

    def test_uncertainty_propagates_even_when_sums_match(self):
        result = calculate(example_case())
        payroll = result["nodes"]["r2026_c5"]
        self.assertEqual(payroll["direct_status"], "verified")
        self.assertEqual(payroll["status"], "review")
        self.assertIn("r2026_c6", payroll["blocked_by"])
        self.assertEqual(result["nodes"]["r2026_c17"]["status"], "review")

    def test_missing_month_is_incomplete_data_not_a_proven_mismatch(self):
        case = example_case()
        case["context"]["end"] = "2026-02-28"
        result = calculate(case)
        self.assertEqual(result["nodes"]["r2026_c5"]["direct_status"], "unverified")
        self.assertIn("2026-02", result["nodes"]["r2026_c5"]["coverage_issues"][0])
        self.assertEqual(result["nodes"]["additional_reserve"]["direct_status"], "unverified")

    def test_yearly_report_does_not_sum_sources_from_another_year(self):
        case = example_case()
        case["context"]["start"] = "2025-01-01"
        case["documents"].append(source("prior_year", "0200", "7.00", document_date="2025-01-15"))
        result = calculate(case)
        self.assertEqual(result["nodes"]["r2026_c5"]["expected"], "110.00")

    def test_missing_scope_is_explicit_and_period_removes_june_document(self):
        case = example_case()
        case["context"].update(start="", end="")
        for field in case["documents"][0]["report_fields"]:
            if field["code"] == "0800":
                field["value"] = "32.50"
            if field["code"] == "total":
                field["value"] = "205.50"
        case["documents"].append(source("june", "0900", "165.00", document_date="2026-06-03"))
        provisional = calculate(case)
        self.assertFalse(provisional["summary"]["context_complete"])
        self.assertTrue(provisional["summary"]["scope_issues"])
        self.assertEqual(provisional["summary"]["total_delta"], "-152.50")
        payroll = provisional["nodes"]["r2026_c5"]
        self.assertEqual(payroll["numeric_status"], "verified")
        self.assertEqual(payroll["direct_status"], "unverified")
        self.assertIn("предварительная", provisional["summary"]["conclusion"])

        corrected = validate_change(case, "context", "settings", {"start": "2026-01-01", "end": "2026-05-31"})
        result = calculate(corrected)
        self.assertTrue(result["summary"]["context_complete"])
        self.assertEqual(result["summary"]["total_delta"], "12.50")
        self.assertEqual(result["summary"]["findings"][0], "r2026_c15")
        production = result["nodes"]["r2026_c15"]
        self.assertEqual(production["status"], "mismatch")
        self.assertEqual(production["substitution"], "20,00")
        self.assertIn("выше на 12,50", production["explanation"][0])
        self.assertFalse(next(document for document in result["documents"] if document["id"] == "june")["included"])

    def test_report_period_filters_existing_projects_and_preserves_manual_rules(self):
        case = example_case()
        case["context"].update(start="", end="")
        report = case["documents"][0]
        report["blocks"] = [{"text": line} for line in (
            "Договор от 05.01.2026",
            "Начало 2-го этапа: 10 января 2026 г.",
            "Окончание 2-го этапа: 27 февраля 2026 г.",
        )]
        case["documents"].extend([
            source("last_day", "0900", "2.00", document_date="2026-02-27"),
            source("next_day", "0900", "90.00", document_date="2026-02-28"),
        ])
        result = calculate(case)
        self.assertEqual(result["context"]["end"], "2026-02-27")
        self.assertEqual(result["nodes"]["r2026_c16"]["expected"], "32.00")
        self.assertTrue(any(issue["type"] == "outside_period" and issue["document_id"] == "next_day"
                            for issue in result["issues"]))
        stale = {"expression": "x1 + x2", "inputs": [{"name": "x1", "ref": "admin:amount"},
                                                    {"name": "x2", "ref": "next_day:amount"}]}
        case["overrides"]["check:r2026_c16"] = {"rule": stale}
        check = calculate(case)["nodes"]["r2026_c16"]
        self.assertEqual(check["rule"], stale)
        self.assertIsNone(check["delta"])
        case["overrides"]["context:settings"] = {"end": "2026-02-28"}
        self.assertEqual(calculate(case)["nodes"]["r2026_c16"]["expected"], "120.00")
        self.assertEqual(case["context"]["end"], "")

    def test_editing_source_changes_formula_and_descendants(self):
        case = validate_change(example_case(), "fact", "production:amount", {"value": "22.00"})
        result = calculate(case)
        self.assertEqual(result["nodes"]["r2026_c15"]["expected"], "22.00")
        self.assertEqual(result["nodes"]["r2026_c15"]["status"], "mismatch")
        self.assertEqual(result["nodes"]["r2026_c17"]["expected"], "195.00")
        self.assertIn("r2026_c15", result["nodes"]["r2026_c17"]["blocked_by"])

    def test_editing_xlsx_operand_recalculates_cached_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "payroll.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet["A1"] = "РАСЧЕТНАЯ ВЕДОМОСТЬ ПО ОПЛАТЕ ТРУДА"
            sheet["A2"] = "Документ: ВЕД-01"
            sheet["A3"] = "Проект: 24/03/15-01 | Период: январь 2026"
            sheet["A6"], sheet["B6"], sheet["C6"] = "0201", "Основная", 100
            sheet["A7"], sheet["B7"], sheet["C7"] = "0202", "Доплаты", 10
            sheet["B8"], sheet["C8"] = "ИТОГО", "=SUM(C6:C7)"
            workbook.save(path)
            workbook.close()
            document = extract_document(path)
            case = example_case()
            case["documents"][1] = document
            changed = validate_change(case, "fact", f"{document['id']}:Sheet!C6", {"value": "101.00"})
            result = calculate(changed)
            self.assertEqual(result["nodes"]["r2026_c5"]["expected"], "111.00")
            self.assertEqual(result["nodes"]["base_insurance"]["expected"], "111.00")

    def test_cycles_and_excluded_sources_cannot_be_saved(self):
        case = example_case()
        cyclic = {"expression": "x1", "inputs": [{"name": "x1", "ref": "r2026_c17"}]}
        with self.assertRaisesRegex(ValueError, "Циклическая"):
            validate_change(case, "check", "r2026_c15", {"rule": cyclic})
        invalid = {"expression": "x1", "inputs": [{"name": "x1", "ref": "report:amount"}]}
        with self.assertRaises(ValueError):
            validate_change(case, "check", "r2026_c15", {"rule": invalid})

    def test_low_confidence_ocr_mismatch_requires_review(self):
        case = example_case()
        field = next(field for field in case["documents"][0]["report_fields"] if field["code"] == "0800")
        field.update(value="200.00", needs_review=True)
        result = calculate(case)
        self.assertEqual(result["nodes"]["r2026_c15"]["status"], "review")
        self.assertEqual(result["nodes"]["r2026_c15"]["delta"], "180.00")

    def test_corrupt_file_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "corrupt.pdf"
            path.write_bytes(b"not a PDF")
            document = extract_document(path)
            self.assertTrue(document["errors"])

    def test_optimistic_revision_rejects_concurrent_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            case = example_case()
            store.save_case(case)
            updated = copy.deepcopy(case)
            updated["revision"] = 2
            store.save_case(updated, expected_revision=1)
            with self.assertRaises(RevisionConflict):
                store.save_case(updated, expected_revision=1)
            self.assertEqual(store.get_case(case["id"])["revision"], 2)

    def test_import_preserves_period_notes_and_commits_complete_case(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            directory = store.files_dir("uploaded")
            directory.mkdir(parents=True)
            workbook = Workbook()
            workbook.active["A1"] = "Показатель отчета"
            workbook.active["B1"] = 100
            workbook.save(directory / "report.xlsx")
            workbook.close()
            (directory / "00_README.txt").write_text("Договор 24/03/15-01. Период 01.01.2026–31.05.2026", encoding="utf-8")
            store.save_job({"id": "job", "status": "queued", "progress": 0})
            process_files(store, "job", "uploaded", "Проверка", "report.xlsx")
            self.assertEqual(store.get_job("job")["status"], "completed")
            case = store.get_case("uploaded")
            self.assertEqual(case["context"]["end"], "2026-05-31")
            self.assertEqual(case["context"]["contract"], "24/03/15-01")


if __name__ == "__main__":
    unittest.main()
