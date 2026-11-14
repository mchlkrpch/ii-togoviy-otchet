import type { Node } from "./types";

export function calculationExpression(node: Node): string {
  return node.rule?.expression?.trim() || node.formula?.trim() || "";
}

export function calculationStatus(node: Node) {
  return node.numeric_status === "mismatch" ? "mismatch" : node.status;
}
