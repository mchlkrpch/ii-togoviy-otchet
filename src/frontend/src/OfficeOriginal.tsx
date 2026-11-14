import { useEffect, useRef, useState } from "react";
import { request, statusLabels } from "./api";
import { canonicalNumber, numberSpans } from "./numericText";
import { calculationExpression, calculationStatus } from "./calculationDisplay";
import TableVariableLabel from "./TableVariableLabel";
import type { CSSProperties } from "react";
import type {
  Document,
  Edit,
  Evidence,
  EvidenceAnnotation,
  NumericTarget,
  Node,
} from "./types";

function numericText(text: string, enabled: boolean) {
  if (!enabled) return text;
  const ranges = numberSpans(
    text,
    canonicalNumber(text) !== undefined ? [text] : undefined,
  );
  let previous = 0;
  const pieces: React.ReactNode[] = [];
  for (const [index, range] of ranges.entries()) {
    pieces.push(text.slice(previous, range.start));
    pieces.push(
      <mark key={index} className="source-number-mark">
        {range.text}
      </mark>,
    );
    previous = range.end;
  }
  pieces.push(text.slice(previous));
  return pieces;
}

export type OriginalSheet = {
  sheet: string;
  sheets: string[];
  lastRow: number;
  lastColumn: number;
  rowStart: number;
  columnStart: number;
  columns: { index: number; name: string; width: number }[];
  rows: {
    index: number;
    height: number;
    cells: {
      address: string;
      text: string;
      style: CSSProperties;
      rowSpan: number;
      colSpan: number;
      formula?: string;
    }[];
  }[];
};

export function SpreadsheetOriginal({
  url,
  sheet,
  row,
  column,
  document,
  highlights,
  activeEvidence,
  reportNodes,
  selectedIds,
  onSelect,
  onReport,
  onLayout,
  annotations = [],
  onEdit,
  zoom = 100,
}: {
  url: string;
  sheet: string;
  row: number;
  column: number;
  document: Document;
  highlights: Evidence[];
  activeEvidence?: Evidence;
  reportNodes: Node[];
  selectedIds: string[];
  onSelect: (evidence: Evidence) => void;
  onReport?: (
    node: Node,
    event: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean },
  ) => void;
  onLayout: (layout: OriginalSheet) => void;
  annotations?: EvidenceAnnotation[];
  onEdit?: (edit: Edit) => void;
  zoom?: number;
}) {
  const [layout, setLayout] = useState<OriginalSheet>();
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setError("");
    setLayout(undefined);
    request<OriginalSheet>(
      `${url}/original-sheet?sheet=${encodeURIComponent(sheet)}&row=${row}&column=${column}`,
    )
      .then((data) => {
        if (alive) {
          setLayout(data);
          onLayout(data);
        }
      })
      .catch((reason) => {
        if (alive) setError(String(reason));
      });
    return () => {
      alive = false;
    };
  }, [url, sheet, row, column, document.cells]);
  if (error)
    return <p className="viewer-error">Не удалось открыть лист: {error}</p>;
  if (!layout)
    return <p className="viewer-loading">Открываю оригинал листа…</p>;
  return (
    <table
      className="original-spreadsheet"
      aria-label={`Оригинал · ${layout.sheet}`}
      style={{ width: layout.columns.reduce((sum, c) => sum + c.width, 0) }}
    >
      <colgroup>
        {layout.columns.map((col) => (
          <col key={col.index} style={{ width: col.width }} />
        ))}
      </colgroup>
      <tbody>
        {layout.rows.map((row) => (
          <tr key={row.index} style={{ height: row.height }}>
            {row.cells.map((cell) => {
              const node = reportNodes.find(
                (node) =>
                  node.reported_evidence?.sheet === layout.sheet &&
                  node.reported_evidence.cell === cell.address,
              );
              const highlighted = highlights.some(
                (e) => e.sheet === layout.sheet && e.cell === cell.address,
              );
              const active =
                activeEvidence?.sheet === layout.sheet &&
                activeEvidence.cell === cell.address;
              const labels = annotations.filter((item) =>
                item.evidence.sheet === layout.sheet && item.evidence.cell === cell.address,
              );
              const status = node && calculationStatus(node);
              const showStatus = node && (status === "mismatch" || Boolean(calculationExpression(node))) && status;
              const labelCell =
                document.role === "source" &&
                !highlighted &&
                canonicalNumber(cell.text) === undefined &&
                row.cells.some((other) =>
                  highlights.some(
                    (e) => e.sheet === layout.sheet && e.cell === other.address,
                  ),
                );
              const select = (
                event: React.MouseEvent | React.KeyboardEvent,
              ) => {
                if (window.getSelection()?.toString().trim()) return;
                onSelect({
                  document_id: document.id,
                  sheet: layout.sheet,
                  cell: cell.address,
                  locator: `${layout.sheet}!${cell.address}`,
                  text: cell.text,
                  method: "xlsx",
                  confidence: 100,
                });
                if (node) onReport?.(node, event);
              };
              return (
                <td
                  key={cell.address}
                  data-cell={cell.address}
                  data-sheet={layout.sheet}
                  style={cell.style}
                  rowSpan={cell.rowSpan}
                  colSpan={cell.colSpan}
                  className={`${labelCell ? "source-label-cell" : ""} ${highlighted ? "original-cell-highlight" : ""} ${active || selectedIds.includes(node?.id || "") ? "original-cell-active" : ""} ${showStatus ? `original-report-cell ${showStatus}` : ""}`}
                  tabIndex={node ? 0 : undefined}
                  title={`${layout.sheet}!${cell.address}${cell.formula ? ` · ${cell.formula}` : ""}`}
                  onClick={select}
                  onDoubleClick={(event) => {
                    if (!onEdit) return;
                    event.preventDefault();
                    event.stopPropagation();
                    onEdit({
                      kind: "cell",
                      target: `${document.id}:${layout.sheet}!${cell.address}`,
                      title: `${document.name} · ${layout.sheet}!${cell.address}`,
                      value:
                        document.cells[`${layout.sheet}!${cell.address}`]
                          ?.raw ?? cell.text,
                      evidence: {
                        document_id: document.id,
                        sheet: layout.sheet,
                        cell: cell.address,
                        text: cell.text,
                        locator: `${layout.sheet}!${cell.address}`,
                        method: "xlsx",
                        confidence: 100,
                      },
                    });
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") select(event);
                  }}
                >
                  {labels.length > 0 && <TableVariableLabel zoom={zoom} label={`${labels.map(item => item.label).join(" / ")} · ${cell.address}`} />}
                  {numericText(
                    cell.text,
                    document.role === "source" && (highlighted || active),
                  )}
                  {showStatus && (
                    <span
                      className="original-status-dot"
                      aria-label={statusLabels[showStatus]}
                    />
                  )}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function WordOriginal({
  url,
  highlights,
  activeEvidence,
  onSelect,
  onReady,
  onPages,
  annotations = [],
  numericTargets = [],
  source,
  onEdit,
}: {
  url: string;
  highlights: Evidence[];
  activeEvidence?: Evidence;
  onSelect: (evidence: Evidence) => void;
  onReady: () => void;
  onPages?: (count: number) => void;
  annotations?: EvidenceAnnotation[];
  numericTargets?: NumericTarget[];
  source?: Document;
  onEdit?: (edit: Edit) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [rendered, setRendered] = useState(0);
  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    const container = host.current!;
    container.replaceChildren();
    setError("");
    Promise.all([
      fetch(`${url}/file`, { signal: controller.signal }).then((response) => {
        if (!response.ok) throw Error("Документ не найден");
        return response.blob();
      }),
      import("docx-preview"),
    ])
      .then(async ([blob, renderer]) => {
        const staging = document.createElement("div");
        await renderer.renderAsync(blob, staging, undefined, {
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: false,
          renderAltChunks: false,
          useBase64URL: true,
        });
        if (!alive) return;
        container.replaceChildren(...staging.childNodes);
        let paragraph = 0,
          table = 0;
        container
          .querySelectorAll("section.docx > article")
          .forEach((article) => {
            Array.from(article.children).forEach((element) => {
              if (element.tagName === "P")
                element.setAttribute("data-locator", `Абзац ${++paragraph}`);
              if (element.tagName === "TABLE") {
                table++;
                Array.from((element as HTMLTableElement).rows).forEach(
                  (row, index) =>
                    row.setAttribute(
                      "data-locator",
                      `Таблица ${table}, строка ${index + 1}`,
                    ),
                );
              }
            });
          });
        setRendered((value) => value + 1);
        onPages?.(container.querySelectorAll("section.docx").length || 1);
        onReady();
      })
      .catch((reason) => {
        if (alive) setError(String(reason));
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [url]);
  useEffect(() => {
    host.current
      ?.querySelectorAll(".variable-cell-label")
      .forEach((element) => element.remove());
    host.current
      ?.querySelectorAll("mark.source-number-mark")
      .forEach((mark) => {
        const parent = mark.parentNode;
        mark.replaceWith(...mark.childNodes);
        parent?.normalize();
      });
    for (const block of source?.blocks || []) {
      if (!block.manual) continue;
      const element = Array.from(
        host.current?.querySelectorAll<HTMLElement>("[data-locator]") || [],
      ).find((element) => element.dataset.locator === block.locator);
      if (element?.tagName === "TR") {
        const cells = Array.from((element as HTMLTableRowElement).cells);
        block.text.split(" | ").forEach((text, index) => {
          if (cells[index]) cells[index].textContent = text;
        });
      } else if (element) element.textContent = block.text;
    }
    host.current
      ?.querySelectorAll<HTMLElement>("[data-locator]")
      .forEach((element) => {
        const locator = element.dataset.locator;
        element.classList.toggle(
          "original-word-highlight",
          highlights.some((e) => e.locator === locator),
        );
        element.classList.toggle(
          "original-word-active",
          activeEvidence?.locator === locator,
        );
        if (
          highlights.some((e) => e.locator === locator) ||
          activeEvidence?.locator === locator
        ) {
          const walker = document.createTreeWalker(element, 4);
          const nodes: { node: Text; start: number; end: number }[] = [];
          let text = "";
          let current = walker.nextNode();
          while (current) {
            const node = current as Text;
            nodes.push({
              node,
              start: text.length,
              end: text.length + node.data.length,
            });
            text += node.data;
            current = walker.nextNode();
          }
          const values = numericTargets
            .filter((item) => item.evidence.locator === locator)
            .map((item) => item.value);
          const ranges = numberSpans(text, values.length ? values : undefined);
          for (const range of [...ranges].reverse())
            for (const item of [...nodes].reverse()) {
              const start = Math.max(range.start, item.start) - item.start,
                end = Math.min(range.end, item.end) - item.start;
              if (start >= end) continue;
              const selection = document.createRange();
              selection.setStart(item.node, start);
              selection.setEnd(item.node, end);
              const mark = document.createElement("mark");
              mark.className = "source-number-mark";
              selection.surroundContents(mark);
            }
        }
        const matching = annotations.filter(
          (item) => item.evidence.locator === locator,
        );
        if (matching.length) {
          const target =
            element.querySelector<HTMLElement>("mark.source-number-mark") ||
            (element.tagName === "TR"
              ? element.querySelector<HTMLElement>("td")
              : element);
          if (target) {
            target.style.position = "relative";
            const label = document.createElement("span");
            label.className = "variable-cell-label";
            label.textContent = matching.map((item) => item.label).join(", ");
            target.appendChild(label);
          }
        }
      });
  }, [
    highlights,
    activeEvidence,
    rendered,
    annotations,
    numericTargets,
    source?.blocks,
  ]);
  return (
    <>
      {error && (
        <p className="viewer-error">Не удалось открыть DOCX: {error}</p>
      )}
      <div
        className="word-original"
        ref={host}
        onDoubleClick={(event) => {
          if (!onEdit || !source) return;
          const target = (event.target as HTMLElement).closest<HTMLElement>(
            "[data-locator]",
          );
          if (!target) return;
          event.preventDefault();
          event.stopPropagation();
          const cell = (
            event.target as HTMLElement
          ).closest<HTMLTableCellElement>("td");
          const suffix =
            target.tagName === "TR" && cell ? "#" + cell.cellIndex : "";
          const block = source.blocks.find(
            (block) => block.locator === target.dataset.locator,
          );
          const value = block
            ? suffix
              ? block.text.split(" | ")[cell!.cellIndex]
              : block.text
            : target.textContent || "";
          onEdit({
            kind: "text",
            target: `${source.id}:${target.dataset.locator}${suffix}`,
            title: `${source.name} · ${target.dataset.locator}`,
            value: value || "",
            evidence: {
              document_id: source.id,
              locator: target.dataset.locator,
              text: value || "",
              method: "docx",
              confidence: 100,
            },
          });
        }}
        onClick={(event) => {
          if (window.getSelection()?.toString().trim()) return;
          const element = (event.target as HTMLElement).closest<HTMLElement>(
            "[data-locator]",
          );
          const evidence = highlights.find(
            (e) => e.locator === element?.dataset.locator,
          );
          if (evidence) onSelect(evidence);
        }}
      />
    </>
  );
}
