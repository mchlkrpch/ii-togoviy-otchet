"""Split a structure field into rectangular regions using source word positions."""

from bisect import bisect_right
from copy import deepcopy
from math import isfinite

from native_structure import decimal_text
from structure_edits import EditError, field_text, field_words, refresh


def section_boundaries(start, end, cuts):
    if any(not isfinite(cut) for cut in cuts):
        raise EditError("Координаты разрезов должны быть конечными числами")
    edges = [start, *sorted(cuts), end]
    if any(right - left < 0.001 for left, right in zip(edges, edges[1:])):
        raise EditError("Разрезы должны быть внутри поля и не совпадать")
    return edges


def point(word):
    x0, y0, x1, y1 = word["bbox"]
    return (x0 + x1) / 2, (y0 + y1) / 2


def words_in_field(path, page, box, cache_dir):
    import numpy as np
    import pymupdf
    from PIL import Image, ImageOps
    from parse_structure import OCR

    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as pdf:
            pdf_page = pdf[page["number"] - 1]
            if pdf_page.rotation:
                pdf_page.remove_rotation()
            native = pdf_page.get_text("words", sort=True)
            if native:
                words = field_words(
                    {"words": [{"text": w[4], "bbox": list(w[:4])} for w in native]},
                    {"bbox": box},
                )
                if words:
                    return words
            dpi = min(360, max(1, int(6000 * 72 / max(page["size"]))))
            scale = dpi / 72
            pixmap = pdf_page.get_pixmap(dpi=dpi, clip=pymupdf.Rect(box), colorspace=pymupdf.csGRAY)
            raster = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width)
            origin = (pixmap.x, pixmap.y)
    else:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((6000, 6000))
            bounds = tuple(round(coordinate) for coordinate in box)
            raster = np.asarray(image.crop(bounds).convert("L"))
            origin = bounds[:2]
            scale = 1
    if min(raster.shape) < 2:
        return []
    words = OCR(cache_dir=cache_dir).read(raster)
    return [
        {"text": word["text"], "bbox": [(value + origin[i % 2]) / scale for i, value in enumerate(word["bbox"])]}
        for word in words
    ]


def prepare_slice(path, page, node, change, serial, cache_dir):
    if not node or not node.get("field_bbox"):
        raise EditError("Выберите поле с собственным прямоугольником")
    x0, y0, x1, y1 = node["field_bbox"]
    xs = section_boundaries(x0, x1, change.x_cuts)
    ys = section_boundaries(y0, y1, change.y_cuts)
    boxes = [[left, top, right, bottom]
             for top, bottom in zip(ys, ys[1:])
             for left, right in zip(xs, xs[1:])]
    if not 2 <= len(boxes) <= 36:
        raise EditError("Добавьте хотя бы один разрез, чтобы получить от 2 до 36 частей")
    if not 0 <= change.parent_index < len(boxes):
        raise EditError("Выберите родительскую часть")

    # Preserve already corrected text on the parent rather than reading it again from the source.
    words = [] if node.get("text_modified") else words_in_field(path, page, node["field_bbox"], cache_dir)
    text_parts = [[] for _ in boxes]
    pair_value = node.get("value_bbox") if node["kind"] == "key_value" else None
    for word in words:
        x, y = point(word)
        if not x0 <= x <= x1 or not y0 <= y <= y1:
            continue
        column = min(bisect_right(xs, x) - 1, len(xs) - 2)
        row = min(bisect_right(ys, y) - 1, len(ys) - 2)
        index = row * (len(xs) - 1) + column
        if index == change.parent_index and pair_value and not (
            pair_value[0] <= x <= pair_value[2] and pair_value[1] <= y <= pair_value[3]
        ):
            continue
        text_parts[index].append(word["text"])
    texts = [" ".join(words) for words in text_parts]
    if not words:
        texts[change.parent_index] = field_text(node)
    return {
        "action": "slice", "page": change.page, "ids": [node["id"]], "serial": serial,
        "boxes": boxes, "texts": texts, "parent_index": change.parent_index,
    }


def intersection(original, box):
    clipped = [max(original[0], box[0]), max(original[1], box[1]),
               min(original[2], box[2]), min(original[3], box[3])]
    return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None


def apply_slice(model, operation):
    page = next((page for page in model["pages"] if page["number"] == operation["page"]), None)
    if page is None:
        raise EditError("Страница не найдена")
    node = next((node for node in page["nodes"] if node["id"] == operation["ids"][0]), None)
    if not node or not node.get("field_bbox"):
        raise EditError("Поле для разреза больше не существует")

    node.setdefault("original", {"text": field_text(node), "bbox": deepcopy(node["field_bbox"])})
    parent_index = operation["parent_index"]
    for index, (box, text) in enumerate(zip(operation["boxes"], operation["texts"])):
        if index == parent_index:
            node.update(bbox=box, field_bbox=box, manual=True, needs_review=True)
            if node["kind"] == "key_value":
                node.update(value=text, raw=f'{node["label"]}: {text}')
                for name in ("key_bbox", "value_bbox"):
                    if name in node:
                        clipped = intersection(node.pop(name), box)
                        if clipped:
                            node[name] = clipped
            elif node["kind"] == "value":
                numeric = decimal_text(text)
                node.update(raw=text, label=text if numeric is None else numeric,
                            value_type="string" if numeric is None else "decimal")
            else:
                node.update(raw=text, label=text or f"Часть {index + 1}")
            continue
        child_id = f'p{page["number"]}/slice{operation["serial"]}-{index + 1}'
        page["nodes"].append({
            "id": child_id, "parent": node["id"], "kind": "text", "children": [],
            "bbox": box, "field_bbox": box, "label": text or f"Часть {index + 1}", "raw": text,
            "color": node["color"], "manual": True, "needs_review": True,
        })
        node["children"].append(child_id)
    refresh(page)
