import { Ellipsis, CircleAlert, CircleHelp, CircleCheck, Pencil, Sparkles } from "lucide-react";
import { amount, statusLabels } from "./api";
import { calculationExpression, calculationStatus } from "./calculationDisplay";
import type { Input, Node } from "./types";

export default function FormulaOverlay({
  node,
  onVariable,
  onDetails,
  message,
  onEdit,
  onPredict,
  onResult,
}: {
  node: Node;
  onVariable: (input: Input) => void;
  onDetails: () => void;
  message?: string;
  onEdit?: () => void;
  onPredict?: () => void;
  onResult?: () => void;
}) {
  const expression = calculationExpression(node);
  const inputs = new Map(node.inputs.map((input) => [input.name, input]));
  const tokens = expression.split(/([\p{L}_][\p{L}\p{N}_]*)/u);
  const status = calculationStatus(node);
  const confirmed = status === "verified";
  const mismatch = status === "mismatch";
  const Icon = confirmed ? CircleCheck : mismatch ? CircleAlert : CircleHelp;
  const different =
    Boolean(expression) && node.reported != null && node.expected != null && node.delta != null && Number(node.delta) !== 0;
  return (
    <div className={`formula-overlay viewer-pill ${mismatch ? "mismatch" : ""}`} aria-label="Формула выбранной ячейки">
      <div className="formula-primary-row">
        {(expression || mismatch) && <span
          className={`formula-status-pill ${confirmed ? "confirmed" : mismatch ? "mismatch" : "question"}`}
        >
          <Icon size={15} />
          {confirmed ? statusLabels.verified : mismatch ? statusLabels.mismatch : "Вопрос"}
        </span>}
        <div className="formula-expression-pill">
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
          {onPredict && (
            <button className="button secondary formula-predict-button" onClick={onPredict}>
              <Sparkles size={14} /> Предсказать ИИ
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
      </div>
      {different && (
        <div className="formula-comparison-pill">
          <span>
            Сейчас <strong>{amount(node.reported, node.unit)}</strong>
          </span>
          <span>
            Должно быть <strong>{amount(node.expected, node.unit)}</strong>
          </span>
          <span className="formula-delta" title="Значение отчета минус ожидаемое значение по источникам">
            Расхождение <strong>{Number(node.delta) > 0 ? "+" : ""}{amount(node.delta, node.unit)}{!node.unit || node.unit === "RUB" ? " ₽" : ""}</strong>
          </span>
        </div>
      )}
      {expression && message && (
        <span className="formula-source-message" role="status">
          {message}
        </span>
      )}
    </div>
  );
}
