import type { CaseResult, Document, Evidence, Node } from "./types";

export type ReportColumn = { index: number; title: string; code: string };
export type ReportRow = {
  id: string;
  title: string;
  cells: Record<number, Node>;
};
export type ReportGrid = { columns: ReportColumn[]; rows: ReportRow[] };
export type SourceReference = {
  document: Document;
  evidence: Evidence[];
  nodeIds: string[];
};

export function columnName(column: number): string {
  let result = "";
  for (let value = column; value > 0; value = Math.floor((value - 1) / 26)) {
    result = String.fromCharCode(65 + ((value - 1) % 26)) + result;
  }
  return result;
}

export function cellPosition(
  address: string,
): { column: number; row: number } | null {
  const match = /^\$?([A-Z]+)\$?(\d+)$/i.exec(address);
  if (!match) return null;
  const column = [...match[1].toUpperCase()].reduce(
    (value, letter) => value * 26 + letter.charCodeAt(0) - 64,
    0,
  );
  return { column, row: Number(match[2]) };
}

export function reportChecks(result: CaseResult): Node[] {
  const reports = new Set(
    result.report_ids ||
      (result.documents
        ? result.documents
            .filter((doc) => doc.role === "report")
            .map((doc) => doc.id)
        : [result.report_id]),
  );
  return result.checks.filter(
    (node) =>
      reports.has(node.reported_evidence?.document_id || "") &&
      !node.reported_parts,
  );
}

export function reportColumn(node: Node): number | null {
  if (node.column != null) return node.column;
  const match = /_c(\d+)$/.exec(node.id);
  if (match) return Number(match[1]);
  return node.reported_evidence?.cell
    ? cellPosition(node.reported_evidence.cell)?.column || null
    : null;
}

export function buildReportGrid(nodes: Node[]): ReportGrid {
  const multiple =
    new Set(nodes.map((node) => node.reported_evidence?.document_id)).size > 1;
  const columns = new Map<number, ReportColumn>();
  const rows = new Map<string, ReportRow>();
  for (const node of nodes) {
    const physicalColumn = reportColumn(node);
    const column = physicalColumn || 1;
    const address =
      node.reported_evidence?.cell && cellPosition(node.reported_evidence.cell);
    const rowId =
      (multiple ? (node.reported_evidence?.document_id || "") + "|" : "") +
      (address
        ? `${node.reported_evidence?.sheet}:${address.row}`
        : physicalColumn
          ? node.row || node.id
          : node.id);
    if (!rows.has(rowId))
      rows.set(rowId, {
        id: rowId,
        title: physicalColumn
          ? node.row_title || node.title || rowId
          : node.title || rowId,
        cells: {},
      });
    rows.get(rowId)!.cells[column] = node;
    columns.set(column, {
      index: column,
      title: physicalColumn ? node.title || node.code || "" : "Значение",
      code: node.code || "",
    });
  }
  return {
    columns: [...columns.values()].sort((a, b) => a.index - b.index),
    rows: [...rows.values()],
  };
}

export function selectionRange(
  grid: ReportGrid,
  firstId: string,
  lastId: string,
): string[] {
  let first: { row: number; column: number } | undefined;
  let last: { row: number; column: number } | undefined;
  grid.rows.forEach((row, index) => {
    for (const [column, node] of Object.entries(row.cells)) {
      if (node.id === firstId) first = { row: index, column: Number(column) };
      if (node.id === lastId) last = { row: index, column: Number(column) };
    }
  });
  if (!first || !last) return [lastId];
  const start = first;
  const end = last;
  return grid.rows.flatMap((row, index) => {
    if (
      index < Math.min(start.row, end.row) ||
      index > Math.max(start.row, end.row)
    )
      return [];
    return grid.columns
      .filter(
        (column) =>
          column.index >= Math.min(start.column, end.column) &&
          column.index <= Math.max(start.column, end.column),
      )
      .map((column) => row.cells[column.index]?.id)
      .filter((id): id is string => !!id);
  });
}

export function evidenceKey(evidence: Evidence): string {
  return [
    evidence.document_id,
    evidence.sheet,
    evidence.cell,
    evidence.page,
    evidence.rect?.join(","),
    evidence.locator,
  ].join("|");
}

export function collectSources(
  result: CaseResult,
  nodeIds: string[],
): SourceReference[] {
  const sources = new Map<string, SourceReference>();
  const documents = new Map(
    result.documents.map((document) => [document.id, document]),
  );
  const pending = [...nodeIds].reverse();
  const visited = new Set<string>();
  while (pending.length) {
    const id = pending.pop()!;
    if (visited.has(id)) continue;
    visited.add(id);
    const node = result.nodes[id];
    if (!node) continue;
    const evidence = node.evidence || node.reported_evidence;
    const document =
      evidence?.document_id && documents.get(evidence.document_id);
    if (document && document.role === "source" && evidence) {
      if (!sources.has(document.id))
        sources.set(document.id, { document, evidence: [], nodeIds: [] });
      const source = sources.get(document.id)!;
      source.nodeIds.push(id);
      if (
        !source.evidence.some(
          (item) => evidenceKey(item) === evidenceKey(evidence),
        )
      )
        source.evidence.push(evidence);
    }
    const formulaInputs = node.inputs
      ? node.inputs.map((input) => input.ref)
      : node.dependencies;
    pending.push(...[...formulaInputs].reverse());
  }
  return [...sources.values()];
}

export function isSameEvidence(left?: Evidence, right?: Evidence): boolean {
  return !!left && !!right && evidenceKey(left) === evidenceKey(right);
}
