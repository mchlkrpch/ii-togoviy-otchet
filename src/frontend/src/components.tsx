import DocumentViewer from "./DocumentViewer";
import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCheck,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  CircleHelp,
  Clock3,
  FileCheck2,
  FileSpreadsheet,
  Files,
  FolderOpen,
  Loader2,
  Pencil,
  Search,
  Upload,
  X,
} from "lucide-react";
import { amount, statusLabels } from "./api";
import type {
  CaseResult,
  ChangeRequest,
  Context,
  Document,
  Evidence,
  Node,
  Rule,
  Status,
  Edit,
} from "./types";

export function Badge({ status, label }: { status: Status; label?: string }) {
  const Icon =
    status === "verified"
      ? CircleCheck
      : status === "mismatch"
        ? CircleHelp
        : status === "review"
          ? CircleAlert
          : Clock3;
  return (
    <span className={`badge ${status}`}>
      <Icon size={13} />
      {label || statusLabels[status]}
    </span>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty-inline">{children}</div>;
}

export function SourceRow({
  item,
  result,
  onEvidence,
  onNode,
}: {
  item: Node["inputs"][number];
  result: CaseResult;
  onEvidence: (evidence?: Evidence) => void;
  onNode: (id: string) => void;
}) {
  const node = result.nodes[item.ref];
  const evidence = item.evidence || node?.evidence || node?.reported_evidence;
  const document = result.documents.find(
    (document) => document.id === evidence?.document_id,
  );
  return (
    <div className="input-row">
      <span className="input-alias">{item.name}</span>
      <div>
        <button className="input-title" onClick={() => onEvidence(evidence)}>
          {document?.facts.number?.value ||
            node?.title ||
            node?.label ||
            item.ref}
          <ArrowRight size={12} />
        </button>
        <small>{document?.name || item.label}</small>
        <button className="locator-link" onClick={() => onEvidence(evidence)}>
          {evidence?.locator || "Открыть источник"}
        </button>
      </div>
      <div className="input-value">
        <strong>{amount(item.value)}</strong>
        {node && (
          <button
            onClick={() => onNode(item.ref)}
            title="Открыть расчет исходного значения"
          >
            <Badge
              status={node.status}
              label={node.inputs.length ? "Расчет" : "Источник"}
            />
          </button>
        )}
      </div>
    </div>
  );
}

export function DependencyDetails({
  node,
  result,
  onSelect,
}: {
  node: Node;
  result: CaseResult;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="dependencies-list">
      <h3>
        Исходные значения <span>{node.dependencies.length}</span>
      </h3>
      {node.dependencies.length ? (
        node.dependencies.map((id) => (
          <NodeButton
            key={id}
            node={result.nodes[id]}
            onClick={() => onSelect(id)}
          />
        ))
      ) : (
        <p className="muted">Это исходное значение из документа.</p>
      )}
      <h3>
        Зависимые показатели <span>{node.dependents.length}</span>
      </h3>
      {node.dependents.length ? (
        node.dependents.map((id) => (
          <NodeButton
            key={id}
            node={result.nodes[id]}
            onClick={() => onSelect(id)}
          />
        ))
      ) : (
        <p className="muted">Другие показатели от этого значения не зависят.</p>
      )}
    </div>
  );
}

export function NodeButton({
  node,
  onClick,
}: {
  node: Node;
  onClick: () => void;
}) {
  return (
    <button className={`node-button ${node.status}`} onClick={onClick}>
      <span className="node-code">
        {node.code || node.evidence?.cell || "ИСТОЧНИК"}
      </span>
      <strong>{node.title || node.label}</strong>
      <span className="node-value">{amount(node.expected)}</span>
      <Badge status={node.status} />
    </button>
  );
}

export function GraphView({
  result,
  selected,
  onSelect,
}: {
  result: CaseResult;
  selected?: Node;
  onSelect: (id: string) => void;
}) {
  const node =
    selected ||
    result.checks.find(
      (check) =>
        check.code === "total" && check.row === result.context.target_year,
    ) ||
    result.checks[0];
  return (
    <main className="content-panel">
      <div className="panel-heading">
        <div>
          <h2>От источника к итогу</h2>
          <p>
            Нажмите на значение, чтобы пройти по цепочке. Сомнение в исходном
            значении влияет на статус зависимых показателей.
          </p>
        </div>
        <select
          value={node.id}
          onChange={(event) => onSelect(event.target.value)}
          aria-label="Показатель графа"
        >
          {result.checks.map((check) => (
            <option key={check.id} value={check.id}>
              {check.row_title} · {check.code} · {check.title}
            </option>
          ))}
        </select>
      </div>
      <div className="graph-lanes">
        <section>
          <h3>
            Исходные значения <span>{node.dependencies.length}</span>
          </h3>
          {node.dependencies.map((id) => (
            <NodeButton
              key={id}
              node={result.nodes[id]}
              onClick={() => onSelect(id)}
            />
          ))}
          {!node.dependencies.length && (
            <Empty>Исходный реквизит документа</Empty>
          )}
        </section>
        <span className="graph-arrow">
          <ArrowRight size={24} />
        </span>
        <section className="graph-current">
          <h3>Выбранный показатель</h3>
          <NodeButton node={node} onClick={() => {}} />
          <div className="note">
            {node.note || "Значение извлечено из первичного документа."}
          </div>
          {node.blocked_by.length > 0 && (
            <div className="note warning">
              Сомнения в {node.blocked_by.length} исходных значениях
            </div>
          )}
        </section>
        <span className="graph-arrow">
          <ArrowRight size={24} />
        </span>
        <section>
          <h3>
            Зависимые итоги <span>{node.dependents.length}</span>
          </h3>
          {node.dependents.map((id) => (
            <NodeButton
              key={id}
              node={result.nodes[id]}
              onClick={() => onSelect(id)}
            />
          ))}
          {!node.dependents.length && <Empty>Конечный показатель</Empty>}
        </section>
      </div>
    </main>
  );
}

export function DocumentsView({
  result,
  onOpen,
  onChange,
  busy,
}: {
  result: CaseResult;
  onOpen: (id: string) => void;
  onChange: (change: ChangeRequest) => Promise<boolean | undefined>;
  busy: boolean;
}) {
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [action, setAction] = useState<{
    document: Document;
    values: Record<string, unknown>;
  } | null>(null);
  return (
    <main className="content-panel">
      <div className="panel-heading">
        <div>
          <h2>
            Реестр документов <span>{result.documents.length}</span>
          </h2>
          <p>
            Сопоставление по договору, дате, номеру документа и содержанию
            операции
          </p>
        </div>
        <label className="search-field">
          <Search size={15} />
          <input
            placeholder="Номер или имя файла"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Поиск документа"
          />
        </label>
      </div>
      <div className="status-tabs document-filters">
        {[
          ["all", "Все документы"],
          ["included", "В расчете"],
          ["excluded", "Исключенные"],
          ["review", "Требуют проверки"],
        ].map(([id, title]) => (
          <button
            key={id}
            className={filter === id ? "active" : ""}
            onClick={() => setFilter(id)}
          >
            {title}
          </button>
        ))}
      </div>
      <div className="table-scroll">
        <table className="documents-table">
          <thead>
            <tr>
              <th>Документ</th>
              <th>Договор / дата</th>
              <th>Статья</th>
              <th>Сумма, ₽</th>
              <th>Участие в расчете</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {result.documents
              .filter(
                (document) =>
                  (filter === "all" || document.state === filter) &&
                  `${document.name} ${document.facts.number?.value}`
                    .toLowerCase()
                    .includes(query.toLowerCase()),
              )
              .map((document) => (
                <tr key={document.id}>
                  <td>
                    <button
                      className="document-name"
                      onClick={() => onOpen(document.id)}
                    >
                      <span className={`file-icon ${document.format}`}>
                        <FileSpreadsheet size={19} />
                      </span>
                      <span>
                        <strong>
                          {document.facts.number?.value || document.name}
                        </strong>
                        <small>{document.name}</small>
                      </span>
                    </button>
                  </td>
                  <td>
                    {document.facts.contract?.value || "—"}
                    <small>
                      {document.facts.date?.value || "Дата не найдена"}
                    </small>
                  </td>
                  <td>
                    {document.role === "source" ? (
                      <select
                        value={document.category || ""}
                        onChange={(event) =>
                          setAction({
                            document,
                            values: { category: event.target.value || null },
                          })
                        }
                        aria-label={`Статья ${document.name}`}
                      >
                        <option value="">Уточнить</option>
                        {["0100", "0200", "0300", "0800", "0900"].map(
                          (code) => (
                            <option key={code}>{code}</option>
                          ),
                        )}
                      </select>
                    ) : (
                      <span className="muted">
                        {document.role === "report" ? "Отчет" : "Копия отчета"}
                      </span>
                    )}
                  </td>
                  <td className="numeric">
                    {amount(document.facts.amount?.value)}
                  </td>
                  <td>
                    <Badge
                      status={
                        document.state === "included"
                          ? "verified"
                          : document.state === "review"
                            ? "review"
                            : "excluded"
                      }
                      label={
                        document.state === "included" ? "В расчете" : undefined
                      }
                    />
                    <small>
                      {result.issues
                        .filter((issue) => issue.document_id === document.id)
                        .map((issue) => issue.title)
                        .join(" · ")}
                    </small>
                  </td>
                  <td>
                    {document.role === "source" && (
                      <button
                        className="button text small"
                        disabled={busy}
                        onClick={() =>
                          setAction({
                            document,
                            values: { include: !document.included },
                          })
                        }
                      >
                        {document.included ? "Исключить" : "Включить"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {action && (
        <ReasonDialog
          title={
            action.values.category !== undefined
              ? "Изменить статью документа"
              : action.document.included
                ? "Исключить документ из расчета"
                : "Включить документ в расчет"
          }
          description={action.document.name}
          busy={busy}
          onClose={() => setAction(null)}
          onSave={async (reason) => {
            if (
              await onChange({
                kind: "document",
                target: action.document.id,
                values: action.values,
                reason,
              })
            )
              setAction(null);
          }}
        />
      )}
    </main>
  );
}

export function SettingsView({
  context,
  onChange,
  busy,
  onDemo,
}: {
  context: Context;
  onChange: (change: ChangeRequest) => Promise<boolean | undefined>;
  busy: boolean;
  onDemo: () => void;
}) {
  const [values, setValues] = useState(context);
  const [reason, setReason] = useState("");
  useEffect(() => setValues(context), [context]);
  return (
    <main className="content-panel settings-panel">
      <div className="panel-heading">
        <div>
          <h2>Область проверки</h2>
          <p>
            Изменение параметров пересчитывает состав документов и все зависимые
            показатели
          </p>
        </div>
      </div>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (
            await onChange({
              kind: "context",
              target: "settings",
              values,
              reason,
            })
          )
            setReason("");
        }}
      >
        <label>
          Номер договора
          <input
            required
            value={values.contract}
            onChange={(event) =>
              setValues({ ...values, contract: event.target.value })
            }
          />
        </label>
        <div className="form-grid">
          <label>
            Начало периода
            <input
              required
              type="date"
              value={values.start}
              onChange={(event) =>
                setValues({ ...values, start: event.target.value })
              }
            />
          </label>
          <label>
            Окончание периода
            <input
              required
              type="date"
              value={values.end}
              onChange={(event) =>
                setValues({ ...values, end: event.target.value })
              }
            />
          </label>
        </div>
        <div className="form-grid">
          <label>
            Год целевой строки
            <input
              required
              pattern="20[0-9]{2}"
              value={values.target_year}
              onChange={(event) =>
                setValues({ ...values, target_year: event.target.value })
              }
            />
          </label>
          <label>
            Допуск расхождения, ₽
            <input
              required
              inputMode="decimal"
              value={values.tolerance}
              onChange={(event) =>
                setValues({ ...values, tolerance: event.target.value })
              }
            />
          </label>
        </div>
        <label>
          Причина изменения
          <textarea
            required
            minLength={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Например, уточнен период подтверждающих документов"
          />
        </label>
        <button
          className="button primary"
          disabled={busy || reason.trim().length < 3}
        >
          {busy ? <Loader2 size={15} className="spin" /> : <Check size={15} />}
          Сохранить и пересчитать
        </button>
      </form>
      <div className="settings-help">
        <h3>Как считается результат</h3>
        <p>
          Суммы рассчитываются с десятичной точностью. Дубликаты, другой договор
          и даты вне периода исключаются из автоматического расчета. Нулевое
          значение без источника требует подтверждения.
        </p>
        <p>
          Поддерживается форма КФЗ из набора. Для другой формы можно загрузить
          отчет и вручную привязать извлеченные показатели к документам через
          формулу.
        </p>
        <button className="button secondary" onClick={onDemo} disabled={busy}>
          <FolderOpen size={15} />
          Создать новую проверку из «вводные»
        </button>
      </div>
    </main>
  );
}

export function Modal({
  title,
  children,
  onClose,
  className = "",
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  className?: string;
}) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className={`modal ${className}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Закрыть"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

export function UploadDialog({
  onClose,
  onSubmit,
  onDemo,
  busy,
}: {
  onClose: () => void;
  onSubmit: (form: FormData) => Promise<void>;
  onDemo: () => void;
  busy: boolean;
}) {
  const [reports, setReports] = useState<File[]>([]);
  const report = reports[0] || null;
  const [sources, setSources] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  return (
    <Modal title="Новая проверка" onClose={onClose}>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!report) return;
          setUploading(true);
          const element = event.currentTarget;
          const form = new FormData(element);
          form.delete("report");
          form.delete("reports");
          form.delete("sources");
          form.append("report", report);
          reports.slice(1).forEach((file) => form.append("reports", file));
          sources.forEach((file) => form.append("sources", file));
          form.append(
            "settings",
            JSON.stringify({
              contract: form.get("contract"),
              start: form.get("start"),
              end: form.get("end"),
              target_year: String(form.get("end") || "").slice(0, 4),
            }),
          );
          await onSubmit(form);
          setUploading(false);
        }}
      >
        <label>
          Название проверки
          <input name="name" defaultValue="Проверка отчетности" required />
        </label>
        <label className="upload-zone">
          <FileCheck2 size={25} />
          <strong>
            {reports.length > 1
              ? `Выбрано отчетов: ${reports.length}`
              : report?.name || "Выберите итоговый отчет"}
          </strong>
          <span>PDF, DOCX, XLSX или изображение</span>
          <input
            name="report"
            type="file"
            multiple
            accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.tif,.tiff"
            required
            onChange={(event) =>
              setReports(Array.from(event.target.files || []))
            }
          />
        </label>
        <label className="upload-zone compact">
          <Files size={23} />
          <strong>
            {sources.length
              ? `Выбрано документов: ${sources.length}`
              : "Добавьте первичные документы"}
          </strong>
          <input
            name="sources"
            type="file"
            multiple
            accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.tif,.tiff,.txt"
            onChange={(event) =>
              setSources(Array.from(event.target.files || []))
            }
          />
        </label>
        <details className="upload-settings" open>
          <summary>
            Договор и период проверки
            <ChevronDown size={14} />
          </summary>
          <p className="upload-scope-help">
            Укажите период для отбора документов. Если он записан в файле
            00_README, выберите этот файл вместе с первичкой. При пустом периоде
            результат будет предварительным.
          </p>
          <label>
            Номер договора
            <input name="contract" placeholder="24/03/15-01" />
          </label>
          <div className="form-grid">
            <label>
              Начало периода
              <input name="start" type="date" />
            </label>
            <label>
              Окончание периода
              <input name="end" type="date" />
            </label>
          </div>
        </details>
        <div className="modal-footer">
          <button
            className="button secondary"
            type="button"
            onClick={onDemo}
            disabled={busy || uploading}
          >
            <FolderOpen size={15} />
            Набор из «вводные»
          </button>
          <button
            className="button primary"
            disabled={busy || uploading || !report}
          >
            {uploading ? (
              <Loader2 size={15} className="spin" />
            ) : (
              <Upload size={15} />
            )}
            Начать проверку
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function EditDialog({
  edit,
  onClose,
  onSave,
  busy,
}: {
  edit: Edit;
  onClose: () => void;
  onSave: (value: string, reason: string) => void;
  busy: boolean;
}) {
  const [value, setValue] = useState(edit.value);
  const [reason, setReason] = useState("");
  return (
    <Modal title="Исправить извлеченное значение" onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSave(value, reason);
        }}
      >
        <p className="modal-description">{edit.title}</p>
        {edit.evidence && (
          <div className="note">
            <strong>{edit.evidence.locator}</strong>
            <p>{edit.evidence.text}</p>
          </div>
        )}
        <label>
          Уточненное значение
          <textarea
            required={
              edit.kind !== "cell" &&
              edit.kind !== "text" &&
              edit.kind !== "pdf_field"
            }
            autoFocus
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </label>
        <label>
          Причина исправления
          <textarea
            required
            minLength={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Что сверено в исходном документе"
          />
        </label>
        <p className="muted">
          После сохранения пересчитаются все зависимые показатели. Исходное
          значение останется в истории.
        </p>
        <div className="modal-footer">
          <button className="button secondary" type="button" onClick={onClose}>
            Отмена
          </button>
          <button
            className="button primary"
            disabled={busy || reason.trim().length < 3}
          >
            <Check size={15} />
            Сохранить и пересчитать
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function ReasonDialog({
  title,
  description,
  onClose,
  onSave,
  busy,
}: {
  title: string;
  description: string;
  onClose: () => void;
  onSave: (reason: string) => void;
  busy: boolean;
}) {
  const [reason, setReason] = useState("");
  return (
    <Modal title={title} onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSave(reason);
        }}
      >
        <p className="modal-description">{description}</p>
        <label>
          Основание изменения
          <textarea
            required
            autoFocus
            minLength={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Почему это изменение требуется"
          />
        </label>
        <div className="modal-footer">
          <button className="button secondary" type="button" onClick={onClose}>
            Отмена
          </button>
          <button
            className="button primary"
            disabled={busy || reason.trim().length < 3}
          >
            Применить и пересчитать
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function RuleDialog({
  node,
  result,
  onClose,
  onSave,
  busy,
}: {
  node: Node;
  result: CaseResult;
  onClose: () => void;
  onSave: (rule: Rule, reason: string, reset?: boolean) => void;
  busy: boolean;
}) {
  const [refs, setRefs] = useState(
    node.rule?.inputs.map((input) => input.ref) || [],
  );
  const [expression, setExpression] = useState(node.rule?.expression || "");
  const [query, setQuery] = useState("");
  const [reason, setReason] = useState("");
  const candidates = Object.values(result.nodes).filter(
    (candidate) =>
      candidate.id !== node.id &&
      (candidate.kind === "check" ||
        result.documents.some(
          (document) =>
            document.id === candidate.document_id &&
            document.role === "source" &&
            document.included,
        )) &&
      `${candidate.title || candidate.label} ${candidate.id} ${result.documents.find((document) => document.id === candidate.document_id)?.name || ""}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  function toggle(ref: string) {
    const next = refs.includes(ref)
      ? refs.filter((item) => item !== ref)
      : [...refs, ref];
    setRefs(next);
    setExpression(next.map((_, index) => `x${index + 1}`).join(" + "));
  }
  const rule = {
    expression,
    inputs: refs.map((ref, index) => ({ name: `x${index + 1}`, ref })),
  };
  return (
    <Modal
      title="Источники и формула проверки"
      onClose={onClose}
      className="rule-modal"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSave(rule, reason);
        }}
      >
        <p className="modal-description">
          {node.code} · {node.title}
        </p>
        <label>
          Формула
          <input
            className="formula-input"
            value={expression}
            required
            onChange={(event) => setExpression(event.target.value)}
            placeholder="x1 + x2"
          />
        </label>
        <small className="muted">
          Доступны +, −, *, /, SUM, MIN, MAX, AVERAGE, ROUND и скобки.
        </small>
        <div className="rule-selected">
          {refs.map((ref, index) => (
            <div key={ref}>
              <code>x{index + 1}</code>
              <span>
                {result.nodes[ref]?.title || result.nodes[ref]?.label}
              </span>
              <strong>{amount(result.nodes[ref]?.expected)}</strong>
              <button
                type="button"
                className="icon-button"
                onClick={() => toggle(ref)}
                aria-label="Убрать источник"
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
        <label className="search-field">
          <Search size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Найти ячейку, документ или показатель"
          />
        </label>
        <div className="rule-candidates">
          {candidates.map((candidate) => (
            <label key={candidate.id}>
              <input
                type="checkbox"
                checked={refs.includes(candidate.id)}
                onChange={() => toggle(candidate.id)}
              />
              <span>
                {candidate.title || candidate.label}
                <small>
                  {result.documents.find(
                    (document) => document.id === candidate.document_id,
                  )?.name || candidate.row_title}
                </small>
              </span>
              <strong>{amount(candidate.expected)}</strong>
            </label>
          ))}
        </div>
        <label>
          Причина изменения
          <textarea
            required
            minLength={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <div className="modal-footer">
          {node.rule_manual ? (
            <button
              type="button"
              className="button secondary"
              disabled={busy || reason.trim().length < 3}
              onClick={() => onSave(rule, reason, true)}
            >
              Вернуть автоматическую формулу
            </button>
          ) : (
            <button
              type="button"
              className="button secondary"
              onClick={onClose}
            >
              Отмена
            </button>
          )}
          <button
            className="button primary"
            disabled={busy || !refs.length || reason.trim().length < 3}
          >
            Сохранить формулу
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function DocumentDrawer({
  result,
  selection,
  onClose,
  onEdit,
  onChange,
  busy,
}: {
  result: CaseResult;
  selection: { documentId: string; evidence?: Evidence };
  onClose: () => void;
  onEdit: (edit: Edit) => void;
  onChange: (change: ChangeRequest) => Promise<boolean | undefined>;
  busy: boolean;
}) {
  const document = result.documents.find(
    (item) => item.id === selection.documentId,
  )!;
  const [location, setLocation] = useState(selection.evidence);
  const [reasonOpen, setReasonOpen] = useState(false);
  function editableFact(identifier: string) {
    let fact = Object.values(document.facts).find(
      (item) => item.id === identifier,
    );
    const seen = new Set<string>();
    while (
      fact?.dependencies?.length === 1 &&
      !fact.formula &&
      !seen.has(fact.id)
    ) {
      seen.add(fact.id);
      const reference = fact.dependencies[0];
      fact = Object.values(document.facts).find(
        (item) => item.id === reference,
      );
    }
    return fact?.formula || fact?.dependencies?.length ? undefined : fact;
  }
  return (
    <div className="drawer-backdrop">
      <section
        className="document-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={document.name}
      >
        <header>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Закрыть документ"
          >
            <ArrowLeft size={19} />
          </button>
          <div>
            <h2>{document.name}</h2>
            <span>
              {document.role === "report"
                ? "Итоговый отчет"
                : document.category
                  ? `Статья ${document.category}`
                  : "Первичный документ"}
            </span>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Закрыть"
          >
            <X size={20} />
          </button>
        </header>
        <div className="document-body">
          <div className="document-preview">
          <DocumentViewer
              onWorkbookChange={onChange}
              revision={result.revision}
              onEditField={onEdit}
              caseId={result.id}
              document={document}
              highlights={location ? [location] : []}
              activeEvidence={location}
              onSelectEvidence={setLocation}
            />
          </div>
          <aside className="document-facts">
            <h3>Извлеченные реквизиты</h3>
            {document.errors.map((message) => (
              <div className="note warning" key={message}>
                {message}
              </div>
            ))}
            {Object.values(document.facts)
              .filter((fact) => !fact.key.includes("!"))
              .map((fact) => (
                <div className="fact-row" key={fact.id}>
                  <span>
                    {fact.label}
                    {fact.manual && (
                      <small className="manual-tag">Исправлено</small>
                    )}
                  </span>
                  <div>
                    <button
                      onClick={() => {
                        setLocation(fact.evidence);
                      }}
                    >
                      {fact.kind === "money"
                        ? amount(fact.value)
                        : fact.value || "Не найдено"}
                    </button>
                    <button
                      className="icon-button"
                      disabled={!editableFact(fact.id)}
                      title={
                        editableFact(fact.id)
                          ? `Исправить ${fact.label}`
                          : "Вычисляется по формуле. Исправьте исходные ячейки."
                      }
                      onClick={() => {
                        const editable = editableFact(fact.id);
                        if (!editable) return;
                        onEdit({
                          kind: "fact",
                          target: editable.id,
                          title: fact.label,
                          value: editable.value || "",
                          evidence: editable.evidence,
                        });
                      }}
                    >
                      <Pencil size={13} />
                    </button>
                  </div>
                  {fact.needs_review && (
                    <small className="warning-text">
                      Требуется сверка с оригиналом
                    </small>
                  )}
                </div>
              ))}
            <div className="category-reason">
              <h3>Почему выбрана статья</h3>
              <p>{document.category_reason}</p>
              <Badge
                status={
                  document.state === "included"
                    ? "verified"
                    : document.state === "review"
                      ? "review"
                      : "excluded"
                }
                label={
                  document.included
                    ? "Участвует в расчете"
                    : "Исключен из расчета"
                }
              />
            </div>
            {document.role === "source" && (
              <button
                className="button secondary"
                onClick={() => setReasonOpen(true)}
                disabled={busy}
              >
                <CheckCheck size={15} />
                Реквизиты сверены вручную
              </button>
            )}
            {location && (
              <div className="evidence-note">
                <h3>Выбранное значение</h3>
                <p>{location.locator}</p>
                <blockquote>{location.text}</blockquote>
                <small>
                  {location.method} · оценка OCR {location.confidence}%
                </small>
                {location.ocr_candidates &&
                  location.ocr_candidates.length > 1 && (
                    <details>
                      <summary>Варианты распознавания</summary>
                      {location.ocr_candidates.map((candidate, index) => (
                        <p key={index}>
                          {candidate.raw} · {candidate.confidence}%
                        </p>
                      ))}
                    </details>
                  )}
              </div>
            )}
            {location?.cell &&
              document.facts[`${location.sheet}!${location.cell}`] &&
              !document.facts[`${location.sheet}!${location.cell}`].formula && (
                <button
                  className="button secondary"
                  onClick={() => {
                    const fact =
                      document.facts[`${location.sheet}!${location.cell}`];
                    onEdit({
                      kind: "fact",
                      target: fact.id,
                      title: `${location.sheet}!${location.cell}`,
                      value: fact.value || "",
                      evidence: location,
                    });
                  }}
                >
                  <Pencil size={14} />
                  Исправить ячейку
                </button>
              )}
          </aside>
        </div>
      </section>
      {reasonOpen && (
        <ReasonDialog
          title="Подтвердить реквизиты документа"
          description="Укажите, какие реквизиты и суммы сверены с оригиналом. Исправленные значения пересчитываются отдельно."
          busy={busy}
          onClose={() => setReasonOpen(false)}
          onSave={async (reason) => {
            if (
              await onChange({
                kind: "document",
                target: document.id,
                values: { reviewed: true },
                reason,
              })
            )
              setReasonOpen(false);
          }}
        />
      )}
    </div>
  );
}
