import base64
import copy
import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from PIL import Image
import pymupdf
from backend.edited_files import edited_file
from backend.engine import calculate, validate_change
from backend.extraction import add_fact, extract_document, docx_blocks
from backend.rich_document import (
    NS,
    W,
    snapshot_from_docx,
    docx_bytes,
    normalize_snapshot,
    document_blocks,
    text_content,
)
from backend.rich_export import html_document, pdf_bytes
from test_verification import example_case


class RichDocumentTests(unittest.TestCase):
    def test_portable_pdf_and_html_exports_preserve_cyrillic_table_and_page_break(self):
        with tempfile.TemporaryDirectory() as directory:
            path, document = self.source(directory)
            snapshot = snapshot_from_docx(path, document)
            snapshot["settings"].update(
                header="Проверка отчета", footer="Страница", footerPageNumber=True
            )
            image_buffer = io.BytesIO()
            Image.new("RGB", (20, 10), "red").save(image_buffer, format="PNG")
            snapshot["content"]["content"].append(
                {
                    "type": "image",
                    "attrs": {
                        "src": "data:image/png;base64,"
                        + base64.b64encode(image_buffer.getvalue()).decode(),
                        "width": 20,
                        "height": 10,
                    },
                }
            )
            snapshot["content"]["content"].extend(
                [
                    {"type": "pageBreak"},
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "Вторая страница. Число <42> & данные",
                            }
                        ],
                    },
                ]
            )
            exported_html = html_document(snapshot)
            self.assertIn("Число &lt;42&gt; &amp; данные", exported_html)
            self.assertIn("<table>", exported_html)
            self.assertIn('class="page-break"', exported_html)
            with pymupdf.open(stream=pdf_bytes(snapshot), filetype="pdf") as exported:
                self.assertEqual(len(exported), 2)
                self.assertIn("Стоимость услуг", exported[0].get_text())
                self.assertIn("Описание", exported[0].get_text())
                self.assertTrue(exported[0].get_images())
                self.assertNotIn("Вторая страница", exported[0].get_text())
                self.assertIn("Вторая страница", exported[1].get_text())
                self.assertIn("Страница 2", exported[1].get_text())

    def source(self, directory):
        path = Path(directory) / "act.docx"
        xml = f"""<w:document xmlns:w="{W}"><w:body>
        <w:p><w:r><w:rPr><w:b/><w:rFonts w:ascii="Arial"/><w:sz w:val="28"/></w:rPr><w:t>Акт № 1</w:t></w:r></w:p>
        <w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>Договор: 24/03/15-01</w:t></w:r></w:p>
        <w:p><w:r><w:t>Дата: 15.01.2026</w:t></w:r></w:p>
        <w:p><w:r><w:t>Эксплуатация лабораторного оборудования</w:t></w:r></w:p>
        <w:p><w:r><w:t>Стоимость услуг: </w:t></w:r><w:r><w:rPr><w:b/><w:color w:val="112233"/></w:rPr><w:t>20,00</w:t></w:r><w:r><w:t> руб.</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Услуга</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Описание</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>
        </w:body></w:document>"""
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", xml)
        document = extract_document(path)
        evidence = {"text": "24/03/15-01", "locator": "Реквизиты", "confidence": 100}
        add_fact(document, "period", "Период", "2026-01", evidence, "text")
        document["category"] = "0800"
        return path, document

    def test_rich_import_preserves_marks_table_alignment_and_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path, document = self.source(directory)
            snapshot = snapshot_from_docx(path, document)
            first = snapshot["content"]["content"][0]
            self.assertEqual(first["attrs"]["auditId"], "Абзац 1")
            marks = first["content"][0]["marks"]
            self.assertIn({"type": "bold"}, marks)
            self.assertTrue(
                any(mark.get("attrs", {}).get("fontSize") == "14pt" for mark in marks)
            )
            self.assertEqual(
                snapshot["content"]["content"][1]["attrs"]["textAlign"], "center"
            )
            self.assertEqual(
                snapshot["content"]["content"][5]["content"][0]["attrs"]["auditId"],
                "Таблица 1, строка 1",
            )
            self.assertEqual(snapshot["settings"]["margins"]["left"], 25.4)

    def test_rich_edit_changes_financial_source_and_deleted_identity_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path, document = self.source(directory)
            case = example_case()
            case["documents"][3] = document
            snapshot = snapshot_from_docx(path, document)
            amount = snapshot["content"]["content"][4]
            amount["content"][1]["text"] = "30,00"
            candidate = validate_change(
                case, "rich_document", document["id"], {"snapshot": snapshot}
            )
            result = calculate(candidate)
            self.assertEqual(result["nodes"]["r2026_c15"]["expected"], "30.00")
            effective = next(
                item for item in result["documents"] if item["id"] == document["id"]
            )
            self.assertEqual(
                effective["facts"]["amount"]["evidence"]["locator"], "Абзац 5"
            )
            moved = snapshot["content"]["content"].pop(4)
            snapshot["content"]["content"].insert(0, moved)
            candidate = validate_change(
                case, "rich_document", document["id"], {"snapshot": snapshot}
            )
            self.assertEqual(
                calculate(candidate)["nodes"]["r2026_c15"]["expected"], "30.00"
            )
            snapshot["content"]["content"].remove(moved)
            candidate = validate_change(
                case, "rich_document", document["id"], {"snapshot": snapshot}
            )
            self.assertIsNone(calculate(candidate)["nodes"]["r2026_c15"]["expected"])

    def test_docx_export_roundtrips_formatting_image_lists_page_layout_and_keeps_original(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            path, document = self.source(directory)
            original = path.read_bytes()
            snapshot = snapshot_from_docx(path, document)
            buffer = io.BytesIO()
            Image.new("RGB", (20, 10), "red").save(buffer, format="PNG")
            image = (
                "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
            )
            snapshot["settings"].update(
                orientation="landscape",
                header="Верхний колонтитул",
                footer="Страница ",
                footerPageNumber=True,
            )
            snapshot["content"]["content"].extend(
                [
                    {
                        "type": "orderedList",
                        "attrs": {"start": 3},
                        "content": [
                            {
                                "type": "listItem",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {"type": "text", "text": "Пункт списка"}
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "type": "image",
                        "attrs": {
                            "src": image,
                            "width": 120,
                            "height": 60,
                            "alt": "Проверка",
                        },
                    },
                    {"type": "pageBreak"},
                    {
                        "type": "paragraph",
                        "attrs": {
                            "textAlign": "right",
                            "indent": 10,
                            "lineHeight": "1.5",
                        },
                        "content": [
                            {
                                "type": "text",
                                "text": "Ссылка",
                                "marks": [
                                    {
                                        "type": "link",
                                        "attrs": {"href": "https://example.com"},
                                    },
                                    {
                                        "type": "highlight",
                                        "attrs": {"color": "#ffeeaa"},
                                    },
                                ],
                            }
                        ],
                    },
                ]
            )
            output = docx_bytes(path, snapshot)
            with ZipFile(io.BytesIO(output)) as archive:
                body = ET.fromstring(archive.read("word/document.xml"))
                size = body.find("w:body/w:sectPr/w:pgSz", NS)
                self.assertGreater(
                    int(size.get("{" + W + "}w")), int(size.get("{" + W + "}h"))
                )
                self.assertTrue(body.findall(".//w:numPr", NS))
                self.assertTrue(body.findall(".//w:drawing", NS))
                self.assertTrue(
                    any(
                        node.get("{" + W + "}type") == "page"
                        for node in body.findall(".//w:br", NS)
                    )
                )
                self.assertTrue(body.findall(".//w:hyperlink", NS))
                self.assertIn("word/audit-header.xml", archive.namelist())
                self.assertIn("word/audit-footer.xml", archive.namelist())
                footer = ET.fromstring(archive.read("word/audit-footer.xml"))
                self.assertTrue(
                    any(
                        "PAGE" in (node.text or "")
                        for node in footer.findall(".//w:instrText", NS)
                    )
                )
                self.assertIn("[Content_Types].xml", archive.namelist())
            self.assertEqual(path.read_bytes(), original)
            exported = Path(directory) / "edited.docx"
            exported.write_bytes(output)
            restored = snapshot_from_docx(exported, extract_document(exported))
            self.assertTrue(
                any(node["type"] == "image" for node in restored["content"]["content"])
            )
            self.assertEqual(restored["settings"]["header"], "Верхний колонтитул")

    def test_rich_snapshot_supersedes_old_text_and_fact_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            path, document = self.source(directory)
            case = example_case()
            case["documents"][3] = document
            old = validate_change(
                case,
                "text",
                document["id"] + ":Абзац 5",
                {"value": "Стоимость услуг: 40,00 руб."},
            )
            effective = next(
                item
                for item in calculate(old)["documents"]
                if item["id"] == document["id"]
            )
            snapshot = snapshot_from_docx(path, effective)
            self.assertIn("40,00", text_content(snapshot["content"]))
            amount = snapshot["content"]["content"][4]
            amount["content"] = [
                {"type": "text", "text": "Стоимость услуг: 50,00 руб."}
            ]
            candidate = validate_change(
                old, "rich_document", document["id"], {"snapshot": snapshot}
            )
            self.assertNotIn(
                "text:" + document["id"] + ":Абзац 5", candidate["overrides"]
            )
            self.assertEqual(
                calculate(candidate)["nodes"]["r2026_c15"]["expected"], "50.00"
            )

    def test_invalid_links_images_and_duplicate_identities_are_rejected_or_invalidated(
        self,
    ):
        snapshot = {
            "content": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "attrs": {"auditId": "Абзац 1"},
                        "content": [
                            {
                                "type": "text",
                                "text": "x",
                                "marks": [
                                    {
                                        "type": "link",
                                        "attrs": {"href": "javascript:alert(1)"},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            "settings": {},
        }
        with self.assertRaises(ValueError):
            normalize_snapshot(snapshot)
        snapshot["content"]["content"][0]["content"][0].pop("marks")
        snapshot["content"]["content"].append(
            copy.deepcopy(snapshot["content"]["content"][0])
        )
        normalized = normalize_snapshot(snapshot)
        identities = [
            node["attrs"]["auditId"] for node in normalized["content"]["content"]
        ]
        self.assertEqual(len(set(identities)), 2)
        self.assertNotIn("Абзац 1", identities)

    def test_native_footnotes_and_equations_survive_export_with_visible_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path, document = self.source(directory)
            with ZipFile(path) as archive:
                parts = {name: archive.read(name) for name in archive.namelist()}
            root = ET.fromstring(parts["word/document.xml"])
            paragraph = root.find("w:body/w:p", NS)
            run = ET.SubElement(paragraph, "{" + W + "}r")
            ET.SubElement(run, "{" + W + "}footnoteReference", {"{" + W + "}id": "5"})
            math = ET.SubElement(
                paragraph,
                "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath",
            )
            ET.SubElement(
                math, "{http://schemas.openxmlformats.org/officeDocument/2006/math}r"
            )
            parts["word/document.xml"] = ET.tostring(root)
            parts["word/footnotes.xml"] = (
                f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="5"><w:p><w:r><w:t>Текст сноски</w:t></w:r></w:p></w:footnote></w:footnotes>'.encode()
            )
            with ZipFile(path, "w") as archive:
                for name, data in parts.items():
                    archive.writestr(name, data)
            snapshot = snapshot_from_docx(path, document)
            self.assertEqual(len(snapshot["warnings"]), 2)
            output = docx_bytes(path, snapshot)
            with ZipFile(io.BytesIO(output)) as archive:
                restored = ET.fromstring(archive.read("word/document.xml"))
                self.assertTrue(restored.findall(".//w:footnoteReference", NS))
                self.assertTrue(
                    restored.findall(
                        ".//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath"
                    )
                )
                self.assertEqual(
                    archive.read("word/footnotes.xml"), parts["word/footnotes.xml"]
                )
