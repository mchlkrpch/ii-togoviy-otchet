import assert from "node:assert/strict";
import test from "node:test";
import {
  bindSelection,
  draftRule,
  fileBindings,
  insertVariable,
  nextVariableName,
  removeFormulaVariable,
  usedNewVariables,
} from "../src/formulaModel.ts";

test("removing a variable preserves the sum wrapper and remaining source bindings", () => {
  const inputs = ["x1", "x2", "x12"].map((name) => ({ name, ref: name }));
  const draft = { expression: "ROUND(x1 + x2 + x12 + x2, 2)", inputs, variables: [] };
  const edited = removeFormulaVariable(draft, "x2");
  assert.equal(edited.expression, "ROUND(x1 + x12, 2)");
  assert.deepEqual(draftRule(edited).inputs, [inputs[0], inputs[2]]);
  assert.equal(removeFormulaVariable({ ...draft, expression: "SUM(x1, x2, x12)" }, "x1").expression, "SUM(x2, x12)");
  const ratio = removeFormulaVariable({ ...draft, expression: "x1 / x2" }, "x2");
  assert.equal(ratio.expression.slice(ratio.cursor, ratio.cursorEnd), "x2");
  assert.deepEqual(ratio.inputs, inputs);
});

test("insertion uses the editor caret and saves only referenced bindings", () => {
  const draft = {
    target: "report",
    expression: "x1 + ",
    inputs: [{ name: "x1", ref: "old" }],
    variables: [
      { key: "manual_a", document_id: "doc", value: "12.34", evidence: {} },
    ],
    reason: "Checked",
    cursor: 5,
    cursorEnd: 5,
  };
  const name = nextVariableName(draft.inputs);
  assert.equal(name, "x2");
  const inserted = insertVariable(draft, name, "doc:manual_a");
  assert.equal(inserted.expression, "x1 + x2");
  assert.deepEqual(draftRule(inserted).inputs, [
    { name: "x1", ref: "old" },
    { name: "x2", ref: "doc:manual_a" },
  ]);
  assert.equal(usedNewVariables(inserted).length, 1);
  assert.equal(usedNewVariables({ ...inserted, expression: "x1" }).length, 0);
});

test("existing spreadsheet formulas stay linked to source cells and saved marks are independent of selection", () => {
  const evidence = {
    document_id: "doc",
    sheet: "Лист",
    cell: "C8",
    text: "123,00",
    locator: "Лист!C8",
  };
  const result = {
    report_id: "report",
    documents: [
      {
        id: "doc",
        facts: {
          total: { id: "doc:total", kind: "money", value: "124", evidence },
        },
      },
    ],
    nodes: { "doc:total": { id: "doc:total", kind: "fact", evidence } },
    checks: [
      { id: "check", code: "0200", inputs: [{ name: "x2", ref: "doc:total" }] },
    ],
  };
  assert.equal(
    bindSelection(result, { document_id: "doc", value: "123", evidence }),
    "doc:total",
  );
  assert.equal(fileBindings(result).get("doc")[0].evidence.cell, "C8");
  assert.ok(fileBindings(result).get("doc")[0].label.includes("x2"));
});
