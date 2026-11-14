import { canonicalNumber } from "./numericText.ts";
import { collectSources, evidenceKey } from "./workbenchModel.ts";
import type {
  CaseResult,
  Evidence,
  EvidenceAnnotation,
  FormulaDraft,
  NumberSelection,
  Rule,
} from "./types";

export function draftRule(draft: FormulaDraft): Rule {
  const names = new Set(draft.expression.match(/\bx[1-9]\d*\b/g) || []);
  return {
    expression: draft.expression,
    inputs: draft.inputs.filter((input) => names.has(input.name)),
  };
}
export function usedNewVariables(draft: FormulaDraft) {
  const refs = new Set(draftRule(draft).inputs.map((input) => input.ref));
  return draft.variables.filter((variable) =>
    refs.has(`${variable.document_id}:${variable.key}`),
  );
}
export function nextVariableName(inputs: { name: string }[]): string {
  const used = new Set(inputs.map((input) => input.name));
  let number = 1;
  while (used.has(`x${number}`)) number++;
  return `x${number}`;
}
export function bindSelection(
  result: CaseResult,
  selection: NumberSelection,
): string | undefined {
  if (selection.ref && result.nodes[selection.ref]) return selection.ref;
  const doc = result.documents.find((doc) => doc.id === selection.document_id);
  if (!doc) return undefined;
  return Object.values(doc.facts).find((fact) => {
    if (fact.kind === "money" && selection.evidence.cell)
      return (
        fact.evidence.cell === selection.evidence.cell &&
        fact.evidence.sheet === selection.evidence.sheet
      );
    if (
      fact.kind !== "money" ||
      canonicalNumber(fact.original_value ?? fact.value ?? "") !==
        canonicalNumber(selection.value)
    )
      return false;
    const left = fact.evidence,
      right = selection.evidence;
    if (left.cell || right.cell)
      return left.cell === right.cell && left.sheet === right.sheet;
    if ((left.page || 1) !== (right.page || 1)) return false;
    if (left.rect && right.rect) {
      const x = (right.rect[0] + right.rect[2]) / 2,
        y = (right.rect[1] + right.rect[3]) / 2;
      return (
        x >= left.rect[0] - 0.005 &&
        x <= left.rect[2] + 0.005 &&
        y >= left.rect[1] - 0.005 &&
        y <= left.rect[3] + 0.005
      );
    }
    return left.locator === right.locator;
  })?.id;
}
export function insertVariable(
  draft: FormulaDraft,
  name: string,
  ref: string,
): FormulaDraft {
  const before = draft.expression.slice(0, draft.cursor),
    after = draft.expression.slice(draft.cursorEnd);
  const leading = /[\w)]$/.test(before) ? " " : "";
  const trailing = /^[\w(]/.test(after) ? " " : "";
  const cursor = before.length + leading.length + name.length;
  return {
    ...draft,
    expression: before + leading + name + trailing + after,
    inputs: [...draft.inputs, { name, ref }],
    cursor,
    cursorEnd: cursor,
  };
}
export function fileBindings(
  result: CaseResult,
): Map<string, EvidenceAnnotation[]> {
  const files = new Map<
    string,
    Map<string, { evidence: Evidence; names: Set<string> }>
  >();
  function add(evidence: Evidence, label: string) {
    if (!evidence.document_id) return;
    if (!files.has(evidence.document_id))
      files.set(evidence.document_id, new Map());
    const map = files.get(evidence.document_id)!;
    const key = evidenceKey(evidence);
    if (!map.has(key)) map.set(key, { evidence, names: new Set() });
    map.get(key)!.names.add(label);
  }
  for (const check of result.checks)
    for (const input of check.inputs) {
      const node = result.nodes[input.ref];
      if (node?.kind === "fact" && node.evidence)
        add(node.evidence, `${check.code || check.title} · ${input.name}`);
      else if (check.rule_manual && node?.reported_evidence)
        add(
          node.reported_evidence,
          `${check.code || check.title} · ${input.name}`,
        );
    }
  for (const variable of result.variables || [])
    if (!files.get(variable.document_id)?.has(evidenceKey(variable.evidence)))
      add(variable.evidence, "Сохраненная переменная");
  return new Map(
    [...files].map(([id, map]) => [
      id,
      [...map.values()].map((item) => ({
        evidence: item.evidence,
        label: [...item.names].join(" / "),
      })),
    ]),
  );
}
export function variableSources(result: CaseResult, ref: string) {
  return collectSources(result, [ref]);
}
