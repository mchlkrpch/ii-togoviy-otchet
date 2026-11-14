import { useEffect, useState } from "react";
import {
  ArrowRight,
  CircleAlert,
  FileCheck2,
  PanelRightClose,
  Pencil,
} from "lucide-react";
import { amount } from "./api";
import { Badge, DependencyDetails, SourceRow } from "./components";
import type { CaseResult, Edit, Evidence, Node } from "./types";

type Props = {
  result: CaseResult;
  selected?: Node;
  onClose: () => void;
  onRule: (node: Node) => void;
  onEdit: (edit: Edit) => void;
  onEvidence: (evidence?: Evidence) => void;
  onSelect: (id: string) => void;
};

export default function CalculationInspector({
  result,
  selected,
  onClose,
  onRule,
  onEdit,
  onEvidence,
  onSelect,
}: Props) {
  const [detailTab, setDetailTab] = useState("calculation");
  useEffect(() => setDetailTab("calculation"), [selected?.id]);
  if (!selected)
    return (
      <aside className="detail-panel inspector-empty">
        <FileCheck2 size={25} />
        <p>Выберите ячейку отчета, чтобы открыть расчет и источники.</p>
      </aside>
    );
  return (
    <aside className="detail-panel">
      <div className="detail-heading">
        <div>
          <span className="eyebrow">
            {selected.code || "ИСХОДНОЕ ЗНАЧЕНИЕ"}
          </span>
          <h2>{selected.title || selected.label}</h2>
        </div>
        <button
          className="icon-button"
          onClick={() => onClose()}
          title="Скрыть карточку"
        >
          <PanelRightClose size={18} />
        </button>
      </div>
      <div className="detail-status">
        <Badge status={selected.status} />
        {selected.rule_manual && (
          <span className="manual-tag">Ручная формула</span>
        )}
      </div>
      <div className="detail-tabs">
        {[
          ["calculation", "Расчет"],
          ["sources", "Источники"],
          ["dependencies", "Зависимости"],
        ].map(([id, title]) => (
          <button
            key={id}
            className={detailTab === id ? "active" : ""}
            onClick={() => setDetailTab(id)}
          >
            {title}
          </button>
        ))}
      </div>
      {detailTab === "calculation" && (
        <>
          {selected.kind === "check" && (
            <div className="comparison">
              <div>
                <span>В отчете</span>
                <button onClick={() => onEvidence(selected.reported_evidence)}>
                  {amount(selected.reported)}
                </button>
                {selected.reported_parts ? (
                  <small>
                    Сумма граф{" "}
                    {selected.reported_parts
                      .map((id) => result.nodes[id].code)
                      .join(" + ")}
                  </small>
                ) : (
                  !selected.id.startsWith("base_") && (
                    <button
                      className="inline-edit"
                      onClick={() =>
                        onEdit({
                          kind: "reported",
                          target: selected.id,
                          title: selected.title || "",
                          value: selected.reported || "",
                          evidence: selected.reported_evidence,
                        })
                      }
                    >
                      <Pencil size={11} />
                      Исправить OCR
                    </button>
                  )
                )}
              </div>
              <span className="comparison-sign">↔</span>
              <div>
                <span>По источникам</span>
                <strong>{amount(selected.expected)}</strong>
                <small>
                  {selected.expected == null
                    ? "Нет полного расчета"
                    : "Расчет до копейки"}
                </small>
              </div>
            </div>
          )}
          {selected.note && <p className="detail-note">{selected.note}</p>}
          {!!selected.explanation?.length && (
            <div className="check-explanation">
              <h3>Почему такой результат</h3>
              {selected.explanation.map((message) => (
                <p key={message}>{message}</p>
              ))}
            </div>
          )}
          <div className="section-title">
            <h3>Формула проверки</h3>
            {selected.kind === "check" && (
              <button
                className="button text small"
                onClick={() => onRule(selected)}
              >
                <Pencil size={12} />
                Изменить
              </button>
            )}
          </div>
          <div className="formula-box">
            <code>
              {selected.rule?.expression ||
                selected.formula ||
                (selected.inputs.length
                  ? selected.inputs.map((item) => item.name).join(" + ")
                  : "Нет привязанных источников")}
            </code>
            {selected.expected != null && (
              <strong>= {amount(selected.expected)}</strong>
            )}
          </div>
          {selected.formula_error && (
            <div className="note warning">
              <CircleAlert size={15} />
              {selected.formula_error}
            </div>
          )}
          {selected.substitution && (
            <div className="formula-substitution">
              <span>Подстановка чисел</span>
              <code>
                {selected.substitution} = {amount(selected.expected)}
              </code>
            </div>
          )}
          {selected.coverage_issues?.map((message) => (
            <div key={message} className="note warning">
              {message}. Показана доступная часть суммы.
            </div>
          ))}
          <div className="inputs-list">
            {selected.inputs.map((item) => (
              <SourceRow
                key={item.name}
                item={item}
                result={result}
                onEvidence={onEvidence}
                onNode={onSelect}
              />
            ))}
          </div>
          {!selected.inputs.length && (
            <p className="muted detail-note">
              Привяжите документ или показатель через «Изменить». Недостаток
              данных сохраняется до появления подтверждения.
            </p>
          )}
          {!!selected.arithmetic?.length && (
            <div className="arithmetic-section">
              <h3>Арифметика внутри отчета</h3>
              {selected.arithmetic.map((group) => (
                <div key={group.label} className="arithmetic-item">
                  <span>
                    {group.label}
                    <small>
                      {group.refs
                        .map((id) => result.nodes[id].code)
                        .join(" + ")}
                    </small>
                  </span>
                  <strong>{amount(group.expected)}</strong>
                  <Badge
                    status={group.status}
                    label={group.status === "verified" ? "Сходится" : undefined}
                  />
                </div>
              ))}
            </div>
          )}
          {!!selected.blocked_by.length && (
            <div className="dependency-warning">
              <CircleAlert size={17} />
              <div>
                <strong>Есть сомнения в исходных показателях</strong>
                <p>
                  Статус зависит от {selected.blocked_by.length}{" "}
                  неподтвержденных значений. Они автоматически передаются в
                  зависимые итоги.
                </p>
                <button
                  className="button text small"
                  onClick={() => setDetailTab("dependencies")}
                >
                  Проверить зависимости <ArrowRight size={13} />
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {detailTab === "sources" && (
        <div className="detail-sources">
          <button
            className="source-document"
            onClick={() =>
              onEvidence(selected.reported_evidence || selected.evidence)
            }
          >
            <FileCheck2 size={19} />
            <span>
              Значение в исходном документе
              <small>
                {(selected.reported_evidence || selected.evidence)?.locator}
              </small>
            </span>
            <ArrowRight size={14} />
          </button>
          {selected.inputs.map((item) => (
            <SourceRow
              key={item.name}
              item={item}
              result={result}
              onEvidence={onEvidence}
              onNode={onSelect}
            />
          ))}
        </div>
      )}
      {detailTab === "dependencies" && (
        <DependencyDetails
          node={selected}
          result={result}
          onSelect={onSelect}
        />
      )}
    </aside>
  );
}
