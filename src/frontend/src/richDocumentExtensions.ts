import { Extension, Node, mergeAttributes } from "@tiptap/core";
import Image from "@tiptap/extension-image";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { Fragment, Slice } from "@tiptap/pm/model";
import { Mapping } from "@tiptap/pm/transform";
import type { EvidenceAnnotation } from "./types";
import { numberSpans } from "./numericText";

export const DocumentAttributes = Extension.create({
  name: "documentAttributes",
  addGlobalAttributes() {
    return [
      {
        types: ["paragraph", "heading", "tableRow", "tableCell", "tableHeader"],
        attributes: {
          auditId: {
            default: null,
            parseHTML: (element) => element.getAttribute("data-audit-id"),
            renderHTML: (attributes) =>
              attributes.auditId ? { "data-audit-id": attributes.auditId } : {},
          },
        },
      },
      {
        types: ["paragraph", "heading"],
        attributes: {
          indent: {
            default: 0,
            renderHTML: (attrs) =>
              attrs.indent ? { style: `margin-left:${attrs.indent}mm` } : {},
          },
          lineHeight: {
            default: null,
            renderHTML: (attrs) =>
              attrs.lineHeight
                ? { style: `line-height:${attrs.lineHeight}` }
                : {},
          },
          spacingBefore: {
            default: null,
            renderHTML: (attrs) =>
              attrs.spacingBefore != null
                ? { style: `margin-top:${attrs.spacingBefore}pt` }
                : {},
          },
          spacingAfter: {
            default: null,
            renderHTML: (attrs) =>
              attrs.spacingAfter != null
                ? { style: `margin-bottom:${attrs.spacingAfter}pt` }
                : {},
          },
        },
      },
      {
        types: ["tableCell", "tableHeader"],
        attributes: {
          backgroundColor: {
            default: null,
            renderHTML: (attrs) =>
              attrs.backgroundColor
                ? { style: `background-color:${attrs.backgroundColor}` }
                : {},
          },
          verticalAlign: {
            default: "top",
            renderHTML: (attrs) => ({
              style: `vertical-align:${attrs.verticalAlign}`,
            }),
          },
          borderColor: {
            default: null,
            renderHTML: (attrs) =>
              attrs.borderColor
                ? { style: `border-color:${attrs.borderColor}` }
                : {},
          },
          borderWidth: {
            default: null,
            renderHTML: (attrs) =>
              attrs.borderWidth
                ? { style: `border-width:${attrs.borderWidth}px` }
                : {},
          },
          borderStyle: {
            default: null,
            renderHTML: (attrs) =>
              attrs.borderStyle
                ? { style: `border-style:${attrs.borderStyle}` }
                : {},
          },
        },
      },
    ];
  },
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: new PluginKey("documentIdentities"),
        props: {
          transformPasted(slice) {
            const reset = (node: ProseMirrorNode): ProseMirrorNode => {
              if (node.isText) return node;
              const content: ProseMirrorNode[] = [];
              node.content.forEach((child) => content.push(reset(child)));
              return node.type.create(
                {
                  ...node.attrs,
                  auditId: "added_" + crypto.randomUUID().replaceAll("-", ""),
                },
                Fragment.fromArray(content),
                node.marks,
              );
            };
            const content: ProseMirrorNode[] = [];
            slice.content.forEach((node) => content.push(reset(node)));
            return new Slice(
              Fragment.fromArray(content),
              slice.openStart,
              slice.openEnd,
            );
          },
        },
        appendTransaction(transactions, oldState, state) {
          if (!transactions.some((transaction) => transaction.docChanged))
            return;
          const used = new Set<string>();
          const mapping = new Mapping();
          transactions.forEach((transaction) =>
            mapping.appendMapping(transaction.mapping),
          );
          const inverse = mapping.invert();
          const transaction = state.tr;
          state.doc.descendants((node, position) => {
            if (!["paragraph", "heading", "tableRow"].includes(node.type.name))
              return;
            let auditId = node.attrs.auditId as string | null;
            if (!auditId) {
              const previous = oldState.doc.nodeAt(inverse.map(position));
              const sameBlockType =
                previous?.type.name === node.type.name ||
                (["paragraph", "heading"].includes(previous?.type.name || "") &&
                  ["paragraph", "heading"].includes(node.type.name));
              if (sameBlockType && previous?.textContent === node.textContent)
                auditId = previous.attrs.auditId;
            }
            if (!auditId || used.has(auditId)) {
              auditId = "added_" + crypto.randomUUID().replaceAll("-", "");
            }
            if (node.attrs.auditId !== auditId) {
              transaction.setNodeMarkup(position, undefined, {
                ...node.attrs,
                auditId,
              });
            }
            used.add(auditId);
          });
          return transaction.docChanged ? transaction : undefined;
        },
      }),
    ];
  },
});

export const PageBreak = Node.create({
  name: "pageBreak",
  group: "block",
  atom: true,
  parseHTML: () => [{ tag: "div[data-page-break]" }],
  renderHTML: ({ HTMLAttributes }) => [
    "div",
    mergeAttributes(HTMLAttributes, {
      "data-page-break": "true",
      class: "rich-page-break",
    }),
  ],
});

export const DocumentImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      width: {
        default: null,
        parseHTML: (element) => element.getAttribute("width"),
      },
      height: {
        default: null,
        parseHTML: (element) => element.getAttribute("height"),
      },
      align: {
        default: "left",
        renderHTML: (attrs) => ({
          style: `display:block;max-width:100%;margin-left:${attrs.align === "right" || attrs.align === "center" ? "auto" : "0"};margin-right:${attrs.align === "center" ? "auto" : "0"}`,
        }),
      },
    };
  },
}).configure({ allowBase64: true });

export type SearchState = { query: string; caseSensitive: boolean };

function textMap(node: ProseMirrorNode, base: number) {
  const parts: { start: number; end: number; position: number }[] = [];
  let text = "";
  node.descendants((child, offset) => {
    if (child.isTextblock && text && !text.endsWith("\n")) text += "\n";
    if (!child.isText || !child.text) return;
    parts.push({
      start: text.length,
      end: text.length + child.text.length,
      position: base + 1 + offset,
    });
    text += child.text;
  });
  return { text, parts };
}

export function findMatches(doc: ProseMirrorNode, search: SearchState) {
  const matches: { from: number; to: number }[] = [];
  if (!search.query) return matches;
  const query = search.caseSensitive
    ? search.query
    : search.query.toLocaleLowerCase();
  doc.descendants((node, position) => {
    if (!node.isTextblock) return;
    const mapped = textMap(node, position);
    const text = search.caseSensitive
      ? mapped.text
      : mapped.text.toLocaleLowerCase();
    let start = text.indexOf(query);
    while (start >= 0) {
      const first = mapped.parts.find(
        (part) => start >= part.start && start < part.end,
      );
      const end = start + query.length;
      const last = mapped.parts.find(
        (part) => end > part.start && end <= part.end,
      );
      if (first && last)
        matches.push({
          from: first.position + start - first.start,
          to: last.position + end - last.start,
        });
      start = text.indexOf(query, end);
    }
  });
  return matches;
}

export function auditHighlights(
  getAnnotations: () => EvidenceAnnotation[],
  getSearch: () => SearchState,
) {
  return new Plugin({
    key: new PluginKey("documentAuditHighlights"),
    props: {
      decorations(state) {
        const decorations: Decoration[] = [];
        const annotations = getAnnotations();
        state.doc.descendants((node, position) => {
          const matching = annotations.filter(
            (annotation) => annotation.evidence.locator === node.attrs.auditId,
          );
          if (!matching.length) return;
          decorations.push(
            Decoration.node(position, position + node.nodeSize, {
              class: "rich-source-context",
              title: matching.map((item) => item.label).join(" / "),
            }),
          );
          const mapped = textMap(node, position);
          const values = matching.flatMap((item) =>
            numberSpans(item.evidence.text).map((span) => span.text),
          );
          for (const range of numberSpans(
            mapped.text,
            values.length ? values : undefined,
          )) {
            for (const part of mapped.parts) {
              const from = Math.max(range.start, part.start),
                to = Math.min(range.end, part.end);
              if (from < to)
                decorations.push(
                  Decoration.inline(
                    part.position + from - part.start,
                    part.position + to - part.start,
                    {
                      class: "rich-source-number",
                      title: matching.map((item) => item.label).join(" / "),
                    },
                  ),
                );
            }
          }
        });
        for (const match of findMatches(state.doc, getSearch()))
          decorations.push(
            Decoration.inline(match.from, match.to, {
              class: "rich-search-match",
            }),
          );
        return DecorationSet.create(state.doc, decorations);
      },
    },
  });
}
