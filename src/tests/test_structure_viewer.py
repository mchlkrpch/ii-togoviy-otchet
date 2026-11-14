import copy
import json
import tempfile
import unittest
from pathlib import Path

from visualize_structure import build_model, create_viewer


ROOT = Path(__file__).resolve().parents[2]
STRUCTURE = ROOT / "output/pdf/cost-card.structure.json"
SOURCE = ROOT / "вводные/01_Карточка_учета_фактических_затрат_этап_3.pdf"


@unittest.skipUnless(STRUCTURE.is_file() and SOURCE.is_file(), "Sample PDF structure required")
class StructureViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(STRUCTURE.read_text(encoding="utf-8"))
        cls.model = build_model(cls.document)

    def test_every_body_cell_occurs_once_even_with_merged_row_headers(self):
        page = self.model["pages"][0]
        values = [n for n in page["nodes"] if n["kind"] == "value"]
        expected = [c for c in self.document["pages"][0]["tables"][0]["cells"] if c["row"] >= 5]
        self.assertEqual(len(values), 65)
        self.assertEqual({n["cell_id"] for n in values}, {c["id"] for c in expected})
        self.assertEqual(len({n["id"] for n in page["nodes"]}), len(page["nodes"]))
        ids = {n["id"] for n in page["nodes"]}
        self.assertTrue(all(c in ids for n in page["nodes"] for c in n["children"]))

    def test_branch_bounds_include_header_and_all_descendant_fields(self):
        nodes = {n["id"]: n for n in self.model["pages"][0]["nodes"]}
        def check(node, outer):
            b = node["bbox"]
            self.assertTrue(outer[0] <= b[0] <= b[2] <= outer[2])
            self.assertTrue(outer[1] <= b[1] <= b[3] <= outer[3])
            for cid in node["children"]:
                check(nodes[cid], outer)
        for node in nodes.values():
            if node["kind"] == "header":
                check(node, node["bbox"])
        salary = nodes["p1.t1/r1c5"]
        self.assertEqual(salary["column_span"], 5)
        self.assertEqual(salary["bbox"][3], self.document["pages"][0]["tables"][0]["bbox"][3])

    def test_row_projection_keeps_full_selector_and_source(self):
        page = self.model["pages"][0]
        node = next(n for n in page["nodes"] if n["id"] == "p1.t1/r8c17")
        self.assertEqual(node["label"], "11580377.40")
        self.assertEqual(node["column_path"], ["Итого"])
        self.assertEqual(node["row_path"], ["3 этап, 2026 г.:"])
        self.assertIn(node["id"], page["row_roots"][2]["children"])

    def test_wrong_pdf_is_rejected_before_rendering(self):
        document = copy.deepcopy(self.document)
        document["source"]["sha256"] = "0"*64
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                create_viewer(SOURCE, document, Path(directory)/"view.html")

    def test_html_is_self_contained_and_document_text_cannot_break_out(self):
        document = copy.deepcopy(self.document)
        document["source"]["name"] = '</script><script>alert("injected")</script>'
        with tempfile.TemporaryDirectory() as directory:
            path = create_viewer(SOURCE, document, Path(directory)/"view.html", dpi=72)
            html = path.read_text(encoding="utf-8")
            self.assertIn("data:image/png;base64,", html)
            self.assertNotIn("__DOCUMENT_DATA__", html)
            self.assertNotIn('<script>alert("injected")</script>', html)
            self.assertNotIn('<script src=', html)
            self.assertNotIn('<link ', html)


if __name__ == "__main__":
    unittest.main()
