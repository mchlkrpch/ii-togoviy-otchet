"""Locate the original numeric glyphs, separately from the field label."""
from functools import lru_cache
import io
from pathlib import Path
import re

import pymupdf
from PIL import Image, ImageOps

from .extraction import ocr_image
from .money import decimal_value, find_money


def inside(rect, context):
    x, y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return context[0] - .005 <= x <= context[2] + .005 and context[1] - .005 <= y <= context[3] + .005


@lru_cache(maxsize=32)
def page_words(filename, modified, page):
    path = Path(filename)
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as pdf:
            source = pdf[page - 1]
            if len(source.get_text().strip()) >= 30:
                return [{"text": word[4], "line": (word[5], word[6]),
                         "rect": [word[0] / source.rect.width, word[1] / source.rect.height,
                                  word[2] / source.rect.width, word[3] / source.rect.height]}
                        for word in source.get_text("words", sort=True)]
            pixmap = source.get_pixmap(dpi=250)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
    else:
        with Image.open(path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
    blocks = ocr_image(image)
    return [dict(word, line=index) for index, block in enumerate(blocks) for word in block.get("words", [])]


def numeric_regions(document, path):
    regions = []
    path = Path(path)
    pdf = pymupdf.open(path) if path.suffix.lower() == ".pdf" else None
    try:
        for fact in document.get("facts", {}).values():
            evidence = fact.get("evidence", {})
            context = evidence.get("rect")
            if fact.get("kind") != "money" or not context:
                continue
            try:
                target = decimal_value(fact["value"])
            except ValueError:
                continue
            literals = [raw for raw, value in find_money(evidence.get("text", "")) if decimal_value(value) == target]
            if not literals:
                literals = [match.group() for match in re.finditer(r"(?<![\d/.,])\d+(?:[ \u00a0\u202f]\d{3})*(?![\d/.,])", evidence.get("text", ""))
                            if decimal_value(match.group()) == target]
            if not literals:
                continue
            page = evidence.get("page", 1)
            candidates = []
            if pdf:
                source = pdf[page - 1]
                for literal in literals:
                    for hit in source.search_for(literal):
                        rect = [hit.x0 / source.rect.width, hit.y0 / source.rect.height,
                                hit.x1 / source.rect.width, hit.y1 / source.rect.height]
                        if inside(rect, context):
                            candidates.append((rect, literal, "pdf-glyphs"))
            if not candidates:
                stored_words = [dict(word, line=index) for index, block in enumerate(document.get("blocks", []))
                                if block.get("page", 1) == page for word in block.get("words", [])]
                words = stored_words or page_words(str(path.resolve()), path.stat().st_mtime_ns, page)
                words = [word for word in words if inside(word["rect"], context)]
                normalized = {re.sub(r"\s+", "", literal).replace(",", ".") for literal in literals}
                for start, word in enumerate(words):
                    for length in range(1, min(8, len(words) - start) + 1):
                        group = words[start:start + length]
                        if any(item["line"] != word["line"] for item in group):
                            break
                        text = " ".join(item["text"] for item in group)
                        if re.sub(r"\s+", "", text).replace(",", ".") not in normalized:
                            continue
                        rect = [min(item["rect"][0] for item in group), min(item["rect"][1] for item in group),
                                max(item["rect"][2] for item in group), max(item["rect"][3] for item in group)]
                        candidates.append((rect, text, "word-boxes"))
            if candidates:
                rect, text, method = min(candidates, key=lambda item: abs(item[0][1] - context[1]) + abs(item[0][0] - context[0]))
                if not any(item["evidence"] == evidence and item["rect"] == rect for item in regions):
                    regions.append({"evidence": evidence, "rect": rect, "text": text, "value": fact["value"], "method": method})
        return regions
    finally:
        if pdf:
            pdf.close()
