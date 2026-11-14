"""DOCX and Tiptap interchange with stable provenance for financial checks."""

import base64
import copy
import io
import json
import math
import re
from difflib import SequenceMatcher
from collections import Counter
from pathlib import PurePosixPath
from urllib.parse import urlparse
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

from PIL import Image

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W, "r": R, "a": A, "wp": WP, "pic": PIC}
ET.register_namespace("w", W)
ET.register_namespace("r", R)
ET.register_namespace("a", A)
ET.register_namespace("wp", WP)
ET.register_namespace("pic", PIC)
MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_SETTINGS = {
    "pageSize": "A4",
    "orientation": "portrait",
    "margins": {"top": 25.4, "right": 25.4, "bottom": 25.4, "left": 25.4},
    "viewMode": "paged",
}


def tag(name):
    return "{" + W + "}" + name


def value(element, name, default=None):
    child = element.find("w:" + name, NS) if element is not None else None
    return child.get(tag("val"), default) if child is not None else default


def enabled(properties, name):
    child = properties.find("w:" + name, NS) if properties is not None else None
    return child is not None and child.get(tag("val"), "1") not in ("0", "false", "off")


def add(parent, name, **attributes):
    return ET.SubElement(
        parent,
        tag(name),
        {
            tag(key): str(value)
            for key, value in attributes.items()
            if value is not None
        },
    )


def number(value, default=0):
    try:
        result = float(str(value).replace("pt", "").replace("px", "").replace("mm", ""))
    except (ValueError, TypeError):
        return default
    return result if math.isfinite(result) else default


def color(value, default=None):
    if value is None:
        return default
    text = str(value).strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", text):
        return text.lstrip("#").upper()
    match = re.fullmatch(
        r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,[^)]+)?\)", text
    )
    if match:
        return "".join(f"{min(255, int(part)):02X}" for part in match.groups())
    return default


def safe_link(href):
    return (
        isinstance(href, str)
        and len(href) <= 8192
        and (
            href.startswith("#")
            or urlparse(href).scheme.lower() in ("http", "https", "mailto", "tel")
        )
    )


def decode_image(source):
    match = re.fullmatch(
        r"data:image/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=\r\n]+)", source or ""
    )
    if not match or len(match[2]) > MAX_IMAGE_BYTES * 1.4:
        raise ValueError(
            "Используйте локальное изображение PNG, JPEG, GIF или WebP размером до 8 МБ"
        )
    try:
        data = base64.b64decode(match[2], validate=True)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Изображение превышает 8 МБ")
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Изображение превышает 40 миллионов пикселей")
            image.verify()
    except (OSError, ValueError) as exc:
        raise ValueError("Не удалось прочитать изображение") from exc
    extension = "jpg" if match[1] in ("jpeg", "jpg") else match[1]
    return data, extension


def text_content(node):
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind in ("hardBreak", "pageBreak"):
        return "\n"
    if kind == "image":
        return ""
    return "".join(text_content(child) for child in node.get("content", []))


def document_blocks(content):
    blocks = []

    def collect(node, inside_table=False):
        kind = node.get("type")
        attrs = node.get("attrs") or {}
        if kind == "tableRow":
            cells = [text_content(cell).strip() for cell in node.get("content", [])]
            locator = attrs.get("auditId")
            if locator:
                blocks.append(
                    {
                        "locator": locator,
                        "text": " | ".join(cells),
                        "method": "docx",
                        "confidence": 100,
                        "manual": True,
                    }
                )
            return
        if kind in ("paragraph", "heading") and not inside_table:
            locator = attrs.get("auditId")
            if locator:
                blocks.append(
                    {
                        "locator": locator,
                        "text": text_content(node),
                        "method": "docx",
                        "confidence": 100,
                        "manual": True,
                    }
                )
            return
        for child in node.get("content", []):
            collect(child, inside_table or kind == "table")

    collect(content)
    return blocks


def normalize_snapshot(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) - {
        "content",
        "settings",
        "name",
        "warnings",
    }:
        raise ValueError("Документ должен содержать content и settings")
    if len(json.dumps(snapshot, ensure_ascii=False)) > 24_000_000:
        raise ValueError("Документ превышает 24 МБ")
    result = copy.deepcopy(snapshot)
    if "warnings" in result and (
        not isinstance(result["warnings"], list)
        or any(
            not isinstance(item, str) or len(item) > 2000 for item in result["warnings"]
        )
    ):
        raise ValueError("Некорректные предупреждения документа")
    content = result.get("content")
    if not isinstance(content, dict) or content.get("type") != "doc":
        raise ValueError("Некорректная структура документа")
    settings = dict(DEFAULT_SETTINGS, **(result.get("settings") or {}))
    if settings.get("pageSize") not in ("A4", "Letter", "A5", "Legal") or settings.get(
        "orientation"
    ) not in ("portrait", "landscape"):
        raise ValueError("Неизвестный размер или ориентация страницы")
    margins = dict(DEFAULT_SETTINGS["margins"], **settings.get("margins", {}))
    if any(
        not 0 <= number(margins[key], -1) <= 80
        for key in ("top", "right", "bottom", "left")
    ):
        raise ValueError("Поля страницы должны быть от 0 до 80 мм")
    settings["margins"] = {
        key: number(margins[key]) for key in ("top", "right", "bottom", "left")
    }
    for key in ("header", "footer"):
        if key in settings and (
            not isinstance(settings[key], str) or len(settings[key]) > 10000
        ):
            raise ValueError("Колонтитул превышает 10 000 символов")
    result["settings"] = settings
    allowed = {
        "doc",
        "paragraph",
        "text",
        "heading",
        "bulletList",
        "orderedList",
        "listItem",
        "taskList",
        "taskItem",
        "blockquote",
        "codeBlock",
        "horizontalRule",
        "hardBreak",
        "pageBreak",
        "image",
        "table",
        "tableRow",
        "tableCell",
        "tableHeader",
    }
    marks = {
        "bold",
        "italic",
        "underline",
        "strike",
        "subscript",
        "superscript",
        "textStyle",
        "highlight",
        "link",
        "code",
    }
    count = 0
    identities = []
    image_bytes = 0

    def check(node, depth=0):
        nonlocal count, image_bytes
        count += 1
        if (
            count > 100000
            or depth > 50
            or not isinstance(node, dict)
            or node.get("type") not in allowed
        ):
            raise ValueError("Неподдерживаемый или слишком большой элемент документа")
        if "text" in node and (
            not isinstance(node["text"], str) or len(node["text"]) > 200000
        ):
            raise ValueError("Текстовый фрагмент превышает допустимую длину")
        attrs = (
            node.setdefault("attrs", {})
            if node["type"] != "text"
            else node.get("attrs", {})
        )
        if not isinstance(attrs, dict):
            raise ValueError("Некорректные параметры элемента")
        if node["type"] in ("paragraph", "heading", "tableRow"):
            identity = attrs.get("auditId")
            if not isinstance(identity, str) or len(identity) > 200:
                identity = "added_" + uuid4().hex
                attrs["auditId"] = identity
            identities.append(node)
        if node["type"] == "heading" and attrs.get("level", 1) not in range(1, 7):
            raise ValueError("Уровень заголовка должен быть от 1 до 6")
        if node["type"] in ("tableCell", "tableHeader"):
            if (
                not 1 <= number(attrs.get("colspan", 1), -1) <= 100
                or not 1 <= number(attrs.get("rowspan", 1), -1) <= 100
            ):
                raise ValueError("Недопустимое объединение ячеек")
        if node["type"] == "image":
            data, _ = decode_image(attrs.get("src"))
            image_bytes += len(data)
            if image_bytes > 16 * 1024 * 1024:
                raise ValueError("Изображения документа превышают 16 МБ")
        for mark in node.get("marks", []):
            if not isinstance(mark, dict) or mark.get("type") not in marks:
                raise ValueError("Неподдерживаемое форматирование текста")
            if mark["type"] == "link" and not safe_link(
                (mark.get("attrs") or {}).get("href")
            ):
                raise ValueError("Недопустимая ссылка")
        children = node.get("content", [])
        if not isinstance(children, list):
            raise ValueError("Некорректный список элементов")
        for child in children:
            check(child, depth + 1)

    check(content)
    counts = Counter(node["attrs"]["auditId"] for node in identities)
    for node in identities:
        if counts[node["attrs"]["auditId"]] > 1:
            node["attrs"]["auditId"] = "added_" + uuid4().hex
    return result


class ZipParts:
    def __init__(self, parts):
        self.parts = parts

    def namelist(self):
        return self.parts.keys()

    def read(self, name):
        return self.parts[name]


class DocxReader:
    def __init__(self, archive):
        self.archive = archive
        self.relationships = {}
        if "word/_rels/document.xml.rels" in archive.namelist():
            for relation in ET.fromstring(archive.read("word/_rels/document.xml.rels")):
                self.relationships[relation.get("Id")] = relation.attrib
        self.styles = {}
        self.default_properties = {}
        if "word/styles.xml" in archive.namelist():
            styles = ET.fromstring(archive.read("word/styles.xml"))
            self.default_properties = {
                name: styles.find("w:docDefaults/w:" + name + "Default/w:" + name, NS)
                for name in ("rPr", "pPr")
            }
            for style in styles.findall("w:style", NS):
                self.styles[style.get(tag("styleId"))] = style
        self.numbering = {}
        if "word/numbering.xml" in archive.namelist():
            root = ET.fromstring(archive.read("word/numbering.xml"))
            abstract = {
                item.get(tag("abstractNumId")): item
                for item in root.findall("w:abstractNum", NS)
            }
            for item in root.findall("w:num", NS):
                self.numbering[item.get(tag("numId"))] = abstract.get(
                    value(item, "abstractNumId")
                )

    def style_properties(self, style_id, name, visited=None):
        root = visited is None
        visited = set() if visited is None else visited
        if not style_id or style_id in visited or style_id not in self.styles:
            return [self.default_properties.get(name)] if root else []
        visited.add(style_id)
        style = self.styles[style_id]
        result = self.style_properties(value(style, "basedOn"), name, visited) + [
            style.find("w:" + name, NS)
        ]
        return [self.default_properties.get(name)] + result if root else result

    def run_marks(self, properties):
        result = []
        if properties is None:
            return result
        for xml_name, mark in (("b", "bold"), ("i", "italic"), ("strike", "strike")):
            if enabled(properties, xml_name):
                result.append({"type": mark})
        if value(properties, "u") not in (None, "none", "0"):
            result.append({"type": "underline"})
        vertical = value(properties, "vertAlign")
        if vertical in ("subscript", "superscript"):
            result.append({"type": vertical})
        attrs = {}
        fonts = properties.find("w:rFonts", NS)
        if fonts is not None:
            attrs["fontFamily"] = fonts.get(
                tag("ascii"), fonts.get(tag("hAnsi"), "Arial")
            )
        if value(properties, "sz"):
            attrs["fontSize"] = (
                str(number(value(properties, "sz")) / 2).rstrip("0").rstrip(".") + "pt"
            )
        font_color = color(value(properties, "color"))
        if font_color:
            attrs["color"] = "#" + font_color
        if attrs:
            result.append({"type": "textStyle", "attrs": attrs})
        shade = properties.find("w:shd", NS)
        background = color(shade.get(tag("fill"))) if shade is not None else None
        highlight = value(properties, "highlight")
        named_colors = {
            "yellow": "#ffff00",
            "green": "#00ff00",
            "cyan": "#00ffff",
            "magenta": "#ff00ff",
            "blue": "#0000ff",
            "red": "#ff0000",
            "darkYellow": "#808000",
        }
        if background or highlight in named_colors:
            result.append(
                {
                    "type": "highlight",
                    "attrs": {
                        "color": (
                            "#" + background if background else named_colors[highlight]
                        )
                    },
                }
            )
        return result

    def inline(self, paragraph, inherited_marks=None):
        pieces = []
        for element in paragraph:
            local = element.tag.rsplit("}", 1)[-1]
            if local == "hyperlink":
                relationship = self.relationships.get(element.get("{" + R + "}id"), {})
                href = relationship.get("Target") or (
                    "#" + element.get(tag("anchor"))
                    if element.get(tag("anchor"))
                    else ""
                )
                marks = list(inherited_marks or [])
                if safe_link(href):
                    marks.append({"type": "link", "attrs": {"href": href}})
                pieces.extend(self.inline(element, marks))
            elif local == "r":
                run_properties = element.find("w:rPr", NS)
                marks = list(inherited_marks or []) + self.run_marks(run_properties)
                merged = {}
                for mark in marks:
                    previous = merged.get(mark["type"], {})
                    merged[mark["type"]] = (
                        dict(
                            mark,
                            attrs=dict(
                                previous.get("attrs", {}), **mark.get("attrs", {})
                            ),
                        )
                        if mark.get("attrs")
                        else mark
                    )
                if run_properties is not None:
                    for xml_name, mark in (
                        ("b", "bold"),
                        ("i", "italic"),
                        ("strike", "strike"),
                    ):
                        if run_properties.find(
                            "w:" + xml_name, NS
                        ) is not None and not enabled(run_properties, xml_name):
                            merged.pop(mark, None)
                    if value(run_properties, "u") in ("none", "0"):
                        merged.pop("underline", None)
                for child in element:
                    kind = child.tag.rsplit("}", 1)[-1]
                    if kind in ("t", "instrText"):
                        text = child.text or ""
                        if text:
                            piece = {"type": "text", "text": text}
                            if merged:
                                piece["marks"] = list(merged.values())
                            pieces.append(piece)
                    elif kind == "tab":
                        pieces.append({"type": "text", "text": "\t"})
                    elif kind == "br":
                        pieces.append(
                            {
                                "type": (
                                    "pageBreak"
                                    if child.get(tag("type")) == "page"
                                    else "hardBreak"
                                )
                            }
                        )
                    elif kind == "drawing":
                        image = self.image(child)
                        if image:
                            pieces.append(image)
            elif local in ("ins", "smartTag", "sdt"):
                pieces.extend(self.inline(element, inherited_marks))
        return pieces

    def image(self, drawing):
        blip = drawing.find(".//a:blip", NS)
        if blip is None:
            return None
        relation = self.relationships.get(blip.get("{" + R + "}embed"), {})
        target = relation.get("Target", "")
        path = str(PurePosixPath("word") / target)
        while "/../" in path:
            parts = path.split("/")
            index = parts.index("..")
            parts[index - 1 : index + 1] = []
            path = "/".join(parts)
        if path not in self.archive.namelist():
            return None
        data = self.archive.read(path)
        suffix = PurePosixPath(path).suffix.lower().lstrip(".")
        mime = {
            "jpg": "jpeg",
            "jpeg": "jpeg",
            "png": "png",
            "gif": "gif",
            "webp": "webp",
        }.get(suffix)
        if not mime or len(data) > MAX_IMAGE_BYTES:
            return None
        extent = drawing.find(".//wp:extent", NS)
        attrs = {
            "src": "data:image/" + mime + ";base64," + base64.b64encode(data).decode(),
            "alt": "",
        }
        if extent is not None:
            attrs.update(
                width=round(number(extent.get("cx")) / 9525),
                height=round(number(extent.get("cy")) / 9525),
            )
        doc = drawing.find(".//wp:docPr", NS)
        if doc is not None:
            attrs["alt"] = doc.get("descr", "")
            attrs["title"] = doc.get("name", "")
        return {"type": "image", "attrs": attrs}

    def paragraph(self, element, identity=None):
        properties = element.find("w:pPr", NS)
        style_id = value(properties, "pStyle")
        inherited = self.style_properties(style_id, "pPr") + [properties]
        attrs = {"auditId": identity} if identity else {}
        heading = re.search(r"(?:Heading|Заголовок)\s*([1-6])", style_id or "", re.I)
        for source in inherited:
            if source is None:
                continue
            alignment = value(source, "jc")
            if alignment:
                attrs["textAlign"] = {"both": "justify"}.get(alignment, alignment)
            spacing = source.find("w:spacing", NS)
            if spacing is not None:
                if spacing.get(tag("before")):
                    attrs["spacingBefore"] = number(spacing.get(tag("before"))) / 20
                if spacing.get(tag("after")):
                    attrs["spacingAfter"] = number(spacing.get(tag("after"))) / 20
                if spacing.get(tag("line")):
                    rule = spacing.get(tag("lineRule"), "auto")
                    attrs["lineHeight"] = str(
                        number(spacing.get(tag("line")))
                        / (240 if rule == "auto" else 20)
                    ) + ("" if rule == "auto" else "pt")
            indent = source.find("w:ind", NS)
            if indent is not None:
                attrs["indent"] = number(indent.get(tag("left"), 0)) * 25.4 / 1440
        marks = []
        for source in self.style_properties(style_id, "rPr"):
            marks.extend(self.run_marks(source))
        content = self.inline(element, marks)
        node = {
            "type": "heading" if heading else "paragraph",
            "attrs": attrs,
            "content": content,
        }
        if heading:
            attrs["level"] = int(heading[1])
        return node

    def table(self, element, table_index):
        rows = []
        vertical = {}
        for row_index, source_row in enumerate(element.findall("w:tr", NS), 1):
            identity = f"Таблица {table_index}, строка {row_index}"
            row = {"type": "tableRow", "attrs": {"auditId": identity}, "content": []}
            column = 0
            header = source_row.find("w:trPr/w:tblHeader", NS) is not None
            for source in source_row.findall("w:tc", NS):
                properties = source.find("w:tcPr", NS)
                span = max(1, int(number(value(properties, "gridSpan", 1), 1)))
                merge = (
                    properties.find("w:vMerge", NS) if properties is not None else None
                )
                if (
                    merge is not None
                    and merge.get(tag("val")) != "restart"
                    and column in vertical
                ):
                    vertical[column]["attrs"]["rowspan"] += 1
                    column += span
                    continue
                attrs = {
                    "colspan": span,
                    "rowspan": 1,
                    "auditId": identity + "#" + str(column),
                }
                width = properties.find("w:tcW", NS) if properties is not None else None
                if width is not None and width.get(tag("type")) == "dxa":
                    attrs["colwidth"] = [
                        round(number(width.get(tag("w"))) / 15 / span)
                    ] * span
                shade = properties.find("w:shd", NS) if properties is not None else None
                fill = color(shade.get(tag("fill"))) if shade is not None else None
                if fill:
                    attrs["backgroundColor"] = "#" + fill
                align = value(properties, "vAlign")
                if align:
                    attrs["verticalAlign"] = align
                paragraphs = [
                    piece
                    for p in source.findall("w:p", NS)
                    for piece in split_inline_blocks(self.paragraph(p))
                ]
                cell = {
                    "type": "tableHeader" if header else "tableCell",
                    "attrs": attrs,
                    "content": paragraphs or [{"type": "paragraph"}],
                }
                row["content"].append(cell)
                if merge is not None:
                    for slot in range(column, column + span):
                        vertical[slot] = cell
                else:
                    for slot in range(column, column + span):
                        vertical.pop(slot, None)
                column += span
            rows.append(row)
        return {"type": "table", "content": rows}

    def list_kind(self, paragraph):
        properties = paragraph.find("w:pPr", NS)
        numbering = properties.find("w:numPr", NS) if properties is not None else None
        if numbering is None:
            return None
        identifier = value(numbering, "numId")
        level = value(numbering, "ilvl", "0")
        definition = self.numbering.get(identifier)
        if definition is None:
            return "bulletList"
        source = definition.find(f"w:lvl[@w:ilvl='{level}']", NS)
        return "bulletList" if value(source, "numFmt") == "bullet" else "orderedList"


def native_warnings(root):
    local_names = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
    warnings = []
    if local_names & {"footnoteReference", "endnoteReference"}:
        warnings.append(
            "Документ содержит сноски. Они сохраняются в DOCX, а их расположение после правки абзаца может измениться."
        )
    if local_names & {"oMath", "oMathPara"}:
        warnings.append(
            "Документ содержит формулы Word. Исходные формулы сохраняются в DOCX, редактирование формул в браузере пока ограничено."
        )
    if local_names & {"ins", "del", "moveFrom", "moveTo"}:
        warnings.append(
            "Документ содержит исправления Word. В редактор загружен принятый текст, история исправлений доступна в исходном файле."
        )
    if local_names & {"pict", "object", "txbxContent", "altChunk"}:
        warnings.append(
            "Документ содержит текстовые поля или встроенные объекты. Они сохраняются в DOCX, расположение после редактирования может измениться."
        )
    if local_names & {"chart", "diagram", "SmartArt"}:
        warnings.append(
            "В документе есть диаграммы или SmartArt. Исходные объекты сохраняются в DOCX и требуют настольного редактора для изменения."
        )
    elif any(
        element.find(".//a:blip", NS) is None
        for element in root.findall(".//w:drawing", NS)
    ):
        warnings.append(
            "Документ содержит сложные графические объекты. Они сохраняются в DOCX, их просмотр в браузере ограничен."
        )
    return warnings


def part_text(root):
    pieces = []
    in_field = False
    for node in root.iter():
        if node.tag == tag("fldChar"):
            in_field = node.get(tag("fldCharType")) != "end"
        elif node.tag == tag("t") and not in_field:
            pieces.append(node.text or "")
    return "".join(pieces)


def opaque_inline(paragraph, reader):
    chunks = []
    for child in paragraph:
        local = child.tag.rsplit("}", 1)[-1]
        if local == "r":
            for item in child:
                item_name = item.tag.rsplit("}", 1)[-1]
                if (
                    item_name
                    in ("footnoteReference", "endnoteReference", "object", "pict")
                    or item_name == "drawing"
                    and reader.image(item) is None
                ):
                    run = ET.Element(tag("r"))
                    run.append(copy.deepcopy(item))
                    chunks.append(run)
        elif local in ("oMath", "oMathPara", "fldSimple"):
            chunks.append(copy.deepcopy(child))
    return chunks


def snapshot_from_docx(path, document):
    saved = document.get("rich_document_snapshot")
    if saved:
        return overlay_edits(copy.deepcopy(saved), document)
    with ZipFile(path) as archive:
        reader = DocxReader(archive)
        root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find("w:body", NS)
        if body is None:
            raise ValueError("В DOCX отсутствует содержимое документа")
        content = []
        paragraph_index = table_index = 0
        current_list = None
        for element in body:
            if element.tag == tag("p"):
                paragraph_index += 1
                paragraph = reader.paragraph(element, f"Абзац {paragraph_index}")
                pieces = split_inline_blocks(paragraph)
                kind = reader.list_kind(element)
                if kind:
                    if current_list is None or current_list["type"] != kind:
                        current_list = {"type": kind, "content": []}
                        content.append(current_list)
                    current_list["content"].append(
                        {"type": "listItem", "content": pieces}
                    )
                else:
                    current_list = None
                    if enabled(element.find("w:pPr", NS), "pageBreakBefore"):
                        content.append({"type": "pageBreak"})
                    content.extend(pieces)
            elif element.tag == tag("tbl"):
                table_index += 1
                current_list = None
                content.append(reader.table(element, table_index))
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        section = body.find("w:sectPr", NS)
        if section is not None:
            size = section.find("w:pgSz", NS)
            if size is not None:
                width, height = number(size.get(tag("w")), 11906), number(
                    size.get(tag("h")), 16838
                )
                settings["orientation"] = "landscape" if width > height else "portrait"
                short, long = sorted((width, height))
                if abs(short - 12240) < 100 and abs(long - 15840) < 100:
                    settings["pageSize"] = "Letter"
            margins = section.find("w:pgMar", NS)
            if margins is not None:
                settings["margins"] = {
                    side: round(number(margins.get(tag(side)), 1440) * 25.4 / 1440, 2)
                    for side in ("top", "right", "bottom", "left")
                }
            for kind in ("header", "footer"):
                reference = section.find("w:" + kind + "Reference", NS)
                if reference is None:
                    continue
                relation = reader.relationships.get(reference.get("{" + R + "}id"), {})
                target = str(PurePosixPath("word") / relation.get("Target", ""))
                if target in archive.namelist():
                    part = ET.fromstring(archive.read(target))
                    settings[kind] = part_text(part)
                    if kind == "footer":
                        settings["footerPageNumber"] = any(
                            "PAGE" in (child.text or "")
                            for child in part.findall(".//w:instrText", NS)
                        )
        snapshot = {
            "content": {"type": "doc", "content": content or [{"type": "paragraph"}]},
            "settings": settings,
            "warnings": native_warnings(root),
        }
    # Old field edits remain visible on first rich-editor opening.
    return normalize_snapshot(overlay_edits(snapshot, document))


def replace_text(node, new_text):
    leaves = [child for child in walk(node) if child.get("type") == "text"]
    old_text = "".join(child.get("text", "") for child in leaves)
    if old_text == new_text:
        return
    if not leaves:
        node["content"] = [{"type": "text", "text": new_text}] if new_text else []
        return
    positions = []
    offset = 0
    for leaf in leaves:
        positions.append((leaf, offset, offset + len(leaf["text"])))
        offset += len(leaf["text"])
    for kind, start, end, replacement_start, replacement_end in reversed(
        SequenceMatcher(None, old_text, new_text).get_opcodes()
    ):
        if kind == "equal":
            continue
        insertion = new_text[replacement_start:replacement_end]
        target = next(
            (leaf for leaf, left, right in positions if left <= start < right),
            positions[-1][0],
        )
        for leaf, left, right in positions:
            local_start = max(0, start - left)
            local_end = min(len(leaf["text"]), end - left)
            if local_start < local_end:
                leaf["text"] = leaf["text"][:local_start] + leaf["text"][local_end:]
            if leaf is target:
                leaf["text"] = (
                    leaf["text"][:local_start] + insertion + leaf["text"][local_start:]
                )
    for parent in walk(node):
        if "content" in parent:
            parent["content"] = [
                child
                for child in parent["content"]
                if child.get("type") != "text" or child.get("text")
            ]


def overlay_edits(snapshot, document):
    edits = {
        block["locator"]: block["text"]
        for block in document["blocks"]
        if block.get("manual") and not block.get("deleted")
    }
    for node in walk(snapshot["content"]):
        identity = (node.get("attrs") or {}).get("auditId")
        if identity not in edits:
            continue
        if node.get("type") in ("paragraph", "heading"):
            replace_text(node, edits[identity])
        elif node.get("type") == "tableRow":
            for cell, text in zip(
                node.get("content", []), edits[identity].split(" | ")
            ):
                replace_text(cell, text)
    return snapshot


def split_inline_blocks(paragraph):
    pieces = []
    text = []
    for child in paragraph.get("content", []):
        if child["type"] not in ("image", "pageBreak"):
            text.append(child)
            continue
        if text:
            pieces.append(
                dict(paragraph, attrs=dict(paragraph.get("attrs") or {}), content=text)
            )
            text = []
        pieces.append(child)
    if text or not pieces:
        pieces.append(
            dict(paragraph, attrs=dict(paragraph.get("attrs") or {}), content=text)
        )
    paragraphs = [node for node in pieces if node["type"] in ("paragraph", "heading")]
    if len(paragraphs) > 1:
        main = max(paragraphs, key=lambda node: len(text_content(node)))
        for node in paragraphs:
            if node is not main:
                node["attrs"]["auditId"] = "added_" + uuid4().hex
    return pieces


def walk(node):
    yield node
    for child in node.get("content", []):
        yield from walk(child)


def apply_snapshot(document, snapshot):
    original = {block["locator"]: block for block in document["blocks"]}
    blocks = document_blocks(snapshot["content"])
    current = {block["locator"]: block for block in blocks}
    changed = {
        identity
        for identity in original.keys() | current.keys()
        if original.get(identity, {}).get("text")
        != current.get(identity, {}).get("text")
    }
    for block in blocks:
        previous = original.get(block["locator"])
        if previous:
            block["original_text"] = previous.get("original_text", previous["text"])
    for identity in original.keys() - current.keys():
        blocks.append(
            dict(
                original[identity],
                text="",
                deleted=True,
                manual=True,
                original_text=original[identity]["text"],
            )
        )
    document["blocks"] = blocks
    document["text"] = "\n".join(
        block["text"] for block in blocks if not block.get("deleted")
    )
    document["rich_document_snapshot"] = copy.deepcopy(snapshot)
    document["edited"] = True
    from .extraction import extract_facts

    candidate = copy.deepcopy(document)
    candidate["facts"] = {}
    candidate["errors"] = []
    extract_facts(candidate)
    if candidate.get("category"):
        document["category"] = candidate["category"]
        document["category_reason"] = candidate.get("category_reason", "")
    for key, fact in document["facts"].items():
        locator = fact["evidence"].get("locator")
        if locator not in changed:
            continue
        replacement = candidate["facts"].get(key)
        fact.update(original_value=fact["value"], manual=True)
        if replacement and replacement["evidence"].get("locator") == locator:
            fact.update(
                value=replacement["value"],
                evidence=replacement["evidence"],
                needs_review=replacement["needs_review"],
            )
        else:
            fact.update(
                value=None,
                needs_review=True,
                error="Исходный фрагмент изменен или удален",
            )
    return changed


class DocxWriter:
    def __init__(self, path, snapshot):
        with ZipFile(path) as source:
            self.parts = {
                entry.filename: source.read(entry.filename)
                for entry in source.infolist()
            }
        self.snapshot = snapshot
        self.root = ET.fromstring(self.parts["word/document.xml"])
        self.root.attrib.pop(
            "{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable",
            None,
        )
        self.body = self.root.find("w:body", NS)
        if self.body is None:
            raise ValueError("В DOCX отсутствует содержимое документа")
        self.opaque = {}
        self.opaque_blocks = []
        source_reader = DocxReader(ZipParts(self.parts))
        paragraph_index = table_index = 0
        for element in self.body:
            if element.tag == tag("p"):
                paragraph_index += 1
                chunks = opaque_inline(element, source_reader)
                if chunks:
                    self.opaque[f"Абзац {paragraph_index}"] = chunks
            elif element.tag == tag("tbl"):
                table_index += 1
                for row_index, row in enumerate(element.findall("w:tr", NS), 1):
                    column = 0
                    for cell in row.findall("w:tc", NS):
                        chunks = [
                            chunk
                            for paragraph in cell.findall("w:p", NS)
                            for chunk in opaque_inline(paragraph, source_reader)
                        ]
                        if chunks:
                            self.opaque[
                                f"Таблица {table_index}, строка {row_index}#{column}"
                            ] = chunks
                        column += max(
                            1,
                            int(
                                number(value(cell.find("w:tcPr", NS), "gridSpan", 1), 1)
                            ),
                        )
            elif element.tag not in (tag("tbl"), tag("sectPr")):
                self.opaque_blocks.append(copy.deepcopy(element))
        section = self.body.find("w:sectPr", NS)
        self.section = (
            copy.deepcopy(section) if section is not None else ET.Element(tag("sectPr"))
        )
        self.body.clear()
        self.relationships = ET.fromstring(
            self.parts.get(
                "word/_rels/document.xml.rels", f'<Relationships xmlns="{REL}"/>'
            )
        )
        self.content_types = ET.fromstring(
            self.parts.get("[Content_Types].xml", f'<Types xmlns="{CT}"/>')
        )
        self.numbering = ET.fromstring(
            self.parts.get("word/numbering.xml", f'<w:numbering xmlns:w="{W}"/>')
        )
        self.styles = ET.fromstring(
            self.parts.get("word/styles.xml", f'<w:styles xmlns:w="{W}"/>')
        )
        self.styles.attrib.pop(
            "{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable",
            None,
        )
        self.numbering.attrib.pop(
            "{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable",
            None,
        )
        self.next_drawing = 10000
        self.abstract_id = (
            max(
                [0]
                + [
                    int(node.get(tag("abstractNumId"), 0))
                    for node in self.numbering.findall("w:abstractNum", NS)
                ]
            )
            + 1
        )
        self.number_id = (
            max(
                [0]
                + [
                    int(node.get(tag("numId"), 0))
                    for node in self.numbering.findall("w:num", NS)
                ]
            )
            + 1
        )

    def relationship(self, target, kind, external=False):
        identifier = "audit" + uuid4().hex
        attributes = {"Id": identifier, "Target": target, "Type": R + "/" + kind}
        if external:
            attributes["TargetMode"] = "External"
        ET.SubElement(self.relationships, "{" + REL + "}Relationship", attributes)
        return identifier

    def content_type(self, name, mime, default=False):
        field = "Extension" if default else "PartName"
        kind = "Default" if default else "Override"
        if not any(node.get(field) == name for node in self.content_types):
            ET.SubElement(
                self.content_types,
                "{" + CT + "}" + kind,
                {field: name, "ContentType": mime},
            )

    def paragraph_properties(self, paragraph, node):
        attrs = node.get("attrs") or {}
        properties = add(paragraph, "pPr")
        if node["type"] == "heading":
            level = min(6, max(1, int(attrs.get("level", 1))))
            identifier = "Heading" + str(level)
            add(properties, "pStyle", val=identifier)
            if not any(item.get(tag("styleId")) == identifier for item in self.styles):
                style = add(self.styles, "style", type="paragraph", styleId=identifier)
                add(style, "name", val="heading " + str(level))
                add(style, "basedOn", val="Normal")
                add(add(style, "pPr"), "outlineLvl", val=level - 1)
                run_style = add(style, "rPr")
                add(run_style, "b")
                add(run_style, "sz", val=max(24, 40 - level * 4))
        alignment = attrs.get("textAlign")
        if alignment in ("left", "right", "center", "justify"):
            add(properties, "jc", val="both" if alignment == "justify" else alignment)
        indent = number(attrs.get("indent"))
        if indent:
            add(properties, "ind", left=round(max(-30, min(200, indent)) * 1440 / 25.4))
        spacing = {}
        for name, attribute in (("before", "spacingBefore"), ("after", "spacingAfter")):
            if attribute in attrs:
                spacing[name] = round(max(0, min(500, number(attrs[attribute]))) * 20)
        line_height = attrs.get("lineHeight")
        if line_height:
            if "pt" in str(line_height) or "px" in str(line_height):
                points = number(line_height) * (0.75 if "px" in str(line_height) else 1)
                spacing.update(
                    line=round(max(1, min(500, points)) * 20), lineRule="atLeast"
                )
            else:
                spacing.update(
                    line=round(max(0.5, min(10, number(line_height, 1.15))) * 240),
                    lineRule="auto",
                )
        if spacing:
            add(properties, "spacing", **spacing)
        return properties

    def run(self, paragraph, node, prefix=""):
        marks = node.get("marks", [])
        link = next((mark for mark in marks if mark["type"] == "link"), None)
        target = paragraph
        if link:
            href = link.get("attrs", {}).get("href", "")
            if href.startswith("#"):
                target = add(paragraph, "hyperlink", anchor=href[1:])
            elif safe_link(href):
                target = ET.SubElement(
                    paragraph,
                    tag("hyperlink"),
                    {"{" + R + "}id": self.relationship(href, "hyperlink", True)},
                )
        run = add(target, "r")
        properties = add(run, "rPr")
        for mark in marks:
            kind, attrs = mark["type"], mark.get("attrs") or {}
            if kind in ("bold", "italic", "strike"):
                add(properties, {"bold": "b", "italic": "i", "strike": "strike"}[kind])
            elif kind == "underline":
                add(properties, "u", val="single")
            elif kind in ("subscript", "superscript"):
                add(properties, "vertAlign", val=kind)
            elif kind == "textStyle":
                if attrs.get("fontFamily"):
                    family = str(attrs["fontFamily"]).split(",")[0].strip(" \"'")[:200]
                    add(properties, "rFonts", ascii=family, hAnsi=family, cs=family)
                if attrs.get("fontSize"):
                    size = number(attrs["fontSize"], 11) * (
                        0.75 if "px" in str(attrs["fontSize"]) else 1
                    )
                    add(properties, "sz", val=round(max(1, min(400, size)) * 2))
                foreground = color(attrs.get("color"))
                if foreground:
                    add(properties, "color", val=foreground)
                background = color(attrs.get("backgroundColor"))
                if background:
                    add(properties, "shd", val="clear", fill=background)
            elif kind == "highlight":
                background = color(attrs.get("color"), "FFFF00")
                add(properties, "shd", val="clear", fill=background)
            elif kind == "code":
                add(properties, "rFonts", ascii="Courier New", hAnsi="Courier New")
                add(properties, "shd", val="clear", fill="F1F3F4")
            elif kind == "link":
                add(properties, "color", val="1155CC")
                add(properties, "u", val="single")
        text = prefix + node.get("text", "")
        fragments = re.split(r"(\t|\n)", text)
        for fragment in fragments:
            if fragment == "\t":
                add(run, "tab")
            elif fragment == "\n":
                add(run, "br")
            elif fragment:
                child = add(run, "t")
                child.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                child.text = fragment

    def image(self, paragraph, node):
        attrs = node.get("attrs") or {}
        data, extension = decode_image(attrs.get("src"))
        name = "audit-image-" + uuid4().hex + "." + extension
        self.parts["word/media/" + name] = data
        mime = "image/jpeg" if extension == "jpg" else "image/" + extension
        self.content_type(extension, mime, True)
        identifier = self.relationship("media/" + name, "image")
        with Image.open(io.BytesIO(data)) as source:
            image_width, image_height = source.size
        width = max(1, min(1800, number(attrs.get("width"), min(image_width, 600))))
        height = max(
            1,
            min(2400, number(attrs.get("height"), image_height * width / image_width)),
        )
        cx, cy = round(width * 9525), round(height * 9525)
        run = add(paragraph, "r")
        drawing = add(run, "drawing")
        inline = ET.SubElement(
            drawing,
            "{" + WP + "}inline",
            {"distT": "0", "distB": "0", "distL": "0", "distR": "0"},
        )
        ET.SubElement(inline, "{" + WP + "}extent", {"cx": str(cx), "cy": str(cy)})
        self.next_drawing += 1
        ET.SubElement(
            inline,
            "{" + WP + "}docPr",
            {
                "id": str(self.next_drawing),
                "name": str(attrs.get("title") or name)[:200],
                "descr": str(attrs.get("alt") or "")[:2000],
            },
        )
        graphic = ET.SubElement(inline, "{" + A + "}graphic")
        graphic_data = ET.SubElement(graphic, "{" + A + "}graphicData", {"uri": PIC})
        picture = ET.SubElement(graphic_data, "{" + PIC + "}pic")
        non_visual = ET.SubElement(picture, "{" + PIC + "}nvPicPr")
        ET.SubElement(non_visual, "{" + PIC + "}cNvPr", {"id": "0", "name": name})
        ET.SubElement(non_visual, "{" + PIC + "}cNvPicPr")
        fill = ET.SubElement(picture, "{" + PIC + "}blipFill")
        ET.SubElement(fill, "{" + A + "}blip", {"{" + R + "}embed": identifier})
        ET.SubElement(ET.SubElement(fill, "{" + A + "}stretch"), "{" + A + "}fillRect")
        shape = ET.SubElement(picture, "{" + PIC + "}spPr")
        transform = ET.SubElement(shape, "{" + A + "}xfrm")
        ET.SubElement(transform, "{" + A + "}off", {"x": "0", "y": "0"})
        ET.SubElement(transform, "{" + A + "}ext", {"cx": str(cx), "cy": str(cy)})
        ET.SubElement(
            ET.SubElement(shape, "{" + A + "}prstGeom", {"prst": "rect"}),
            "{" + A + "}avLst",
        )

    def inline(self, paragraph, children):
        for child in children:
            kind = child["type"]
            if kind == "text":
                self.run(paragraph, child)
            elif kind in ("hardBreak", "pageBreak"):
                add(
                    add(paragraph, "r"),
                    "br",
                    type="page" if kind == "pageBreak" else None,
                )
            elif kind == "image":
                self.image(paragraph, child)
            else:
                self.inline(paragraph, child.get("content", []))

    def list_number(self, kind, start=1):
        abstract_id, number_id = self.abstract_id, self.number_id
        self.abstract_id += 1
        self.number_id += 1
        definition = add(self.numbering, "abstractNum", abstractNumId=abstract_id)
        add(definition, "multiLevelType", val="multilevel")
        for level in range(9):
            entry = add(definition, "lvl", ilvl=level)
            add(entry, "start", val=max(1, int(start)))
            add(entry, "numFmt", val="bullet" if kind == "bulletList" else "decimal")
            add(
                entry,
                "lvlText",
                val="•" if kind == "bulletList" else "%" + str(level + 1) + ".",
            )
            add(entry, "lvlJc", val="left")
            properties = add(entry, "pPr")
            add(properties, "ind", left=720 * (level + 1), hanging=360)
        instance = add(self.numbering, "num", numId=number_id)
        add(instance, "abstractNumId", val=abstract_id)
        return number_id

    def blocks(self, parent, nodes, level=0, list_number=None, task=None):
        for node in nodes:
            kind = node["type"]
            if kind in ("paragraph", "heading", "codeBlock"):
                paragraph = add(parent, "p")
                properties = self.paragraph_properties(paragraph, node)
                if list_number is not None:
                    numbering = add(properties, "numPr")
                    add(numbering, "ilvl", val=min(8, level))
                    add(numbering, "numId", val=list_number)
                if task is not None:
                    self.run(
                        paragraph, {"type": "text", "text": "☒ " if task else "☐ "}
                    )
                self.inline(paragraph, node.get("content", []))
                identity = (node.get("attrs") or {}).get("auditId")
                for original in self.opaque.get(identity, []):
                    paragraph.append(copy.deepcopy(original))
                if kind == "codeBlock":
                    add(properties, "shd", val="clear", fill="F1F3F4")
            elif kind in ("bulletList", "orderedList", "taskList"):
                identifier = (
                    None
                    if kind == "taskList"
                    else self.list_number(
                        kind, (node.get("attrs") or {}).get("start", 1)
                    )
                )
                for item in node.get("content", []):
                    checked = (
                        bool((item.get("attrs") or {}).get("checked"))
                        if kind == "taskList"
                        else None
                    )
                    self.blocks(
                        parent, item.get("content", []), level, identifier, checked
                    )
            elif kind in ("listItem", "taskItem"):
                self.blocks(
                    parent, node.get("content", []), level + 1, list_number, task
                )
            elif kind == "blockquote":
                before = len(parent)
                self.blocks(parent, node.get("content", []), level, list_number, task)
                for paragraph in list(parent)[before:]:
                    properties = paragraph.find("w:pPr", NS)
                    if properties is not None:
                        add(properties, "ind", left=720)
            elif kind == "table":
                self.table(parent, node)
            elif kind == "image":
                paragraph = add(parent, "p")
                alignment = (node.get("attrs") or {}).get("align")
                if alignment in ("left", "right", "center"):
                    add(add(paragraph, "pPr"), "jc", val=alignment)
                self.image(paragraph, node)
            elif kind == "horizontalRule":
                borders = add(add(add(parent, "p"), "pPr"), "pBdr")
                add(borders, "bottom", val="single", sz=6, color="888888")
            elif kind == "pageBreak":
                add(add(add(parent, "p"), "r"), "br", type="page")
            else:
                self.blocks(parent, node.get("content", []), level, list_number, task)

    def table_cell(self, row, node, continuation=False):
        cell = add(row, "tc")
        properties = add(cell, "tcPr")
        attrs = node.get("attrs") or {}
        span = max(1, int(number(attrs.get("colspan", 1), 1)))
        if span > 1:
            add(properties, "gridSpan", val=span)
        widths = attrs.get("colwidth") or []
        if widths:
            add(
                properties,
                "tcW",
                w=round(sum(number(width, 100) for width in widths) * 15),
                type="dxa",
            )
        if continuation or number(attrs.get("rowspan", 1), 1) > 1:
            add(properties, "vMerge", val="continue" if continuation else "restart")
        background = color(attrs.get("backgroundColor"))
        if background:
            add(properties, "shd", val="clear", fill=background)
        if attrs.get("verticalAlign") in ("top", "center", "bottom"):
            add(properties, "vAlign", val=attrs["verticalAlign"])
        border_color = color(attrs.get("borderColor"), "B7B7B7")
        width = max(0, min(96, number(attrs.get("borderWidth"), 1) * 6))
        borders = add(properties, "tcBorders")
        for side in ("top", "bottom", "left", "right"):
            add(
                borders,
                side,
                val=(
                    "nil"
                    if width == 0
                    else {
                        "dashed": "dashed",
                        "dotted": "dotted",
                        "double": "double",
                    }.get(attrs.get("borderStyle"), "single")
                ),
                sz=round(width),
                color=border_color,
            )
        if continuation:
            add(cell, "p")
        else:
            self.blocks(cell, node.get("content", []) or [{"type": "paragraph"}])
            chunks = self.opaque.get(attrs.get("auditId"), [])
            if chunks:
                paragraph = next(
                    (item for item in reversed(list(cell)) if item.tag == tag("p")),
                    None,
                )
                if paragraph is None:
                    paragraph = add(cell, "p")
                for original in chunks:
                    paragraph.append(copy.deepcopy(original))
        if len(cell) == 1 or cell[-1].tag != tag("p"):
            add(cell, "p")
        return span

    def table(self, parent, node):
        table = add(parent, "tbl")
        properties = add(table, "tblPr")
        add(properties, "tblW", w=0, type="auto")
        add(properties, "tblLayout", type="fixed")
        active = {}
        for source in node.get("content", []):
            row = add(table, "tr")
            cells = source.get("content", [])
            if cells and all(cell.get("type") == "tableHeader" for cell in cells):
                add(add(row, "trPr"), "tblHeader")
            next_active = {}
            column = 0
            for cell in cells:
                while column in active:
                    previous, remaining = active[column]
                    span = self.table_cell(row, previous, True)
                    if remaining > 1:
                        next_active[column] = (previous, remaining - 1)
                    column += span
                span = self.table_cell(row, cell)
                height = int(number((cell.get("attrs") or {}).get("rowspan", 1), 1))
                if height > 1:
                    next_active[column] = (cell, height - 1)
                column += span
            while column in active:
                previous, remaining = active[column]
                span = self.table_cell(row, previous, True)
                if remaining > 1:
                    next_active[column] = (previous, remaining - 1)
                column += span
            active = next_active

    def header_footer(self, kind, text, page_number=False):
        source_ref = self.section.find("w:" + kind + "Reference", NS)
        old_text = ""
        old_has_page = False
        if source_ref is not None:
            identifier = source_ref.get("{" + R + "}id")
            relationship = next(
                (item for item in self.relationships if item.get("Id") == identifier),
                None,
            )
            path = (
                str(PurePosixPath("word") / relationship.get("Target", ""))
                if relationship is not None
                else ""
            )
            if path in self.parts:
                original = ET.fromstring(self.parts[path])
                old_text = part_text(original)
                old_has_page = any(
                    "PAGE" in (child.text or "")
                    for child in original.findall(".//w:instrText", NS)
                )
        if text == old_text and page_number == old_has_page:
            return
        for reference in list(self.section.findall("w:" + kind + "Reference", NS)):
            self.section.remove(reference)
        name = "audit-" + kind + ".xml"
        root = ET.Element(tag("hdr" if kind == "header" else "ftr"))
        paragraph = add(root, "p")
        self.run(paragraph, {"type": "text", "text": text})
        if page_number:
            add(add(paragraph, "r"), "fldChar", fldCharType="begin")
            instruction = add(add(paragraph, "r"), "instrText")
            instruction.text = " PAGE "
            add(add(paragraph, "r"), "fldChar", fldCharType="separate")
            self.run(paragraph, {"type": "text", "text": "1"})
            add(add(paragraph, "r"), "fldChar", fldCharType="end")
        self.parts["word/" + name] = ET.tostring(
            root, encoding="utf-8", xml_declaration=True
        )
        identifier = self.relationship(name, kind)
        self.section.insert(
            0,
            ET.Element(
                tag(kind + "Reference"),
                {tag("type"): "default", "{" + R + "}id": identifier},
            ),
        )
        self.content_type(
            "/word/" + name,
            "application/vnd.openxmlformats-officedocument.wordprocessingml."
            + kind
            + "+xml",
        )

    def save(self):
        self.blocks(self.body, self.snapshot["content"].get("content", []))
        for original in self.opaque_blocks:
            self.body.append(copy.deepcopy(original))
        settings = self.snapshot["settings"]
        sizes = {
            "A4": (11906, 16838),
            "Letter": (12240, 15840),
            "A5": (8391, 11906),
            "Legal": (12240, 20160),
        }
        width, height = sizes.get(settings.get("pageSize"), sizes["A4"])
        if settings.get("orientation") == "landscape":
            width, height = height, width
        for source in list(self.section.findall("w:pgSz", NS)) + list(
            self.section.findall("w:pgMar", NS)
        ):
            self.section.remove(source)
        add(
            self.section,
            "pgSz",
            w=width,
            h=height,
            orient=settings.get("orientation", "portrait"),
        )
        add(
            self.section,
            "pgMar",
            **{
                side: round(number(value) * 1440 / 25.4)
                for side, value in settings["margins"].items()
            },
        )
        if "header" in settings:
            self.header_footer("header", settings["header"])
        if "footer" in settings or settings.get("footerPageNumber"):
            self.header_footer(
                "footer",
                settings.get("footer", ""),
                bool(settings.get("footerPageNumber")),
            )
        self.body.append(self.section)
        self.parts["word/document.xml"] = ET.tostring(
            self.root, encoding="utf-8", xml_declaration=True
        )
        self.parts["word/_rels/document.xml.rels"] = ET.tostring(
            self.relationships, encoding="utf-8", xml_declaration=True
        )
        self.parts["word/styles.xml"] = ET.tostring(
            self.styles, encoding="utf-8", xml_declaration=True
        )
        self.parts["word/numbering.xml"] = ET.tostring(
            self.numbering, encoding="utf-8", xml_declaration=True
        )
        if not any(item.get("Type") == R + "/styles" for item in self.relationships):
            self.relationship("styles.xml", "styles")
        if not any(item.get("Type") == R + "/numbering" for item in self.relationships):
            self.relationship("numbering.xml", "numbering")
        self.parts["word/_rels/document.xml.rels"] = ET.tostring(
            self.relationships, encoding="utf-8", xml_declaration=True
        )
        self.content_type(
            "rels", "application/vnd.openxmlformats-package.relationships+xml", True
        )
        self.content_type("xml", "application/xml", True)
        self.content_type(
            "/word/document.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        )
        self.content_type(
            "/word/styles.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
        )
        self.content_type(
            "/word/numbering.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
        )
        self.parts["[Content_Types].xml"] = ET.tostring(
            self.content_types, encoding="utf-8", xml_declaration=True
        )
        if "_rels/.rels" not in self.parts:
            root = ET.Element("{" + REL + "}Relationships")
            ET.SubElement(
                root,
                "{" + REL + "}Relationship",
                {
                    "Id": "rId1",
                    "Type": R + "/officeDocument",
                    "Target": "word/document.xml",
                },
            )
            self.parts["_rels/.rels"] = ET.tostring(
                root, encoding="utf-8", xml_declaration=True
            )
        buffer = io.BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
            for name, data in self.parts.items():
                output.writestr(name, data)
        return buffer.getvalue()


def docx_bytes(path, snapshot):
    return DocxWriter(path, normalize_snapshot(snapshot)).save()
