"""Portable HTML and PDF exports of the document editor's saved model."""

import html
import re

import pymupdf

from .rich_document import color, decode_image, normalize_snapshot, number, safe_link

PAGE_SIZES = {
    "A4": (210, 297),
    "A5": (148, 210),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}
PAGE_BREAK = "<!--document-page-break-->"
BASE_CSS = """body {font-family:sans-serif;font-size:11pt;color:#000;line-height:1.15;margin:0;}
p {margin:0 0 6pt;} table {border-collapse:collapse;max-width:100%;width:100%;}
td,th {border:0.5pt solid #999;padding:4pt;vertical-align:top;}
th {font-weight:bold;} img {max-width:100%;height:auto;}
blockquote {border-left:2pt solid #bbb;padding-left:12pt;margin-left:12pt;}
pre,code {font-family:monospace;} pre {white-space:pre-wrap;}
li p {margin-bottom:2pt;} .page-break {page-break-before:always;break-before:page;}
"""


def attribute(value):
    return html.escape(str(value), quote=True)


def css_color(value):
    converted = color(value)
    return "#" + converted if converted else None


def style_attribute(styles):
    return ' style="' + attribute(";".join(styles)) + '"' if styles else ""


def paragraph_styles(attrs):
    styles = []
    if attrs.get("textAlign") in ("left", "center", "right", "justify"):
        styles.append("text-align:" + attrs["textAlign"])
    for attr, css, unit in (
        ("indent", "margin-left", "mm"),
        ("spacingBefore", "margin-top", "pt"),
        ("spacingAfter", "margin-bottom", "pt"),
    ):
        if attrs.get(attr) is not None:
            styles.append(f"{css}:{max(0, min(500, number(attrs[attr])))}{unit}")
    line_height = str(attrs.get("lineHeight") or "")
    if re.fullmatch(r"\d+(?:\.\d+)?(?:pt|px|%)?", line_height):
        styles.append("line-height:" + line_height)
    return styles


def render_node(node, images=None):
    kind = node["type"]
    attrs = node.get("attrs") or {}
    children = "".join(render_node(child, images) for child in node.get("content", []))
    if kind == "text":
        rendered = html.escape(node.get("text", ""))
        for mark in node.get("marks", []):
            mark_kind = mark["type"]
            mark_attrs = mark.get("attrs") or {}
            wrapper = {
                "bold": "strong",
                "italic": "em",
                "underline": "u",
                "strike": "s",
                "subscript": "sub",
                "superscript": "sup",
                "code": "code",
            }.get(mark_kind)
            if wrapper:
                rendered = f"<{wrapper}>{rendered}</{wrapper}>"
            elif mark_kind == "link" and safe_link(mark_attrs.get("href")):
                rendered = (
                    '<a href="'
                    + attribute(mark_attrs["href"])
                    + '">'
                    + rendered
                    + "</a>"
                )
            elif mark_kind in ("textStyle", "highlight"):
                styles = []
                for key, css in (
                    ("color", "color"),
                    ("backgroundColor", "background-color"),
                ):
                    value = css_color(mark_attrs.get(key))
                    if value:
                        styles.append(css + ":" + value)
                if mark_kind == "highlight" and css_color(mark_attrs.get("color")):
                    styles = ["background-color:" + css_color(mark_attrs["color"])]
                if mark_attrs.get("fontSize"):
                    styles.append(
                        f'font-size:{max(1, min(400, number(mark_attrs["fontSize"], 11)))}pt'
                    )
                family = re.sub(
                    r"[^\w ,.-]", "", str(mark_attrs.get("fontFamily") or "")
                )[:150]
                if family:
                    styles.append("font-family:" + family)
                rendered = (
                    "<span" + style_attribute(styles) + ">" + rendered + "</span>"
                )
        return rendered
    if kind == "doc":
        return children
    if kind in ("paragraph", "heading"):
        element = "p" if kind == "paragraph" else "h" + str(attrs.get("level", 1))
        return (
            "<"
            + element
            + style_attribute(paragraph_styles(attrs))
            + ">"
            + (children or "<br/>")
            + "</"
            + element
            + ">"
        )
    if kind == "pageBreak":
        return PAGE_BREAK
    if kind == "hardBreak":
        return "<br/>"
    if kind == "horizontalRule":
        return "<hr/>"
    if kind == "image":
        source = attrs["src"]
        data, extension = decode_image(source)
        if images is not None:
            source = f"editor-image-{len(images)}.{extension}"
            images.append((data, source))
        styles = []
        for side in ("width", "height"):
            if attrs.get(side):
                styles.append(f"{side}:{max(1, min(8000, number(attrs[side], 100)))}px")
        image = (
            '<img src="'
            + attribute(source)
            + '" alt="'
            + attribute(attrs.get("alt") or "")
            + '"'
            + style_attribute(styles)
            + "/>"
        )
        align = (
            attrs.get("align")
            if attrs.get("align") in ("left", "center", "right")
            else "left"
        )
        return '<p style="text-align:' + align + '">' + image + "</p>"
    if kind in ("tableCell", "tableHeader"):
        element = "th" if kind == "tableHeader" else "td"
        styles = []
        background = css_color(attrs.get("backgroundColor"))
        if background:
            styles.append("background-color:" + background)
        if attrs.get("verticalAlign") in ("top", "middle", "bottom"):
            styles.append("vertical-align:" + attrs["verticalAlign"])
        widths = attrs.get("colwidth")
        if isinstance(widths, list) and widths:
            styles.append(
                "width:"
                + str(sum(max(0, min(8000, number(width))) for width in widths))
                + "px"
            )
        if attrs.get("borderColor"):
            styles.append("border-color:" + (css_color(attrs["borderColor"]) or "#999"))
        if attrs.get("borderWidth") is not None:
            styles.append(
                f'border-width:{max(0, min(20, number(attrs["borderWidth"], 1)))}px'
            )
        if attrs.get("borderStyle") in ("none", "solid", "dashed", "dotted", "double"):
            styles.append("border-style:" + attrs["borderStyle"])
        spans = f' colspan="{int(number(attrs.get("colspan", 1), 1))}" rowspan="{int(number(attrs.get("rowspan", 1), 1))}"'
        return (
            "<"
            + element
            + spans
            + style_attribute(styles)
            + ">"
            + children
            + "</"
            + element
            + ">"
        )
    element = {
        "bulletList": "ul",
        "taskList": "ul",
        "orderedList": "ol",
        "listItem": "li",
        "taskItem": "li",
        "blockquote": "blockquote",
        "codeBlock": "pre",
        "table": "table",
        "tableRow": "tr",
    }.get(kind)
    if element:
        extra = ""
        if kind == "orderedList":
            extra = ' start="' + str(max(1, int(number(attrs.get("start"), 1)))) + '"'
        if kind == "taskItem":
            children = ("☑ " if attrs.get("checked") else "☐ ") + children
        return "<" + element + extra + ">" + children + "</" + element + ">"
    return children


def html_document(snapshot):
    snapshot = normalize_snapshot(snapshot)
    settings = snapshot["settings"]
    margins = settings["margins"]
    page_css = (
        "@page {size:"
        + settings["pageSize"]
        + " "
        + settings["orientation"]
        + ";margin:"
        + " ".join(
            str(margins[side]) + "mm" for side in ("top", "right", "bottom", "left")
        )
        + ";}"
    )
    title = html.escape(str(snapshot.get("name") or "Документ"))
    body = render_node(snapshot["content"]).replace(
        PAGE_BREAK, '<div class="page-break"></div>'
    )
    header = (
        "<header>" + html.escape(settings["header"]) + "</header>"
        if settings.get("header")
        else ""
    )
    footer = (
        "<footer>" + html.escape(settings["footer"]) + "</footer>"
        if settings.get("footer")
        else ""
    )
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8"/><title>'
        + title
        + "</title><style>"
        + BASE_CSS
        + page_css
        + "</style></head><body>"
        + header
        + body
        + footer
        + "</body></html>"
    )


def pdf_bytes(snapshot):
    snapshot = normalize_snapshot(snapshot)
    settings = snapshot["settings"]
    width, height = PAGE_SIZES[settings["pageSize"]]
    if settings["orientation"] == "landscape":
        width, height = height, width
    scale = 72 / 25.4
    box = pymupdf.Rect(0, 0, width * scale, height * scale)
    margins = settings["margins"]
    area = pymupdf.Rect(
        margins["left"] * scale,
        margins["top"] * scale,
        box.width - margins["right"] * scale,
        box.height - margins["bottom"] * scale,
    )
    if area.width < 20 or area.height < 20:
        raise ValueError("Для экспорта PDF уменьшите поля страницы")
    images = []
    body = render_node(snapshot["content"], images)
    archive = pymupdf.Archive()
    for data, name in images:
        archive.add((data, name))

    def next_page(index, filled):
        if index > 500:
            raise ValueError(
                "Содержимое не помещается в 500 страниц PDF. Уменьшите таблицы или изображения."
            )
        return box, area, None

    result = pymupdf.open()
    try:
        # Separate stories make an explicit editor page break deterministic.
        for section in body.split(PAGE_BREAK):
            story = pymupdf.Story(
                section or "<p><br/></p>", user_css=BASE_CSS, archive=archive
            )
            with story.write_with_links(next_page) as generated:
                result.insert_pdf(generated)
        for index, page in enumerate(result):
            for kind in ("header", "footer"):
                text = settings.get(kind) or ""
                if kind == "footer" and settings.get("footerPageNumber"):
                    text += (" " if text else "") + str(index + 1)
                if not text:
                    continue
                if kind == "header":
                    rect = pymupdf.Rect(area.x0, 5, area.x1, max(20, area.y0 - 5))
                else:
                    rect = pymupdf.Rect(
                        area.x0,
                        min(box.height - 20, area.y1 + 5),
                        area.x1,
                        box.height - 5,
                    )
                page.insert_htmlbox(
                    rect,
                    "<p>" + html.escape(text) + "</p>",
                    css="body {font-family:sans-serif;font-size:9pt;text-align:center;}",
                )
        result.set_metadata(
            {"title": str(snapshot.get("name") or "Документ"), "creator": "Сверка"}
        )
        return result.tobytes(garbage=4, deflate=True)
    finally:
        result.close()
