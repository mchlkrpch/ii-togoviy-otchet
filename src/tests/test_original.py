import tempfile
from decimal import Decimal
from pathlib import Path
import unittest

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles import Color

from backend.original import display_value, workbook_layout


class OriginalWorkbookTests(unittest.TestCase):
    def test_excel_theme_light_and_dark_colors_are_not_reversed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"theme.xlsx"
            workbook=Workbook();cell=workbook.active['A1'];cell.value='Text'
            cell.font=Font(color=Color(theme=1));cell.fill=PatternFill('solid',fgColor=Color(theme=0))
            workbook.save(path);workbook.close()
            style=workbook_layout(path)['rows'][0]['cells'][0]['style']
            self.assertEqual(style['backgroundColor'],'#FFFFFF')
            self.assertEqual(style['color'],'#000000')
    def test_display_respects_decimal_rounding_and_percentage(self):
        self.assertEqual(display_value(Decimal("1.005"), "0.00"), "1,01")
        self.assertEqual(display_value(Decimal("0.125"), "0.0%"), "12,5%")

    def test_original_styles_merges_and_formula_values_preserve_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.title = "Ведомость"
            sheet.merge_cells("A1:C1")
            sheet["A1"] = "Отчет"
            sheet["A1"].font = Font(name="Arial", size=16, bold=True, color="123456")
            sheet["A1"].fill = PatternFill("solid", fgColor="ABCDEF")
            sheet["A1"].alignment = Alignment(horizontal="center", wrap_text=True)
            sheet["A1"].border = Border(bottom=Side(style="thin", color="333333"))
            sheet.column_dimensions["A"].width = 25
            sheet.row_dimensions[1].height = 36
            sheet["C6"], sheet["C7"], sheet["C8"] = 100.11, 10.22, "=SUM(C6:C7)"
            sheet["C8"].number_format = "#,##0.00"
            sheet.column_dimensions["D"].hidden = True
            sheet["D2"] = "скрыто"
            book.save(path)
            book.close()
            original = path.read_bytes()
            layout = workbook_layout(path)
            heading = layout["rows"][0]["cells"][0]
            self.assertEqual(heading["colSpan"], 3)
            self.assertEqual(heading["style"]["fontFamily"], "Arial")
            self.assertEqual(heading["style"]["color"], "#123456")
            self.assertEqual(heading["style"]["backgroundColor"], "#ABCDEF")
            self.assertEqual(layout["rows"][0]["height"], 48)
            self.assertEqual(layout["columns"][0]["width"], 180)
            self.assertEqual(len(layout["columns"]), 3)
            total = next(cell for cell in layout["rows"][7]["cells"] if cell["address"] == "C8")
            self.assertEqual(total["text"], "110,33")
            self.assertEqual(total["formula"], "=SUM(C6:C7)")
            self.assertEqual(path.read_bytes(), original)
