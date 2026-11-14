"""Geometry, contextual addressing and a manually transcribed scan regression."""

import copy
import shutil
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np
    from parse_structure import (detect_grids, header_hierarchy, number, parse_pdf,
                                 recover_cells, select_field)
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("Install src/requirements-structure.txt to test the optional structure CLI") from exc


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "вводные/01_Карточка_учета_фактических_затрат_этап_3.pdf"

# Manually transcribed from the rendered scan, columns 3..17, not from OCR.
EXPECTED = [
    "0.00 0.00 54024.59 54024.59 0.00 54024.59 0.00 13488.27 13488.27 0.00 13488.27 0.00 14217.76 29630.84 111361.46",
    "0.54 0.00 152284.98 150449.56 53521.17 96928.39 1835.42 37272.94 37269.26 16163.39 21105.87 3.68 52433.87 71932.26 313924.05",
    "34.14 0.00 5558474.83 5248019.04 5054470.26 193548.78 310455.79 1671359.96 1578126.37 1526450.09 51676.28 93233.59 1145798.10 3204744.51 11580377.40",
    "34.68 0.00 5710759.81 5398468.60 5107991.43 290477.17 312291.21 1708632.90 1615395.63 1542613.48 72782.15 93237.27 1198231.97 3276676.77 11894301.45",
]


class GeometryTests(unittest.TestCase):
    def test_merged_cells_survive_translation_and_scaling(self):
        for scale, dx, dy in [(1, 0, 0), (1, 40, 35), (1.5, 0, 0)]:
            with self.subTest(scale=scale, dx=dx, dy=dy):
                image = np.full((int(500*scale)+dy, int(900*scale)+dx), 255, np.uint8)
                xs = [int(x*scale)+dx for x in (60, 260, 460, 660)]
                ys = [int(y*scale)+dy for y in (80, 160, 220, 300, 380)]
                for y in ys:
                    cv2.line(image, (xs[0], y), (xs[-1], y), 0, 2)
                for i, x in enumerate(xs):
                    cv2.line(image, (x, ys[1] if i == 1 else ys[0]), (x, ys[-1]), 0, 2)
                grids, horizontal, vertical = detect_grids(image)
                self.assertEqual(len(grids), 1)
                grid = grids[0]
                cells, warnings = recover_cells(grid["xs"], grid["ys"], horizontal, vertical)
                self.assertFalse(warnings)
                self.assertEqual((len(grid["xs"])-1, len(grid["ys"])-1), (3, 4))
                self.assertEqual(len(cells), 11)
                self.assertEqual(next(c for c in cells if c["id"] == "r1c1")["column_span"], 2)
                coverage = np.zeros((4, 3), dtype=int)
                for cell in cells:
                    r, c = cell["row"], cell["column"]
                    coverage[r:r+cell["row_span"], c:c+cell["column_span"]] += 1
                self.assertTrue((coverage == 1).all())

    def test_decimal_normalization_preserves_precision(self):
        self.assertEqual(number("5\u00a0558\u202f474,83"), "5558474.83")
        self.assertEqual(number("−0,01"), "-0.01")
        self.assertIsNone(number(": 93 237,27"))
        self.assertIsNone(number("2026 г."))

    def test_identical_leaf_names_have_different_parent_paths(self):
        def cell(id_, row, col, span, text):
            return {"id": id_, "row": row, "column": col, "column_span": span,
                    "row_span": 1, "text": text, "needs_review": False}

        cells = [cell("a", 0, 0, 2, "Оплата труда"), cell("b", 0, 2, 2, "Взносы"),
                 cell("c", 1, 0, 1, "Всего"), cell("d", 1, 1, 1, "Доплаты"),
                 cell("e", 1, 2, 1, "Всего"), cell("f", 1, 3, 1, "Доплаты")]
        tree, columns = header_hierarchy(cells, 2, 4)
        self.assertEqual(len(tree), 2)
        self.assertEqual(columns[0]["header_path"], ["Оплата труда", "Всего"])
        self.assertEqual(columns[2]["header_path"], ["Взносы", "Всего"])


@unittest.skipUnless(SOURCE.is_file() and shutil.which("tesseract"), "Sample PDF and Tesseract required")
class ScanIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = parse_pdf(SOURCE, cache_dir=ROOT / "tmp/pdfs/ocr-cache")
        cls.table = cls.document["pages"][0]["tables"][0]

    def test_actual_scan_geometry_and_all_sixty_numbers(self):
        table = self.table
        self.assertEqual(len(self.document["pages"]), 1)
        self.assertEqual((table["grid"]["columns"], table["grid"]["rows"]), (17, 9))
        self.assertEqual(table["header_row_count"], 4)
        self.assertEqual(table["row_header_column_count"], 2)
        self.assertEqual(len(table["cells"]), 109)
        self.assertEqual(len(table["rows"]), 4)
        self.assertFalse(table["warnings"])
        for row, expected in zip(table["rows"], EXPECTED):
            self.assertEqual([v["value"] for v in row["values"]], expected.split())
            self.assertTrue(all(v["value_type"] == "decimal" for v in row["values"]))

    def test_full_context_resolves_repeated_labels(self):
        paths = [self.table["columns"][i]["header_path"] for i in (5, 10)]
        self.assertEqual(paths[0][-1], paths[1][-1])  # Both are "Всего".
        self.assertNotEqual(paths[0], paths[1])
        values = [select_field(self.document, ["3 этап, 2026 г.:"], path)["value"] for path in paths]
        self.assertEqual(values, ["5248019.04", "1578126.37"])

    def test_missing_and_ambiguous_selectors_fail(self):
        with self.assertRaises(LookupError):
            select_field(self.document, ["3 этап, 2026 г.:"], ["Всего"])
        duplicate = copy.deepcopy(self.document)
        duplicate["pages"][0]["tables"].append(copy.deepcopy(self.table))
        with self.assertRaises(LookupError):
            select_field(duplicate, ["3 этап, 2026 г.:"], ["Итого"])

    def test_selectors_are_independent_of_source_file_and_coordinates(self):
        modified = copy.deepcopy(self.document)
        modified["source"]["name"] = "другой_период.pdf"
        table = modified["pages"][0]["tables"][0]
        table["id"] = "different-local-id"
        for cell in table["cells"]:
            cell["bbox"] = [x+20 for x in cell["bbox"]]
        field = select_field(modified, ["3 этап, 2026 г.:"], ["Итого"])
        self.assertEqual(field["value"], "11580377.40")

    def test_ocr_conflicts_and_margin_cleanup_are_retained(self):
        cells = {c["id"]: c for c in self.table["cells"]}
        self.assertTrue(cells["r6c5"]["needs_review"])
        self.assertGreater(len({c["value"] for c in cells["r6c5"]["ocr_candidates"]}), 1)
        self.assertTrue(cells["r9c14"]["needs_review"])
        self.assertIn("trim-margin-noise", cells["r9c14"]["ocr_candidates"][-1]["method"])
        self.assertEqual(cells["r9c14"]["text"], "93 237,27")

    def test_outside_text_columns_and_signatures_are_preserved(self):
        lines = [b["text"] for b in self.document["pages"][0]["text_blocks"]]
        self.assertIn("Исполнитель: МФТИ, Физтех", lines)
        self.assertTrue(any("Руководитель проекта" in line for line in lines))
        self.assertFalse(any("Исполнитель" in line and "контракт" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
