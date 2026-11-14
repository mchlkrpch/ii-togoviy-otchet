import copy
import io
import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.comments import Comment
from backend.spreadsheet_editor import (
    workbook_snapshot,
    normalize_snapshot,
    snapshot_bytes,
)
from backend.extraction import extract_document, add_fact
from backend.engine import validate_change, calculate
from test_verification import example_case


class SpreadsheetEditorTests(unittest.TestCase):
    def source(self, path):
        book = Workbook()
        sheet = book.active
        sheet.title = "Ведомость"
        sheet["A1"] = "Ведомость заработной платы"
        sheet.merge_cells("A1:D1")
        sheet["A3"] = "Договор: 24/03/15-01 | Период: Январь 2026"
        sheet["C6"] = 100
        sheet["C7"] = 10
        sheet["C8"] = "=SUM(C6:C7)"
        sheet["C6"].font = Font(name="Arial", bold=True, color="000000")
        sheet["C6"].fill = PatternFill("solid", fgColor="E8F0FE")
        sheet["C6"].number_format = "#,##0.00"
        sheet.column_dimensions["C"].width = 25
        sheet.row_dimensions[6].height = 30
        sheet.freeze_panes = "C5"
        sheet["C6"].comment = Comment("Проверить по ведомости", "Бухгалтер")
        book.save(path)
        book.close()
        document = extract_document(path)
        evidence = {"text": "24/03/15-01", "locator": "Реквизиты", "confidence": 100}
        for key, value in (
            ("contract", "24/03/15-01"),
            ("date", "2026-01-15"),
            ("period", "2026-01"),
        ):
            add_fact(document, key, key, value, evidence, "text")
        document["category"] = "0200"
        add_fact(
            document,
            "amount",
            "Итого",
            "110",
            document["facts"]["Ведомость!C8"]["evidence"],
            dependencies=[document["id"] + ":Ведомость!C8"],
        )
        return document

    def test_edit_and_roundtrip_style_layout_values_and_formulas(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "salary.xlsx"
            document = self.source(path)
            original = path.read_bytes()
            snapshot = workbook_snapshot(path, document)
            sheet = snapshot["sheets"]["sheet-0"]
            cell = sheet["cellData"]["5"]["2"]
            cell["v"] = 120
            cell["s"]["it"] = 1
            candidate = example_case()
            candidate["documents"][1] = document
            candidate = validate_change(
                candidate, "workbook", document["id"], {"snapshot": snapshot}
            )
            result = calculate(candidate)
            self.assertEqual(result["nodes"]["r2026_c5"]["expected"], "130.00")
            document = next(
                doc for doc in result["documents"] if doc["id"] == document["id"]
            )
            output = snapshot_bytes(document["workbook_snapshot"])
            book = load_workbook(io.BytesIO(output))
            self.assertEqual(book.active["C6"].value, 120)
            self.assertTrue(book.active["C6"].font.bold)
            self.assertTrue(book.active["C6"].font.italic)
            self.assertEqual(book.active["C6"].number_format, "#,##0.00")
            self.assertEqual(book.active["C8"].value, "=SUM(C6:C7)")
            self.assertEqual(book.active["C6"].comment.text, "Проверить по ведомости")
            self.assertEqual(str(book.active.freeze_panes), "C5")
            self.assertIn("A1:D1", book.active.merged_cells)
            self.assertEqual(book.active.row_dimensions[6].height, 30)
            self.assertAlmostEqual(book.active.column_dimensions["C"].width, 25)
            book.close()
            self.assertEqual(path.read_bytes(), original)

    def test_native_freeze_sentinel_and_copied_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "salary.xlsx"
            document = self.source(path)
            snapshot = workbook_snapshot(path, document)
            data = snapshot["sheets"]["sheet-0"]
            data["freeze"] = {
                "startColumn": -1,
                "xSplit": 0,
                "startRow": 4,
                "ySplit": 4,
            }
            data["cellData"].setdefault("1", {})["2"] = copy.deepcopy(
                data["cellData"]["5"]["2"]
            )
            normalized = normalize_snapshot(snapshot, document["id"])
            self.assertEqual(
                normalized["sheets"]["sheet-0"]["cellData"]["5"]["2"]["custom"][
                    "auditOrigin"
                ],
                "Ведомость!C6",
            )
            self.assertNotEqual(
                normalized["sheets"]["sheet-0"]["cellData"]["1"]["2"]["custom"][
                    "auditOrigin"
                ],
                "Ведомость!C6",
            )
            book = load_workbook(io.BytesIO(snapshot_bytes(normalized)))
            self.assertEqual(book.active.freeze_panes, "A5")
            book.close()

    def test_inserted_empty_cell_does_not_break_sum_but_deleted_source_does(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "salary.xlsx"
            document = self.source(path)
            snapshot = workbook_snapshot(path, document)
            data = snapshot["sheets"]["sheet-0"]
            data["cellData"] = {
                str(int(row) + 1 if int(row) >= 6 else int(row)): columns
                for row, columns in data["cellData"].items()
            }
            data["cellData"]["6"] = {
                "2": {"custom": {"auditOrigin": "added_empty"}, "v": None}
            }
            data["cellData"]["8"]["2"]["f"] = "=SUM(C6:C8)"
            case = example_case()
            case["documents"][1] = document
            candidate = validate_change(
                case, "workbook", document["id"], {"snapshot": snapshot}
            )
            self.assertEqual(
                calculate(candidate)["nodes"]["r2026_c5"]["expected"], "110.00"
            )

    def test_inserting_row_keeps_source_identity_and_deleted_source_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "salary.xlsx"
            document = self.source(path)
            snapshot = workbook_snapshot(path, document)
            sheet = snapshot["sheets"]["sheet-0"]
            sheet["cellData"] = {
                str(int(row) + 1 if int(row) >= 5 else int(row)): columns
                for row, columns in sheet["cellData"].items()
            }
            sheet["cellData"]["8"]["2"]["f"] = "=SUM(C7:C8)"
            case = example_case()
            case["documents"][1] = document
            candidate = validate_change(
                case, "workbook", document["id"], {"snapshot": snapshot}
            )
            result = calculate(candidate)
            source = result["nodes"][document["id"] + ":Ведомость!C6"]
            self.assertEqual(source["evidence"]["cell"], "C7")
            self.assertEqual(source["expected"], "100")
            self.assertEqual(result["nodes"]["r2026_c5"]["expected"], "110.00")
            del sheet["cellData"]["6"]["2"]
            candidate = validate_change(
                case, "workbook", document["id"], {"snapshot": snapshot}
            )
            result = calculate(candidate)
            self.assertIsNone(result["nodes"]["r2026_c5"]["expected"])

    def test_snapshot_validation_rejects_duplicate_sheet_names_and_large_grids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "salary.xlsx"
            doc = self.source(path)
            snapshot = workbook_snapshot(path, doc)
            snapshot["sheetOrder"].append("other")
            snapshot["sheets"]["other"] = copy.deepcopy(snapshot["sheets"]["sheet-0"])
            with self.assertRaises(ValueError):
                normalize_snapshot(snapshot, doc["id"])
            snapshot["sheets"]["other"]["name"] = "Другой"
            snapshot["sheets"]["other"]["rowCount"] = 10**9
            with self.assertRaises(ValueError):
                normalize_snapshot(snapshot, doc["id"])

    def test_contract_and_period_follow_edited_workbook_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "salary.xlsx"
            document = self.source(path)
            snapshot = workbook_snapshot(path, document)
            snapshot["sheets"]["sheet-0"]["cellData"]["2"]["0"][
                "v"
            ] = "Договор: 11/22/33-44 | Период: Февраль 2026"
            case = example_case()
            case["documents"][1] = document
            candidate = validate_change(
                case, "workbook", document["id"], {"snapshot": snapshot}
            )
            effective = next(
                item
                for item in calculate(candidate)["documents"]
                if item["id"] == document["id"]
            )
            self.assertEqual(effective["facts"]["contract"]["value"], "11/22/33-44")
            self.assertEqual(effective["facts"]["period"]["value"], "2026-02")
