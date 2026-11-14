import copy
import tempfile
import unittest

from backend.engine import calculate, validate_change
from backend.store import Store
from test_verification import example_case


class FormulaEditorTests(unittest.TestCase):
    def variable(self):
        return {"key": "manual_" + "a" * 32, "document_id": "production", "value": "12.34",
                "evidence": {"document_id": "production", "page": 1, "rect": [.2, .3, .4, .35],
                             "text": "12,34", "locator": "Страница 1", "method": "manual-selection", "confidence": 100}}

    def test_selected_number_and_formula_are_saved_atomically_and_reopened(self):
        case = example_case()
        original = copy.deepcopy(case)
        variable = self.variable()
        ref = "production:" + variable["key"]
        values = {"rule": {"expression": "x1 * 2", "inputs": [{"name": "x1", "ref": ref}]}, "variables": [variable]}
        candidate = validate_change(case, "check", "r2026_c15", values)
        self.assertEqual(case, original)
        result = calculate(candidate)
        self.assertEqual(result["nodes"]["r2026_c15"]["expected"], "24.68")
        self.assertEqual(result["nodes"]["r2026_c17"]["expected"], "197.68")
        self.assertEqual(result["nodes"][ref]["evidence"]["rect"], [.2, .3, .4, .35])
        with tempfile.TemporaryDirectory() as directory:
            Store(directory).save_case(candidate)
            reopened = calculate(Store(directory).get_case("case"))
            self.assertEqual(reopened["variables"][0]["id"], ref)
            self.assertEqual(reopened["nodes"]["r2026_c15"]["expected"], "24.68")

    def test_invalid_binding_or_cycle_leaves_original_untouched(self):
        case = example_case()
        original = copy.deepcopy(case)
        variable = self.variable()
        ref = "production:" + variable["key"]
        rule = {"expression": "x1", "inputs": [{"name": "x1", "ref": ref}]}
        invalid = copy.deepcopy(variable)
        invalid["evidence"]["rect"] = [0, 0, 2, 2]
        with self.assertRaises(ValueError):
            validate_change(case, "check", "r2026_c15", {"rule": rule, "variables": [invalid]})
        invalid = copy.deepcopy(variable)
        invalid["evidence"]["text"] = "Стоимость 12,34"
        with self.assertRaises(ValueError):
            validate_change(case, "check", "r2026_c15", {"rule": rule, "variables": [invalid]})
        with self.assertRaises(ValueError):
            validate_change(case, "check", "r2026_c15", {"rule": {"expression": "x1", "inputs": [{"name": "x1", "ref": "r2026_c15"}]}, "variables": [variable]})
        with self.assertRaises(ValueError):
            validate_change(case, "check", "r2026_c15", {"rule": {"expression": "x1", "inputs": [{"name": "x1", "ref": "r2026_c15"}]}, "variables": []})
        self.assertEqual(case, original)
