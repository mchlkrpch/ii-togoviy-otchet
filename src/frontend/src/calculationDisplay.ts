import type { Node } from "./types";

export function calculationExpression(node: Node): string {
  return node.rule?.expression?.trim() || node.formula?.trim() || "";
}
