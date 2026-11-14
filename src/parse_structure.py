"""Recover ruled PDF tables, merged cells and header hierarchies locally.

CLI: python src/parse_structure.py input.pdf -o output.json --debug-dir output/debug
The parser learns labels from the document, not from a dictionary of this form.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from pathlib import Path

import cv2
import numpy as np
import pymupdf


SCHEMA_VERSION = "pdf-structure/2"


def normalize(text):
    """Whitespace/Unicode normalization only; no fuzzy matching or synonyms."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def number(text):
    cleaned = re.sub(r"[\s\u00a0\u202f]", "", text).replace("−", "-")
    if not re.fullmatch(r"[+-]?\d+(?:[,.]\d+)?", cleaned):
        return None
    try:
        return format(Decimal(cleaned.replace(",", ".")), "f")
    except InvalidOperation:
        return None


class OCR:
    def __init__(self, language="rus+eng", cache_dir=None):
        if not shutil.which("tesseract"):
            raise RuntimeError("Install Tesseract and the requested language models first.")
        self.language = language
        self.version = subprocess.run(
            ["tesseract", "--version"], capture_output=True, text=True, check=True,
        ).stdout.splitlines()[0]
        language_info = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        available = language_info[1:]
        missing = set(language.split("+")) - set(available)
        if missing:
            raise RuntimeError(f"Missing Tesseract languages: {sorted(missing)}")
        match = re.search(r'"([^"]+)"', language_info[0])
        model_directory = Path(match[1]) if match else None
        self.model_hashes = {}
        for name in language.split("+"):
            model = model_directory / f"{name}.traineddata" if model_directory else None
            self.model_hashes[name] = hashlib.sha256(model.read_bytes()).hexdigest() if model and model.is_file() else None
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def read(self, image, psm=6, language=None):
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError("Cannot encode OCR image")
        data = encoded.tobytes()
        language = language or self.language
        settings = f"{self.version}|{self.model_hashes}|{language}|psm={psm}|oem=1|spaces=1"
        digest = hashlib.sha256(settings.encode() + data).hexdigest()
        cache = self.cache_dir / f"{digest}.json" if self.cache_dir else None
        if cache and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        command = ["tesseract", "stdin", "stdout", "-l", language,
                   "--oem", "1", "--psm", str(psm),
                   "-c", "preserve_interword_spaces=1", "tsv"]
        environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        result = subprocess.run(command, input=data, capture_output=True,
                                timeout=120, env=environment)
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.returncode or "Failed loading language" in stderr:
            raise RuntimeError(f"Tesseract failed: {stderr.strip()}")
        words = []
        for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
            if not row["text"].strip() or float(row["conf"]) < 0:
                continue
            x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
            words.append({"text": row["text"], "confidence": round(float(row["conf"]), 2),
                          "bbox": [x, y, x + w, y + h],
                          "line": [int(row[k]) for k in ("block_num", "par_num", "line_num")]})
        if cache:
            # Atomic replacement also permits concurrent reads of identical crops.
            atomic_json(cache, words)
        return words


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".structure-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def groups(positions, gap=3):
    positions = np.asarray(positions)
    if not len(positions):
        return []
    return [int(round(group.mean())) for group in
            np.split(positions, np.flatnonzero(np.diff(positions) > gap) + 1)]


def line_masks(gray):
    ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 35, 15)
    height, width = gray.shape
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                 np.ones((1, max(25, width // 32)), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                               np.ones((max(20, height // 50), 1), np.uint8))
    return horizontal, vertical


def detect_grids(gray):
    horizontal, vertical = line_masks(gray)
    joined = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    grids = []
    height, width = gray.shape
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < width * 0.15 or h < height * 0.025 or cv2.contourArea(contour) < w * h * 0.25:
            continue
        xs = [x + p for p in groups(np.flatnonzero(
            (vertical[y:y+h, x:x+w] > 0).sum(axis=0) > h * 0.12))]
        ys = [y + p for p in groups(np.flatnonzero(
            (horizontal[y:y+h, x:x+w] > 0).sum(axis=1) > w * 0.12))]
        if len(xs) < 2 or len(ys) < 3:
            continue
        # A clipped outer vertical border is recoverable from horizontal endpoints.
        assumptions = []
        for edge, position in (("left", x + 2), ("right", x + w - 3)):
            nearest = xs[0] if edge == "left" else xs[-1]
            if abs(nearest - position) > 8:
                xs.append(position)
                assumptions.append(f"inferred_{edge}_border_from_horizontal_lines")
        xs = sorted(xs)
        grids.append({"xs": xs, "ys": ys, "assumptions": assumptions})
    grids.sort(key=lambda g: (g["ys"][0], g["xs"][0]))
    return grids, horizontal, vertical


def recover_cells(xs, ys, horizontal, vertical):
    rows, columns = len(ys) - 1, len(xs) - 1
    parents = list(range(rows * columns))

    def find(item):
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    def join(a, b):
        parents[find(a)] = find(b)

    for row in range(rows):
        for column in range(columns):
            if column + 1 < columns:
                strip = vertical[ys[row]+5:ys[row+1]-5,
                                 max(0, xs[column+1]-5):xs[column+1]+6]
                if strip.size and (strip > 0).any(axis=1).mean() < 0.5:
                    join(row * columns + column, row * columns + column + 1)
            if row + 1 < rows:
                strip = horizontal[max(0, ys[row+1]-5):ys[row+1]+6,
                                   xs[column]+5:xs[column+1]-5]
                if strip.size and (strip > 0).any(axis=0).mean() < 0.5:
                    join(row * columns + column, (row + 1) * columns + column)
    components = defaultdict(list)
    for row in range(rows):
        for column in range(columns):
            components[find(row * columns + column)].append((row, column))
    cells, warnings = [], []
    for tiles in components.values():
        r0, r1 = min(r for r, _ in tiles), max(r for r, _ in tiles) + 1
        c0, c1 = min(c for _, c in tiles), max(c for _, c in tiles) + 1
        if len(tiles) != (r1 - r0) * (c1 - c0):
            warnings.append({"code": "non_rectangular_merge", "tiles": tiles})
            # Do not silently turn a damaged component into overlapping rectangles.
            rectangles = [(r, r+1, c, c+1) for r, c in tiles]
        else:
            rectangles = [(r0, r1, c0, c1)]
        for a, b, c, d in rectangles:
            cells.append({"id": f"r{a+1}c{c+1}", "row": a, "column": c,
                          "row_span": b-a, "column_span": d-c,
                          "bbox_pixels": [xs[c], ys[a], xs[d], ys[b]]})
    return sorted(cells, key=lambda cell: (cell["row"], cell["column"])), warnings


def words_text(words):
    lines = defaultdict(list)
    for word in words:
        lines[tuple(word["line"])].append(word["text"])
    return normalize(" ".join(" ".join(line) for line in lines.values()))


def recognize_cell(cell, gray, ocr, native_words, scale):
    x0, y0, x1, y1 = cell["bbox_pixels"]
    selected = [w for w in native_words
                if x0 <= (w[0]+w[2])/2 * scale < x1
                and y0 <= (w[1]+w[3])/2 * scale < y1]
    if selected:
        selected.sort(key=lambda w: (round(w[1], 1), w[0]))
        return {**cell, "text": normalize(" ".join(w[4] for w in selected)),
                "confidence": None, "method": "pdf-text", "needs_review": False}
    crop = gray[y0+4:y1-3, x0+4:x1-3]
    if not crop.size:
        return {**cell, "text": "", "confidence": None, "method": "empty", "needs_review": True}
    # Blank cells are valid and must remain represented in the grid.
    if (crop < 160).mean() < 0.002:
        return {**cell, "text": "", "confidence": None, "method": "empty", "needs_review": False}
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    crop = cv2.copyMakeBorder(crop, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)
    words = ocr.read(crop)
    text = words_text(words)
    confidence = round(sum(w["confidence"] for w in words) / len(words), 2) if words else 0
    return {**cell, "text": text, "confidence": confidence,
            "method": "ocr-cell", "needs_review": confidence < 75}


def identify_header(cells, row_count, column_count):
    # Many accounting forms have an explicit 1..N column-number strip.
    for row in range(row_count):
        strip = [c for c in cells if c["row"] == row and c["row_span"] == 1]
        hits = sum(c["text"].strip(" .") == str(c["column"]+1) for c in strip)
        if hits >= max(3, column_count * 0.7):
            return row, row+1, row, "column_number_strip"
    # Generic fallback is intentionally reported as an inference.
    for row in range(1, row_count):
        strip = [c for c in cells if c["row"] == row]
        if strip and sum(number(c["text"]) is not None for c in strip) >= max(2, len(strip)*0.5):
            return row, row, None, "numeric_body_heuristic"
    return 0, 0, None, "unresolved"


def refine_numbers(cells, gray, ocr, row_count, column_count, workers):
    """Reread numeric body cells without guessing/correcting their digits."""
    _, data_start, _, method = identify_header(cells, row_count, column_count)
    if method == "unresolved":
        return cells
    numeric_columns = [c["column"] for c in cells
                       if c["row"] >= data_start and number(c["text"]) is not None]
    if not numeric_columns:
        return cells
    first_numeric = min(numeric_columns)

    def refine(cell):
        if (cell["method"] != "ocr-cell" or cell["row"] < data_start
                or cell["column"] < first_numeric or not cell["text"]):
            return cell
        initial = {"text": cell["text"], "value": number(cell["text"]),
                   "confidence": cell["confidence"], "method": f"{ocr.language}/psm6"}
        candidates = [initial]
        x0, y0, x1, y1 = cell["bbox_pixels"]
        crop = gray[y0+4:y1-3, x0+4:x1-3]
        crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        crop = cv2.copyMakeBorder(crop, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        # English is optional if the caller requests a different set of languages.
        language = "eng" if "eng" in ocr.language.split("+") else ocr.language
        for threshold in (False, True):
            variant = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1] if threshold else crop
            words = ocr.read(variant, psm=7, language=language)
            text = words_text(words)
            confidence = round(sum(w["confidence"] for w in words)/len(words), 2) if words else 0
            candidates.append({"text": text, "value": number(text), "confidence": confidence,
                               "method": f"{language}/psm7" + ("/otsu" if threshold else "")})
            if number(text) is not None and confidence >= 85 and initial["value"] == number(text):
                break
        valid = [c for c in candidates if c["value"] is not None]
        if not valid:
            # Isolated margin specks can become ':' before an otherwise clear number.
            # Trim to tall glyphs only as a recorded fallback, never edit recognized digits.
            binary = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
            _, _, stats, _ = cv2.connectedComponentsWithStats(binary)
            glyphs = stats[1:][(stats[1:, cv2.CC_STAT_HEIGHT] > crop.shape[0]*0.3)
                               & (stats[1:, cv2.CC_STAT_WIDTH] < crop.shape[1]*0.5)]
            if len(glyphs):
                left = max(0, int(glyphs[:, 0].min())-6)
                right = min(crop.shape[1], int((glyphs[:, 0]+glyphs[:, 2]).max())+6)
                trimmed = cv2.copyMakeBorder(crop[:, left:right], 20, 20, 20, 20,
                                             cv2.BORDER_CONSTANT, value=255)
                words = ocr.read(trimmed, psm=7, language=language)
                text = words_text(words)
                confidence = round(sum(w["confidence"] for w in words)/len(words), 2) if words else 0
                candidates.append({"text": text, "value": number(text), "confidence": confidence,
                                   "method": f"{language}/psm7/trim-margin-noise"})
                valid = [c for c in candidates if c["value"] is not None]
        if not valid:
            return {**cell, "ocr_candidates": candidates, "needs_review": True}
        counts = {v["value"]: sum(c["value"] == v["value"] for c in valid) for v in valid}
        # Correlated OCR variants can agree on an incorrect digit. Preserve a valid
        # original reading when variants disagree, and expose the conflict for review.
        chosen = initial if initial["value"] is not None else max(valid, key=lambda c: c["confidence"])
        disagreement = len(counts) > 1
        return {**cell, "text": chosen["text"], "confidence": chosen["confidence"],
                "ocr_candidates": candidates,
                "needs_review": disagreement or chosen["confidence"] < 75 or counts[chosen["value"]] < 2}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(refine, cells))


def header_hierarchy(cells, header_rows, column_count):
    headers = [c for c in cells if c["row"] < header_rows]
    parent_of = {}
    for child in headers:
        candidates = [p for p in headers
                      if p["row"] + p["row_span"] <= child["row"]
                      and p["column"] <= child["column"]
                      and p["column"] + p["column_span"] >= child["column"] + child["column_span"]]
        if candidates:
            parent = max(candidates, key=lambda p: (p["row"] + p["row_span"], -p["column_span"]))
            parent_of[child["id"]] = parent["id"]
    nodes = {c["id"]: {"cell_id": c["id"], "label": c["text"],
                       "column_start": c["column"]+1, "column_span": c["column_span"],
                       "needs_review": c["needs_review"], "children": []} for c in headers}
    roots = []
    for cell in headers:
        node = nodes[cell["id"]]
        parent = parent_of.get(cell["id"])
        if parent:
            nodes[parent]["children"].append(node)
        else:
            roots.append(node)
    columns = []
    for column in range(column_count):
        chain = sorted([c for c in headers if c["column"] <= column < c["column"]+c["column_span"]],
                       key=lambda c: c["row"])
        path = [c["text"] for c in chain if c["text"]]
        columns.append({"number": column+1, "header_path": path,
                        "header_cell_ids": [c["id"] for c in chain],
                        "needs_review": any(c["needs_review"] for c in chain)})
    return roots, columns


def structure_table(cells, xs, ys):
    header_rows, data_start, number_row, method = identify_header(cells, len(ys)-1, len(xs)-1)
    tree, columns = header_hierarchy(cells, header_rows, len(xs)-1)
    body = [c for c in cells if c["row"] >= data_start]
    numeric_columns = [c["column"] for c in body if number(c["text"]) is not None]
    row_header_count = min(numeric_columns) if numeric_columns else 1
    for column in columns:
        column["role"] = "row_header" if column["number"] <= row_header_count else "value"
    rows = []
    for r in range(data_start, len(ys)-1):
        covering = [c for c in body if c["row"] <= r < c["row"]+c["row_span"]]
        labels = sorted([c for c in covering if c["column"] < row_header_count], key=lambda c: c["column"])
        row_path = [c["text"] for c in labels if c["text"]]
        values = []
        for cell in covering:
            if cell["column"] < row_header_count:
                continue
            column = columns[cell["column"]]
            parsed = number(cell["text"])
            # Decimal strings preserve precision; callers may use Decimal(value).
            values.append({"column_number": cell["column"]+1, "column_span": cell["column_span"],
                           "column_path": column["header_path"], "cell_id": cell["id"],
                           "raw": cell["text"], "value": parsed if parsed is not None else cell["text"],
                           "value_type": "decimal" if parsed is not None else "string",
                           "needs_review": cell["needs_review"] or column["needs_review"]})
        rows.append({"grid_row": r+1, "row_path": row_path,
                     "row_header_cell_ids": [c["id"] for c in labels], "values": values})
    return {"header_row_count": header_rows, "column_number_grid_row": number_row+1 if number_row is not None else None,
            "header_detection": method, "row_header_column_count": row_header_count,
            "header_tree": tree, "columns": columns, "rows": rows}


def text_blocks(words, offset=(0, 0), scale=1):
    lines = defaultdict(list)
    for word in words:
        lines[tuple(word["line"])].append(word)
    blocks = []
    separated = []
    for line in lines.values():
        # Tesseract may put two distant page columns on the same OCR line.
        line = sorted(line, key=lambda w: w["bbox"][0])
        height = float(np.median([w["bbox"][3]-w["bbox"][1] for w in line]))
        current = []
        for word in line:
            if current and word["bbox"][0]-current[-1]["bbox"][2] > max(40*scale, height*8):
                separated.append(current)
                current = []
            current.append(word)
        if current:
            separated.append(current)
    for line in separated:
        x0, y0 = min(w["bbox"][0] for w in line), min(w["bbox"][1] for w in line)
        x1, y1 = max(w["bbox"][2] for w in line), max(w["bbox"][3] for w in line)
        text = normalize(" ".join(w["text"] for w in line))
        blocks.append({"text": text,
                       "words": [{"text": w["text"], "bbox": [round((v+offset[i % 2])/scale, 2)
                                  for i, v in enumerate(w["bbox"])]} for w in line],
                       "bbox": [round((x0+offset[0])/scale, 2), round((y0+offset[1])/scale, 2),
                                round((x1+offset[0])/scale, 2), round((y1+offset[1])/scale, 2)],
                       "confidence": round(sum(w["confidence"] for w in line)/len(line), 2)})
    return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))


def select_field(document, row_path, column_path, table_id=None):
    """Resolve an exact contextual selector, rejecting missing/ambiguous matches.

    table_id is optional and local to an extraction. For portable references,
    use full header and row paths; never persist the current cell coordinates.
    """
    row_path = [normalize(s) for s in row_path]
    column_path = [normalize(s) for s in column_path]
    matches = []
    for page in document["pages"]:
        for table in page["tables"]:
            if table_id and table["id"] != table_id:
                continue
            for row in table["rows"]:
                if [normalize(s) for s in row["row_path"]] != row_path:
                    continue
                for value in row["values"]:
                    if [normalize(s) for s in value["column_path"]] == column_path:
                        matches.append({**value, "page": page["number"], "table_id": table["id"]})
    if len(matches) != 1:
        raise LookupError(f"Expected one field, found {len(matches)}: row={row_path}, column={column_path}")
    return matches[0]


def parse_raster_page(gray, page_number, size, ocr, *, scale=1, native=(), workers=4,
                      coordinate_system="Image pixels, top-left origin; bbox=[x0,y0,x1,y1]",
                      debug_target=None):
    """Apply the same grid recovery, OCR and text-block logic to scans and photos."""
    grids, horizontal, vertical = detect_grids(gray)
    entry = {"number": page_number, "size_points": size,
             "coordinate_system": coordinate_system,
             "text_source": "pdf-text" if native else "ocr",
             "text_blocks": [], "tables": []}
    preview = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if debug_target else None
    for table_index, grid in enumerate(grids, 1):
        xs, ys = grid["xs"], grid["ys"]
        cells, warnings = recover_cells(xs, ys, horizontal, vertical)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            cells = list(executor.map(lambda c: recognize_cell(c, gray, ocr, native, scale), cells))
        cells = refine_numbers(cells, gray, ocr, len(ys)-1, len(xs)-1, workers)
        table = {"id": f"p{page_number}.t{table_index}",
                 "bbox": [round(p/scale, 2) for p in (xs[0], ys[0], xs[-1], ys[-1])],
                 "grid": {"rows": len(ys)-1, "columns": len(xs)-1,
                          "x": [round(x/scale, 2) for x in xs], "y": [round(y/scale, 2) for y in ys]},
                 "assumptions": grid["assumptions"], "warnings": warnings,
                 **structure_table(cells, xs, ys), "cells": cells}
        if table["header_detection"] != "column_number_strip":
            table["warnings"].append({"code": "header_inferred" if table["header_row_count"] else "header_unresolved"})
        for cell in cells:
            if preview is not None:
                x0, y0, x1, y1 = cell["bbox_pixels"]
                color = (210, 70, 30) if cell["row"] < table["header_row_count"] else (20, 140, 20)
                cv2.rectangle(preview, (x0+2, y0+2), (x1-2, y1-2), color, 2)
                cv2.putText(preview, cell["id"], (x0+5, y0+14), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
            cell["bbox"] = [round(p/scale, 2) for p in cell.pop("bbox_pixels")]
        entry["tables"].append(table)
    # Preserve text around tables rather than discarding titles/requisites/signatures.
    outside = gray.copy()
    for grid in grids:
        outside[max(0, grid["ys"][0]-6):grid["ys"][-1]+6,
                max(0, grid["xs"][0]-6):grid["xs"][-1]+6] = 255
    if native:
        outside_words = []
        for word in native:
            center = ((word[0]+word[2])/2*scale, (word[1]+word[3])/2*scale)
            if any(g["xs"][0] <= center[0] <= g["xs"][-1]
                   and g["ys"][0] <= center[1] <= g["ys"][-1] for g in grids):
                continue
            outside_words.append({"text": word[4], "bbox": list(word[:4]),
                                  "confidence": 100, "line": list(word[5:7])})
        entry["text_blocks"] = text_blocks(outside_words)
    else:
        outside_words = ocr.read(outside, psm=3)
        # Sparse OCR adds isolated signatures missed by automatic page layout.
        sparse = ocr.read(outside, psm=11)
        for word in sparse:
            x0, y0, x1, y1 = word["bbox"]
            area = max(1, (x1-x0)*(y1-y0))
            covered = any(
                max(0, min(x1, w["bbox"][2])-max(x0, w["bbox"][0])) *
                max(0, min(y1, w["bbox"][3])-max(y0, w["bbox"][1])) / area > 0.35
                for w in outside_words)
            if not covered:
                outside_words.append({**word, "line": [100000+word["line"][0], *word["line"][1:]]})
        entry["text_blocks"] = text_blocks(outside_words, scale=scale)
    if not grids:
        entry["warnings"] = ["No ruled table detected; only text was extracted."]
    if debug_target and not cv2.imwrite(str(debug_target), preview):
        raise OSError("Could not save debug image")
    return entry


def structure_document(source, *, language, dpi=None):
    source = Path(source)
    return {"schema_version": SCHEMA_VERSION,
            "source": {"name": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
            "parser": {"dpi": dpi, "language": language, "pymupdf": pymupdf.VersionBind,
                       "opencv": cv2.__version__, "numpy": np.__version__, "ocr": None,
                       "ocr_model_sha256": {}},
            "pages": [], "warnings": []}


def parse_pdf(source, dpi=360, language="rus+eng", workers=4, cache_dir=None, debug_dir=None):
    from native_structure import parse_native_page
    from structure_semantics import enrich_document
    document = structure_document(source, language=language, dpi=dpi)
    ocr = None
    if debug_dir:
        debug_dir = Path(debug_dir)
        debug_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(source) as pdf:
        if pdf.needs_pass:
            raise ValueError("Password-protected PDF is not supported")
        for index, page in enumerate(pdf, 1):
            print(f"Parsing page {index}/{len(pdf)}...", file=sys.stderr)
            if page.rotation:
                # Use display coordinates for words, vector tables and raster overlays alike.
                page.remove_rotation()
            if len(page.get_text().strip()) >= 30:
                document["pages"].append(parse_native_page(page, index))
                continue
            if ocr is None:
                ocr = OCR(language, cache_dir)
                document["parser"].update(ocr=ocr.version, ocr_model_sha256=ocr.model_hashes)
            # Bound raster dimensions for uncommon oversized scan pages.
            page_dpi = min(dpi, max(1, int(6000 * 72 / max(page.rect.width, page.rect.height))))
            pixmap = page.get_pixmap(dpi=page_dpi, colorspace=pymupdf.csGRAY, alpha=False)
            gray = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width)
            document["pages"].append(parse_raster_page(
                gray, index, [round(page.rect.width, 2), round(page.rect.height, 2)], ocr,
                scale=page_dpi/72, native=page.get_text("words", sort=True), workers=workers,
                coordinate_system="PDF points, top-left origin; bbox=[x0,y0,x1,y1]",
                debug_target=debug_dir / f"page-{index}-cells.png" if debug_dir else None,
            ))
    return enrich_document(document)


def parse_image(source, language="rus+eng", workers=4, cache_dir=None):
    from PIL import Image, ImageOps
    from structure_semantics import enrich_document
    document = structure_document(source, language=language)
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        # Keep the same displayed orientation and aspect ratio as the preview.
        image.thumbnail((6000, 6000))
        gray = np.asarray(image.convert("L"))
    ocr = OCR(language, cache_dir)
    document["parser"].update(ocr=ocr.version, ocr_model_sha256=ocr.model_hashes)
    document["pages"].append(parse_raster_page(gray, 1, list(image.size), ocr, workers=workers))
    return enrich_document(document)


def format_tree(document):
    lines = [document["source"]["name"]]
    for page in document["pages"]:
        lines.append(f"  Страница {page['number']} ({page['text_source']})")
        for pair in page.get("key_value_fields", []):
            lines.append(f"    Реквизит: {pair['key']} → {pair['value']}")
        for block in page["text_blocks"]:
            lines.append(f"    Текст: {block['text']}")
        for table in page["tables"]:
            lines.append(f"    Таблица {table['id']}: {table['grid']['columns']} граф, {len(table['rows'])} строк данных")

            def visit(nodes, depth):
                for node in nodes:
                    start, span = node["column_start"], node["column_span"]
                    scope = str(start) if span == 1 else f"{start}-{start+span-1}"
                    lines.append("      " + "  "*depth + f"[{scope}] {node['label'] or '(пусто)'}")
                    visit(node["children"], depth+1)

            visit(table["header_tree"], 0)
            for row in table["rows"]:
                lines.append("      Строка: " + " / ".join(row["row_path"]))
                if row.get("hierarchy_path"):
                    lines.append("        Горизонтальная иерархия: " + " → ".join(row["hierarchy_path"]))
                for value in row["values"]:
                    mark = " [проверить OCR]" if value["needs_review"] else ""
                    lines.append(f"        Графа {value['column_number']}: {value['value']}{mark}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--tree", type=Path, help="Also save a readable UTF-8 tree")
    parser.add_argument("--debug-dir", type=Path, help="Save detected cell outlines as PNG")
    parser.add_argument("--cache-dir", type=Path, help="Cache OCR crops for repeatable reruns")
    parser.add_argument("--dpi", type=int, default=360)
    parser.add_argument("--language", default="rus+eng")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error("Input PDF does not exist")
    if args.output.resolve() == args.source.resolve():
        parser.error("Output must not overwrite the input PDF")
    if args.tree and args.tree.resolve() in (args.source.resolve(), args.output.resolve()):
        parser.error("Tree, JSON and input must have different paths")
    if not 100 <= args.dpi <= 600 or not 1 <= args.workers <= 16:
        parser.error("DPI must be 100..600 and workers 1..16")
    try:
        document = parse_pdf(args.source, args.dpi, args.language, args.workers,
                             args.cache_dir, args.debug_dir)
        atomic_json(args.output, document)
        if args.tree:
            args.tree.parent.mkdir(parents=True, exist_ok=True)
            args.tree.write_text(format_tree(document), encoding="utf-8")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, pymupdf.FileDataError) as exc:
        parser.exit(1, f"Parsing failed: {exc}\n")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
