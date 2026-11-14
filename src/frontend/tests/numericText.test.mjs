import assert from "node:assert/strict";
import test from "node:test";
import { canonicalNumber, numberSpans } from "../src/numericText.ts";
import { collectSources } from "../src/workbenchModel.ts";

test("numeric highlighting excludes field labels, dates and unrelated amounts", () => {
  const text =
    "Стоимость оказанных услуг: 245 000,00 руб. от 15.05.2026; ранее 300 000,00";
  const spans = numberSpans(text, ["245000.00"]);
  assert.deepEqual(
    spans.map((span) => span.text),
    ["245 000,00"],
  );
  assert.equal(text.slice(spans[0].start, spans[0].end), "245 000,00");
  assert.deepEqual(numberSpans(text, ["0.00"]), []);
  assert.equal(canonicalNumber("−000,00"), "0");
  assert.notEqual(
    canonicalNumber("999999999999999.01"),
    canonicalNumber("999999999999999.02"),
  );
  assert.deepEqual(
    numberSpans("Ставка 0,12345", ["0.12345"]).map((span) => span.text),
    ["0,12345"],
  );
});

test("primary documents follow formula variables and exclude status-only dependencies", () => {
  const result = {
    documents: [
      { id: "used", role: "source" },
      { id: "other", role: "source" },
    ],
    nodes: {
      report: {
        inputs: [{ ref: "amount" }],
        dependencies: ["amount", "status"],
      },
      amount: {
        evidence: { document_id: "used" },
        inputs: [],
        dependencies: [],
      },
      status: {
        evidence: { document_id: "other" },
        inputs: [],
        dependencies: [],
      },
    },
  };
  assert.deepEqual(
    collectSources(result, ["report"]).map((source) => source.document.id),
    ["used"],
  );
  result.nodes.report.inputs = [];
  assert.deepEqual(collectSources(result, ["report"]), []);
});
