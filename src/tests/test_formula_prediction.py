import copy
import unittest
from unittest.mock import patch

from backend.engine import calculate, source_rules
from backend.formula_prediction import field_metadata, predict
from backend.prediction_fields import case_fields, target_field
from backend.prediction_service import suggest
from test_verification import example_case


class FixedEncoder:
    def similarities(self, query, texts):
        if query.startswith("Общепроизводственные расходы") or query.startswith("Временное пользование"):
            return [.95 if "калибров" in text else .3 for text in texts]
        if query.startswith("Общехозяйственные расходы"):
            return [.95 if "управлен" in text else .3 for text in texts]
        if query.startswith("Материальные затраты") or query.startswith("Аванс, проценты"):
            return [.3 for _ in texts]
        return [.85 for _ in texts]


class FormulaPredictionTests(unittest.TestCase):
    def test_kfz_prediction_uses_source_ids_without_saved_formula_or_target_value(self):
        case = example_case()
        case["documents"][-2]["blocks"] = [{"text": "Калибровка приборов"}]
        case["documents"][-1]["blocks"] = [{"text": "Управление договором"}]
        result = calculate(case)
        target = target_field(result["nodes"]["r2026_c15"], result["documents"][0])
        fields = case_fields(result)
        first = predict(target, fields, FixedEncoder())
        self.assertEqual(first["suggestions"][0]["rule"]["inputs"], [{"name": "x1", "ref": "production:amount"}])
        target["value"] = "900000"
        for field in fields:
            field["value"] = "123456"
        second = predict(target, fields, FixedEncoder())
        self.assertEqual(first, second)

    def test_plan_column_overrides_broad_page_heading(self):
        field = field_metadata("field", "Зарплата", ["2.1.2.", "Зарплата"],
            ["План на 2024-2025 и исполнение", "План на 2025 год"], page_heading="Исполнение плана 2024")
        self.assertEqual(field["period"], "2025")
        self.assertEqual(field["scenario"], "plan")

    def test_unit_conversion_and_rounding_are_from_metadata(self):
        target = {"id": "target", "label": "Программы", "scope": "5", "unit": "млн руб.", "period": "2025", "precision": 1}
        fields = [{"id": "source", "label": "Программы", "document_id": "source", "scope": "5", "unit": "тыс. руб.", "period": "2025", "column": "Сумма распределения"}]
        rule = predict(target, fields, FixedEncoder())["suggestions"][0]["rule"]
        self.assertEqual(rule["expression"], "ROUND(x1 / 1000, 1)")

    def test_numbered_aggregate_uses_immediate_children(self):
        target = {"id": "target", "code": "2", "label": "Расходы", "unit": "RUB"}
        fields = [{"id": code, "code": code, "label": code, "document_id": "source", "unit": "RUB"}
                  for code in ("2.1", "2.1.1", "2.2")]
        refs = predict(target, fields, FixedEncoder())["suggestions"][0]["rule"]["inputs"]
        self.assertEqual([item["ref"] for item in refs], ["2.1", "2.2"])

    def test_insufficient_metadata_abstains(self):
        target = {"id": "target", "label": "Неизвестный показатель", "unit": "RUB", "period": "2025"}
        fields = [{"id": "source", "label": "Другое", "document_id": "source", "unit": "чел.", "period": "2024"}]
        self.assertEqual(predict(target, fields, FixedEncoder())["suggestions"], [])

    def test_proposal_does_not_mutate_case_or_use_manual_rule(self):
        case = example_case()
        case["documents"][-2]["blocks"] = [{"text": "Калибровка приборов"}]
        case["documents"][-1]["blocks"] = [{"text": "Управление договором"}]
        case["overrides"]["check:r2026_c15"] = {"rule": {"expression": "x1 * 5", "inputs": [{"name": "x1", "ref": "admin:amount"}]}}
        original = copy.deepcopy(case)
        class Files:
            def files_dir(self, identifier):
                from pathlib import Path
                return Path("/nonexistent")
        with patch("backend.formula_prediction.encoder.similarities", FixedEncoder().similarities):
            response = suggest(case, Files(), "r2026_c15")
        self.assertEqual(case, original)
        self.assertEqual(response["suggestions"][0]["expected"], "20.00")

    def test_service_period_takes_precedence_over_payment_date(self):
        case = example_case()
        payroll = case["documents"][1]
        payroll["included"] = True
        payroll["facts"]["period"]["value"] = "2025-12"
        self.assertNotIn("0200", source_rules([payroll], "2026"))
        self.assertEqual(source_rules([payroll], "2025")["0200"], ["payroll:amount"])

    def test_growth_uses_current_and_previous_year(self):
        target = {"id": "target", "label": "Рост выручки", "unit": "%", "period": "2025"}
        fields = [{"id": year, "label": "Выручка", "unit": "RUB", "period": year, "document_id": "source"}
                  for year in ("2024", "2025")]
        rule = predict(target, fields, FixedEncoder())["suggestions"][0]["rule"]
        self.assertEqual([item["ref"] for item in rule["inputs"]], ["2025", "2024"])
        self.assertIn("100 *", rule["expression"])


if __name__ == "__main__":
    unittest.main()
