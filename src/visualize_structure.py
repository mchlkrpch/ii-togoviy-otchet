"""Generate an offline PDF structure viewer and open it in the default browser."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import webbrowser
from pathlib import Path

import pymupdf


PALETTE = ["#0f766e", "#475569", "#7c3aed", "#b45309", "#2563eb",
           "#c026d3", "#0891b2", "#e11d48", "#4d7c0f"]


def union_box(boxes):
    boxes = list(boxes)
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def build_model(document):
    """Make explicit group extents, source fields, hierarchy and row projections."""
    pages = []
    for page in document["pages"]:
        nodes, roots, row_roots = [], [], []
        prefix = f"p{page['number']}"
        facts = {f["id"]: f for f in document.get("facts", [])}
        pairs = {f["block_index"]: f for f in page.get("key_value_fields", [])}
        if pairs:
            metadata_id = f"{prefix}/metadata"
            nodes.append({"id": metadata_id, "kind": "metadata", "label": "Реквизиты · ключ → значение",
                          "bbox": union_box(f["bbox"] for f in pairs.values()), "color": "#0f766e",
                          "parent": None, "children": [f["id"] for f in pairs.values()]})
            roots.append(metadata_id)
        for table in page["tables"]:
            cells = {c["id"]: c for c in table["cells"]}
            node_by_cell = {}
            value_by_cell = {v["cell_id"]: (row, v) for row in table["rows"] for v in row["values"]}
            row_by_cell = {cid: row for row in table["rows"] for cid in row["row_header_cell_ids"]}
            body_start = table["header_row_count"] + bool(table["column_number_grid_row"])

            def add_value(cell, parent, color, path):
                row, value = value_by_cell.get(cell["id"], (row_by_cell.get(cell["id"], {}), {}))
                node = {"id": f"{table['id']}/{cell['id']}", "kind": "value", "parent": parent,
                        "label": value.get("value", cell["text"]), "raw": cell["text"],
                        "value_type": value.get("value_type", "string"),
                        "row_path": row.get("row_path", []), "column_path": path,
                        "bbox": cell["bbox"], "field_bbox": cell["bbox"], "color": color,
                        "cell_id": cell["id"], "column": cell["column"]+1,
                        "row_span": cell["row_span"], "column_span": cell["column_span"],
                        "needs_review": value.get("needs_review", cell["needs_review"]),
                        "confidence": cell["confidence"], "alternatives": cell.get("ocr_candidates", []),
                        "children": []}
                fact = facts.get(value.get("fact_id"))
                if fact:
                    context = fact["row_context"]
                    node.update({"metric": fact["metric"], "unit": fact["unit"],
                                 "row_context": context, "row_hierarchy": fact["row_hierarchy"],
                                 "column_header_bboxes": [cells[c]["bbox"] for c in fact["source"]["column_header_cell_ids"]],
                                 "column_header_node_ids": [f"{table['id']}/{c}" for c in fact["source"]["column_header_cell_ids"]],
                                 "row_header_node_ids": [f"{table['id']}/{c}" for c in row["row_header_cell_ids"]],
                                 "row_header_bboxes": [cells[c]["bbox"] for c in row["row_header_cell_ids"]],
                                 "row_bbox": union_box([cells[c]["bbox"] for c in row["row_header_cell_ids"]]
                                                        + [cells[v["cell_id"]]["bbox"] for v in row["values"]]),
                                 "selector": {"column_path": path, **context}})
                nodes.append(node)
                node_by_cell[cell["id"]] = node["id"]
                return node["id"]

            def visit(header, parent, color, depth, path):
                cell = cells[header["cell_id"]]
                start = header["column_start"]-1
                end = start+header["column_span"]
                body = [c for c in table["cells"] if c["row"] >= body_start and start <= c["column"] < end]
                path = [*path, header["label"]]
                node = {"id": f"{table['id']}/{cell['id']}", "kind": "header", "parent": parent,
                        "label": header["label"] or "Пустой заголовок", "column_path": path,
                        "bbox": union_box([cell["bbox"], *[c["bbox"] for c in body]]),
                        "field_bbox": cell["bbox"], "color": color, "depth": depth,
                        "column": start+1, "column_span": end-start, "cell_id": cell["id"],
                        "confidence": cell["confidence"], "needs_review": header["needs_review"], "children": []}
                nodes.append(node)
                if header["children"]:
                    node["children"] = [visit(child, node["id"], color, depth+1, path) for child in header["children"]]
                else:
                    node["children"] = [add_value(c, node["id"], color, path) for c in body
                                        if c["id"] not in node_by_cell]
                return node["id"]

            table_node = {"id": table["id"], "kind": "table", "label": f"Таблица · {len(table['columns'])} граф · {len(table['rows'])} строки",
                          "bbox": table["bbox"], "color": "#475569", "parent": None, "children": []}
            nodes.append(table_node)
            roots.append(table_node["id"])
            for index, header in enumerate(table["header_tree"]):
                table_node["children"].append(visit(header, table_node["id"], PALETTE[index % len(PALETTE)], 1, []))
            # When a header is unresolved, preserve every body field in the viewer.
            for cell in table["cells"]:
                if cell["row"] >= body_start and cell["id"] not in node_by_cell:
                    table_node["children"].append(add_value(cell, table_node["id"], "#475569", []))
            for row in table["rows"]:
                ids = list(dict.fromkeys([*row["row_header_cell_ids"], *[v["cell_id"] for v in row["values"]]]))
                row_roots.append({"label": " / ".join(row["row_path"]) or f"Строка {row['grid_row']}",
                                  "hierarchy_path": row.get("hierarchy_path", row["row_path"]),
                                  "children": [node_by_cell[cid] for cid in ids if cid in node_by_cell]})
        for i, block in enumerate(page["text_blocks"]):
            if i in pairs:
                pair = pairs[i]
                nodes.append({"id": pair["id"], "kind": "key_value", "label": pair["label"],
                              "key": pair["key"], "value": pair["value"], "raw": block["text"],
                              "column_path": [pair["label"]], "bbox": pair["bbox"],
                              "field_bbox": pair["bbox"], "key_bbox": pair["key_bbox"],
                              "value_bbox": pair["value_bbox"], "color": "#0f766e",
                              "parent": metadata_id, "confidence": block.get("confidence"),
                              "needs_review": pair["needs_review"], "children": []})
                continue
            nodes.append({"id": f"{prefix}/text{i}", "kind": "text", "label": block["text"],
                          "bbox": block["bbox"], "field_bbox": block["bbox"], "color": "#64748b",
                          "confidence": block.get("confidence"), "parent": None, "children": []})
        numeric_fields = page.get("numeric_fields", [])
        if numeric_fields:
            group_id = f"{prefix}/numbers"
            nodes.append({"id":group_id,"kind":"metadata","label":"Числовые поля текста",
                          "bbox":union_box(n["bbox"] for n in numeric_fields),"color":"#2563eb",
                          "parent":None,"children":[n["id"] for n in numeric_fields]})
            roots.append(group_id)
            for field in numeric_fields:
                nodes.append({"id":field["id"],"kind":"value","label":field["value"],
                              "raw":field["raw"],"value_type":"decimal","bbox":field["bbox"],
                              "field_bbox":field["bbox"],"color":"#2563eb","parent":group_id,
                              "row_path":[field["context"]],"column_path":["Числовой показатель текста"],
                              "needs_review":False,"children":[]})
        pages.append({"number": page["number"], "size": page["size_points"], "nodes": nodes,
                      "roots": roots, "row_roots": row_roots,
                      "warnings": [w for t in page["tables"] for w in [*t["assumptions"], *t["warnings"]]]})
    return {"name": document["source"]["name"], "sha256": document["source"]["sha256"], "pages": pages}


def create_viewer(source, document, destination, dpi=180):
    source, destination = Path(source), Path(destination)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != document["source"]["sha256"]:
        raise ValueError("JSON belongs to a different PDF (SHA-256 mismatch). Reparse the source.")
    if destination.resolve() == source.resolve():
        raise ValueError("Viewer must not overwrite the source PDF")
    model = build_model(document)
    with pymupdf.open(source) as pdf:
        if len(pdf) != len(model["pages"]):
            raise ValueError("Page count differs between PDF and JSON")
        for page, entry in zip(pdf, model["pages"]):
            native_words=page.get_text('words',sort=False)
            if native_words:
                entry['words']=[{'text':w[4],'bbox':[round(v,3) for v in w[:4]]} for w in native_words]
                entry['word_source']='pdf-text'
            else:
                import numpy as np
                from parse_structure import OCR
                pix=page.get_pixmap(dpi=300,colorspace=pymupdf.csGRAY,alpha=False)
                gray=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width)
                words=OCR(cache_dir=Path('tmp/pdfs/ocr-cache')).read(gray,psm=11)
                entry['words']=[{'text':w['text'],'bbox':[round(v/(300/72),3) for v in w['bbox']]} for w in words]
                entry['word_source']='tesseract'
            entry["image"] = "data:image/png;base64," + base64.b64encode(
                page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")).decode("ascii")
    baseline={**model,'pages':[{k:v for k,v in entry.items() if k!='image'} for entry in model['pages']]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    baseline_path=destination.with_suffix('.model.json')
    temporary=baseline_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(baseline,ensure_ascii=False),encoding='utf-8')
    temporary.replace(baseline_path)
    template = Path(__file__).with_name("structure_viewer.html").read_text(encoding="utf-8")
    # PDF labels are untrusted data, never markup or executable script.
    payload = json.dumps(model, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = template.replace("__DOCUMENT_DATA__", payload)
    editor_script=Path(__file__).with_name('structure_editor.js').read_text(encoding='utf-8')
    html=html.replace('</body>', '<script>'+editor_script.replace('</script','<\\/script')+'</script></body>')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--structure", type=Path, help="Existing parse_structure.py JSON; otherwise parse the PDF")
    parser.add_argument("-o", "--output", type=Path, default=Path("output/pdf/structure-viewer.html"))
    parser.add_argument("--dpi", type=int, default=180, help="Display rendering resolution, 72..300")
    parser.add_argument("--no-open", action="store_true", help="Generate the HTML without opening a browser")
    args = parser.parse_args()
    if not 72 <= args.dpi <= 300:
        parser.error("DPI must be 72..300")
    try:
        if args.structure:
            if args.output.resolve() == args.structure.resolve():
                parser.error("Viewer must not overwrite the structure JSON")
            document = json.loads(args.structure.read_text(encoding="utf-8"))
        else:
            from parse_structure import parse_pdf
            document = parse_pdf(args.source, cache_dir=Path("tmp/pdfs/ocr-cache"))
        destination = create_viewer(args.source, document, args.output, args.dpi)
    except (OSError, ValueError, KeyError, RuntimeError, pymupdf.FileDataError) as exc:
        parser.exit(1, f"Cannot create viewer: {exc}\n")
    print(f"Viewer: {destination.resolve()}")
    if not args.no_open and not webbrowser.open(destination.resolve().as_uri()):
        print("Open the HTML file in a browser manually.", file=sys.stderr)


if __name__ == "__main__":
    main()
