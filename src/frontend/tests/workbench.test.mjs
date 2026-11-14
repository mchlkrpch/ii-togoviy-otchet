import assert from "node:assert/strict";
import test from "node:test";
import {
  buildReportGrid,
  cellPosition,
  collectSources,
  columnName,
  reportChecks,
  reportColumn,
  selectionRange,
} from "../src/workbenchModel.ts";

function check(id, row, column) {
  return {
    id,
    row,
    column,
    title: `Графа ${column}`,
    dependencies: [],
    reported_evidence: { document_id: "report" },
  };
}

test("report selection is rectangular across rows, preserves gaps and supports reverse direction", () => {
  const nodes = [
    check("a", "2025", 3),
    check("b", "2025", 4),
    check("c", "2026", 3),
    check("d", "2026", 4),
    check("outside", "2026", 5),
  ];
  const grid = buildReportGrid(nodes);
  assert.deepEqual(selectionRange(grid, "a", "d"), ["a", "b", "c", "d"]);
  assert.deepEqual(selectionRange(grid, "d", "a"), ["a", "b", "c", "d"]);
  assert.deepEqual(
    selectionRange(
      buildReportGrid(nodes.filter((node) => node.id !== "b")),
      "a",
      "d",
    ),
    ["a", "c", "d"],
  );
  assert.deepEqual(selectionRange(grid, "missing", "d"), ["d"]);
});

test("saved cases retain physical columns and native Excel rows", () => {
  assert.equal(reportColumn({ id: "r2026_c15" }), 15);
  assert.equal(
    reportColumn({ id: "cell", reported_evidence: { cell: "$AA$8" } }),
    27,
  );
  assert.equal(columnName(27), "AA");
  assert.deepEqual(cellPosition("$AA$8"), { column: 27, row: 8 });
  const grid = buildReportGrid([
    {
      ...check("a", undefined, 3),
      reported_evidence: { sheet: "Лист1", cell: "C8" },
    },
    {
      ...check("b", undefined, 4),
      reported_evidence: { sheet: "Лист1", cell: "D8" },
    },
  ]);
  assert.equal(grid.rows.length, 1);
  assert.equal(grid.rows[0].cells[4].id, "b");
});

test("source highlighting follows all prerequisites, deduplicates aliases and terminates on cycles", () => {
  const evidence = (cell) => ({
    document_id: "payroll",
    sheet: "Ведомость",
    cell,
    locator: cell,
  });
  const result = {
    documents: [
      { id: "report", role: "report" },
      { id: "payroll", role: "source" },
      { id: "unrelated", role: "source" },
    ],
    nodes: {
      total: {
        reported_evidence: { document_id: "report" },
        dependencies: ["sum", "alias", "missing"],
      },
      sum: { evidence: evidence("C8"), dependencies: ["salary", "reserve"] },
      alias: { evidence: evidence("C8"), dependencies: ["sum"] },
      salary: { evidence: evidence("C6"), dependencies: [] },
      reserve: { evidence: evidence("C7"), dependencies: ["total"] },
    },
  };
  const sources = collectSources(result, ["total"]);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].document.id, "payroll");
  assert.deepEqual(
    sources[0].evidence.map((item) => item.cell),
    ["C8", "C6", "C7"],
  );
  assert.deepEqual(
    new Set(sources[0].nodeIds),
    new Set(["sum", "salary", "reserve", "alias"]),
  );
});

test("report grid excludes synthetic and primary-document checks", () => {
  const real = check("real", "2026", 3);
  const synthetic = {
    ...check("synthetic", "2026", 4),
    reported_parts: ["real"],
  };
  const primary = {
    ...check("primary", "2026", 5),
    reported_evidence: { document_id: "payroll" },
  };
  assert.deepEqual(
    reportChecks({ report_id: "report", checks: [real, synthetic, primary] }),
    [real],
  );
});
