import { Ellipsis, CircleHelp, CircleCheck, Pencil } from "lucide-react";
import { amount } from "./api";
import { calculationExpression } from "./calculationDisplay";
import type { Input, Node } from "./types";

export default function FormulaOverlay({
  node,
  onVariable,
  onDetails,
  message,
  onEdit,
  onResult,
}: {
  node: Node;
  onVariable: (input: Input) => void;
  onDetails: () => void;
  message?: string;
  onEdit?: () => void;
  onResult?: () => void;
}) {
  const expression = calculationExpression(node);
  const inputs = new Map(node.inputs.map((input) => [input.name, input]));
  const tokens = expression.split(/([\p{L}_][\p{L}\p{N}_]*)/u);
  const confirmed = node.status === "verified";
  const Icon = confirmed ? CircleCheck : CircleHelp;
  const different =
    Boolean(expression) && node.reported != null && node.expected != null && Number(node.delta) !== 0;
  return (
    <div className="formula-overlay" aria-label="Формула выбранной ячейки">
      {expression && <span
        className={`formula-status-pill ${confirmed ? "confirmed" : "question"}`}
      >
        <Icon size={15} />
        {confirmed ? "Подтверждено" : "Вопрос"}
      </span>}
      <div className="formula-expression-pill viewer-pill">
        <span className="formula-cell-code">{node.code || node.key}</span>
        <code>
          {!expression && <span className="formula-cell-value">{amount(node.reported ?? node.value ?? node.expected, node.unit)}</span>}
          {tokens.map((token, index) =>
            inputs.has(token) ? (
              <button
                key={index}
                className="formula-variable"
                aria-label={`Показать источник ${token}`}
                title={`${token} = ${amount(inputs.get(token)?.value, node.unit)}`}
                onClick={() => onVariable(inputs.get(token)!)}
              >
                {token}
              </button>
            ) : (
              <span key={index}>{token}</span>
            ),
          )}
          {expression && node.expected != null && (
            <button
              className="formula-result-link"
              title="Вернуться к отчету с сохраненным масштабом и позицией"
              onClick={onResult}
            >
              {" "}
              = {amount(node.expected, node.unit)}
            </button>
          )}
        </code>
        {onEdit && (
          <button
            className="icon-button"
            aria-label="Редактировать формулу"
            title="Редактировать формулу"
            onClick={onEdit}
          >
            <Pencil size={14} />
          </button>
        )}
        {expression && <button
          className="icon-button"
          aria-label="Подробности расчета и исправления"
          onClick={onDetails}
        >
          <Ellipsis size={17} />
        </button>}
      </div>
      {different && (
        <div className="formula-comparison-pill viewer-pill">
          <span>
            Сейчас <strong>{amount(node.reported, node.unit)}</strong>
          </span>
          <span>
            Должно быть <strong>{amount(node.expected, node.unit)}</strong>
          </span>
        </div>
      )}
      {expression && message && (
        <span className="formula-source-message viewer-pill" role="status">
          {message}
        </span>
      )}
    </div>
  );
}
