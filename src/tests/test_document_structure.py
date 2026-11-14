import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
from PIL import Image

from backend import document_structure
from backend.extraction import extract_document
from backend.store import RevisionConflict, Store
from structure_edits import EditError


class DocumentStructureTests(unittest.TestCase):
    def test_slice_keeps_parent_identity_and_saves_word_positions_and_nested_edits(self):
        from backend.app import StructureSliceEdit, StructureTextEdit

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "layout.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=300, height=240)
                page.insert_text((50, 60), "Alpha beta gamma delta epsilon")
                page.insert_text((50, 130), "Source text for a stable word layout")
                pdf.save(path)
            original = path.read_bytes()
            document = extract_document(path)
            store = Store(root / "state")
            initial = document_structure.get_structure(store, "a", document, path)
            graph = initial["model"]["pages"][0]
            node = next(node for node in graph["nodes"] if node["label"].startswith("Alpha"))
            self.assertIn(node["id"], graph["roots"])
            x0, y0, x1, y1 = node["field_bbox"]
            bounds = [x0 + 29, x0 + 55]
            split = StructureSliceEdit(version=0, page=1, node_id=node["id"], action="slice",
                                       x_cuts=bounds, y_cuts=[], parent_index=1)
            result = document_structure.update_structure(store, "a", document, path, split)
            self.assertEqual(result["version"], 1)
            nodes = {node["id"]: node for node in result["model"]["pages"][0]["nodes"]}
            parent = nodes[node["id"]]
            self.assertEqual(parent["field_bbox"], [*bounds[:1], y0, bounds[1], y1])
            self.assertEqual(parent["bbox"], node["field_bbox"])
            self.assertEqual(parent["label"], "beta")
            self.assertEqual([nodes[child]["label"] for child in parent["children"]], ["Alpha", "gamma delta epsilon"])
            self.assertTrue(all(nodes[child]["parent"] == parent["id"] for child in parent["children"]))

            child = nodes[parent["children"][0]]
            nested = StructureSliceEdit(version=1, page=1, node_id=child["id"], action="slice",
                                        y_cuts=[(y0 + y1) / 2], parent_index=0)
            result = document_structure.update_structure(store, "a", document, path, nested)
            self.assertEqual(result["version"], 2)
            nested_nodes = {node["id"]: node for node in result["model"]["pages"][0]["nodes"]}
            nested_child_id, = nested_nodes[child["id"]]["children"]
            self.assertEqual(nested_nodes[nested_child_id]["parent"], child["id"])
            corrected = StructureTextEdit(version=2, page=1, node_id=nested_child_id, text="Edited segment")
            result = document_structure.update_structure(store, "a", document, path, corrected)
            self.assertEqual(result["version"], 3)
            with patch("backend.structure_slices.words_in_field", side_effect=AssertionError("Already recognized")):
                self.assertEqual(document_structure.get_structure(Store(store.root), "a", document, path), result)
            self.assertEqual(document_structure.get_structure(store, "b", document, path), initial)
            with self.assertRaises(RevisionConflict):
                document_structure.update_structure(store, "a", document, path, nested)
            split.version = 3
            with self.assertRaises(EditError):
                document_structure.update_structure(store, "a", document, path, split)
            self.assertEqual(path.read_bytes(), original)

    def test_pdf_atlas_preserves_hierarchy_and_isolates_text_corrections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "report.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=500, height=400)
                page.insert_text((55, 48), "Vendor: University accounting office")
                page.insert_text((55, 70), "An approval statement")
                xs, ys = [50, 150, 250, 350], [95, 123, 151, 179, 207]
                for y in ys:
                    page.draw_line((xs[0], y), (xs[-1], y))
                for i, x in enumerate(xs):
                    page.draw_line((x, ys[1] if i == 2 else ys[0]), (x, ys[-1]))
                for i, labels in enumerate([
                    ["Item", "Expenses", ""], ["Description", "Labor", "Other"],
                    ["Project A", "15", "10"], ["Project B", "35", "9"],
                ]):
                    for x, text in zip(xs, labels):
                        page.insert_text((x + 5, ys[i] + 18), text)
                pdf.save(path)
            original = path.read_bytes()
            document = extract_document(path)
            state_path = root / "state"
            store = Store(state_path)
            initial = document_structure.get_structure(store, "a", document, path)
            graph = initial["model"]["pages"][0]
            nodes = {node["id"]: node for node in graph["nodes"]}
            self.assertIn("p1/text1", graph["roots"])
            expenses = next(node for node in nodes.values() if node["kind"] == "header" and node["label"] == "Expenses")
            self.assertEqual([nodes[child]["label"] for child in expenses["children"]], ["Labor", "Other"])
            self.assertTrue(all(nodes[child]["parent"] == expenses["id"] and nodes[child]["color"] == expenses["color"]
                                for child in expenses["children"]))
            self.assertEqual(initial["model"]["key_values"], {"vendor": "University accounting office"})
            pair = next(node for node in nodes.values() if node["kind"] == "key_value")
            change = SimpleNamespace(version=0, page=1, node_id=pair["id"], text="Updated accounting office")
            updated = document_structure.update_structure(store, "a", document, path, change)
            self.assertEqual(updated["version"], 1)
            self.assertEqual(updated["model"]["key_values"], {"vendor": "Updated accounting office"})
            updated_pair = next(node for node in updated["model"]["pages"][0]["nodes"] if node["id"] == pair["id"])
            self.assertEqual(updated_pair["value_bbox"], pair["value_bbox"])
            self.assertEqual(updated_pair["original"]["text"], "University accounting office")
            change.version = 1
            change.text = "Latest correction"
            updated = document_structure.update_structure(store, "a", document, path, change)
            with patch.object(document_structure, "extract_model", side_effect=AssertionError("Already cached")):
                self.assertEqual(document_structure.get_structure(Store(state_path), "a", document, path), updated)
                self.assertEqual(document_structure.get_structure(store, "b", document, path), initial)
            with self.assertRaises(RevisionConflict):
                document_structure.update_structure(store, "a", document, path, change)
            change.version = 2
            change.node_id = next(node["id"] for node in nodes.values() if node["kind"] == "table")
            with self.assertRaises(EditError):
                document_structure.update_structure(store, "a", document, path, change)
            self.assertEqual(path.read_bytes(), original)

    def test_rotated_native_pdf_geometry_matches_display(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "rotated.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=500, height=300)
                page.insert_text((60, 90), "Vendor: University accounting office")
                raw_bbox = page.get_text("blocks")[0][:4]
                page.set_rotation(90)
                bbox = pymupdf.Rect(raw_bbox) * page.rotation_matrix
                pdf.save(path)
            model = document_structure.extract_model(path, root / "ocr")
            graph = model["pages"][0]
            self.assertEqual(graph["size"], [300, 500])
            pair = next(node for node in graph["nodes"] if node["kind"] == "key_value")
            self.assertTrue(all(abs(actual - expected) < 0.1 for actual, expected in zip(pair["bbox"], bbox)))

    @unittest.skipUnless(shutil.which("tesseract") and
                         (Path(__file__).resolve().parents[2] / "вводные/12_Акт_связь_0426.png").is_file(),
                         "Requires the sample image and Tesseract")
    def test_sample_scan_exposes_key_value_fields(self):
        path = Path(__file__).resolve().parents[2] / "вводные/12_Акт_связь_0426.png"
        with tempfile.TemporaryDirectory() as directory:
            model = document_structure.extract_model(path, Path(directory))
            page = model["pages"][0]
            with Image.open(path) as image:
                self.assertEqual(page["size"], list(image.size))
            fields = [node for node in page["nodes"] if node["kind"] == "key_value"]
            self.assertTrue(fields)
            self.assertTrue(all(node["value"] and node["value_bbox"] for node in fields))
