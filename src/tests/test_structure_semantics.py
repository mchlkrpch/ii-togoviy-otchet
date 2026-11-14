import copy
import json
import unittest
from pathlib import Path

from structure_semantics import (enrich_document, extract_pairs, find_facts,
                                 get_fact, get_key_value, row_context)

ROOT = Path(__file__).resolve().parents[2]


class PairTests(unittest.TestCase):
    def test_explicit_key_value_preserves_source(self):
        fields = extract_pairs([{"text": "Исполнитель: МФТИ, Физтех", "bbox": [1, 2, 30, 5]}], 1)
        self.assertEqual(fields[0]["key"], "исполнитель")
        self.assertEqual(fields[0]["value"], "МФТИ, Физтех")

    def test_duplicate_keys_are_retained_without_last_value_wins(self):
        doc = {"pages": [{"number": 1, "tables": [], "text_blocks": [
            {"text": "Исполнитель: Альфа", "bbox": [1, 2, 30, 5]},
            {"text": "Исполнитель: Бета", "bbox": [1, 6, 30, 9]}]}]}
        enrich_document(doc)
        self.assertNotIn("исполнитель", doc["key_values"])
        self.assertEqual(len(doc["ambiguous_keys"]["исполнитель"]), 2)
        with self.assertRaises(LookupError):
            get_key_value(doc, "Исполнитель")

    def test_context_distinguishes_total_reference_and_detail(self):
        self.assertEqual(row_context(["3 этап, 2026 г.:"])["year"], 2026)
        self.assertEqual(row_context(["ВСЕГО 3 этап:"])["aggregation"], "total")
        self.assertNotIn("year", row_context(["ВСЕГО 3 этап:"]))
        self.assertTrue(row_context(["2 этап (справочно)", "Август 2025"])["reference"])


@unittest.skipUnless((ROOT / "output/pdf/cost-card.structure.json").is_file(), "Parsed sample required")
class SemanticScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads((ROOT / "output/pdf/cost-card.structure.json").read_text())
        enrich_document(cls.document)

    def test_requested_executor_and_labor_example(self):
        self.assertEqual(get_key_value(self.document, "ИСПОЛНИТЕЛЬ:")["value"], "МФТИ, Физтех")
        f = get_fact(self.document, metric="Трудоемкость", stage=3, year=2026)
        self.assertEqual((f["value"], f["unit"]), ("34.14", "человеко-месяц"))
        self.assertEqual(f["row_hierarchy"], ["3 этап", "2026 г."])
        self.assertEqual(f["source"]["row_header_cell_ids"], ["r8c1"])
        self.assertEqual(f["source"]["column_header_cell_ids"], ["r1c3"])

    def test_deterministic_query_and_contradictory_context(self):
        self.assertEqual(get_fact(self.document, query="трудоемкость за 3 этап 2026")["value"], "34.14")
        self.assertEqual(find_facts(self.document, query="трудоемкость 3 этап 2026", year=2025), [])
        self.assertEqual(find_facts(self.document, metric="Выручка"), [])

    def test_ambiguous_search_rejects_automatic_choice(self):
        with self.assertRaises(LookupError):
            get_fact(self.document, metric="Трудоемкость", stage=3)
        total = get_fact(self.document, metric="Трудоемкость", stage=3, aggregation="total")
        self.assertEqual(total["value"], "34.68")

    def test_two_axes_and_key_values_cover_all_values(self):
        self.assertEqual(len(self.document["facts"]), 60)
        table = self.document["pages"][0]["tables"][0]
        self.assertEqual(table["rows"][2]["key_values"]["/Трудоемкость (человеко-месяц)"], "34.14")
        self.assertEqual(sum(len(r["key_values"]) for r in table["rows"]), 60)
        self.assertEqual([n["label"] for n in table["row_tree"]], ["2 этап", "3 этап"])
        before = copy.deepcopy(self.document)
        enrich_document(self.document)
        self.assertEqual(before, self.document)


if __name__ == "__main__":
    unittest.main()
