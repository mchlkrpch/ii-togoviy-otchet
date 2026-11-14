import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image

from backend.numeric_regions import numeric_regions


class NumericRegionTests(unittest.TestCase):
    def test_pdf_highlight_covers_number_and_uses_the_correct_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((40, 80), "Service cost: 245 000,00 RUB")
            page.insert_text((40, 140), "Other cost: 245 000,00 RUB")
            width, height = page.rect.width, page.rect.height
            expected = page.search_for("245 000,00")[1]
            pdf.save(path)
            pdf.close()
            evidence = {"page": 1, "rect": [30 / width, 120 / height, 300 / width, 150 / height], "text": "Other cost: 245 000,00 RUB"}
            doc = {"facts": {"amount": {"kind": "money", "value": "245000.00", "evidence": evidence}}}
            regions = numeric_regions(doc, path)
            self.assertEqual(len(regions), 1)
            self.assertEqual(regions[0]["text"], "245 000,00")
            self.assertAlmostEqual(regions[0]["rect"][0], expected.x0 / width)
            self.assertAlmostEqual(regions[0]["rect"][1], expected.y0 / height)
            self.assertGreater(regions[0]["rect"][0], evidence["rect"][0])
            doc["facts"]["amount"]["value"] = "0.00"
            self.assertEqual(numeric_regions(doc, path), [])

    def test_ocr_combines_numeric_words_and_excludes_the_label(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.png"
            Image.new("RGB", (100, 100), "white").save(path)
            evidence = {"page": 1, "rect": [.05, .2, .9, .3], "text": "Стоимость услуг: 98 000,00 руб."}
            doc = {"facts": {"amount": {"kind": "money", "value": "98000.00", "evidence": evidence}}}
            words = [{"text": "Стоимость", "rect": [.05, .2, .3, .3], "line": 1},
                     {"text": "услуг:", "rect": [.3, .2, .4, .3], "line": 1},
                     {"text": "98", "rect": [.5, .2, .55, .3], "line": 1},
                     {"text": "000,00", "rect": [.56, .2, .75, .3], "line": 1},
                     {"text": "руб.", "rect": [.76, .2, .9, .3], "line": 1}]
            with patch("backend.numeric_regions.page_words", return_value=words):
                regions = numeric_regions(doc, path)
            self.assertEqual(regions[0]["rect"], [.5, .2, .75, .3])
            self.assertEqual(regions[0]["text"], "98 000,00")
