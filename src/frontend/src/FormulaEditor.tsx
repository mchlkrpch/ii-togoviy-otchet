import { useEffect, useMemo, useRef, useState } from "react";
import { Check, FilePlus2, Save, X } from "lucide-react";
import { request, amount } from "./api";
import { draftRule, usedNewVariables } from "./formulaModel";
import type { CaseResult, FormulaDraft } from "./types";

export default function FormulaEditor({
  result,
  draft,
  onDraft,
  onChooseFile,
  onSave,
  onCancel,
  busy,
  focusVersion,
  onVariable,
  notice,
}: {
  result: CaseResult;
  draft: FormulaDraft;
  onDraft: (draft: FormulaDraft) => void;
  onChooseFile: (id: string) => void;
  onSave: () => void;
  onCancel: () => void;
  busy: boolean;
  focusVersion: number;
  onVariable: (ref: string, name: string) => void;
  notice?: string;
}) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [filesOpen, setFilesOpen] = useState(false);
  const [fileIndex, setFileIndex] = useState(0);
  const [filter, setFilter] = useState("");
  const [preview, setPreview] = useState<{
    expected: string | null;
    delta: string | null;
    status: string;
  }>();
  const [error, setError] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const rule = useMemo(
    () => draftRule(draft),
    [draft.expression, draft.inputs],
  );
  const variables = useMemo(
    () => usedNewVariables(draft),
    [draft.expression, draft.inputs, draft.variables],
  );
  const payload = JSON.stringify({ target: draft.target, rule, variables });
  useEffect(() => {
    let alive = true;
    setPreview(undefined);
    setError("");
    setPreviewing(true);
    const timer = setTimeout(() => {
      request<{
        expected: string | null;
        delta: string | null;
        status: string;
      }>(`/api/cases/${result.id}/formula-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
      })
        .then((data) => {
          if (alive) {
            setPreview(data);
            setPreviewing(false);
          }
        })
        .catch((reason) => {
          if (alive) {
            setError(String(reason).replace(/^Error: /, ""));
            setPreviewing(false);
          }
        });
    }, 300);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [payload, result.id, result.revision]);
  useEffect(() => {
    if (!textarea.current) return;
    textarea.current.focus({ preventScroll: true });
    textarea.current.setSelectionRange(draft.cursor, draft.cursorEnd);
  }, [focusVersion]);
  const documents = [...result.documents]
    .sort(
      (a, b) =>
        Number(b.id === result.report_id) - Number(a.id === result.report_id),
    )
    .filter((doc) => doc.name.toLowerCase().includes(filter.toLowerCase()));
  function choose(id: string) {
    setFilesOpen(false);
    setFilter("");
    onChooseFile(id);
  }
  function operator(text: string) {
    const start = textarea.current?.selectionStart ?? draft.cursor,
      end = textarea.current?.selectionEnd ?? draft.cursorEnd;
    onDraft({
      ...draft,
      expression:
        draft.expression.slice(0, start) + text + draft.expression.slice(end),
      cursor: start + text.length,
      cursorEnd: start + text.length,
    });
    window.requestAnimationFrame(() => {
      textarea.current?.focus({ preventScroll: true });
      textarea.current?.setSelectionRange(
        start + text.length,
        start + text.length,
      );
    });
  }
  return (
    <section className="formula-editor-shell" aria-label="Редактор формулы">
      <header>
        <strong>
          Формула · {result.nodes[draft.target]?.code || draft.target}
        </strong>
        <span>/ — выбрать файл · ÷ — деление</span>
        <button
          className="icon-button"
          aria-label="Закрыть редактор"
          onClick={onCancel}
          disabled={busy}
        >
          <X size={16} />
        </button>
      </header>
      <div className="formula-editor-input-row">
        <textarea
          ref={textarea}
          aria-label="Математическая формула"
          value={draft.expression}
          readOnly={busy}
          spellCheck={false}
          placeholder="Введите формулу или нажмите / для выбора числа"
          onChange={(event) => {
            setFilesOpen(false);
            onDraft({
              ...draft,
              expression: event.target.value,
              cursor: event.target.selectionStart,
              cursorEnd: event.target.selectionEnd,
            });
          }}
          onSelect={(event) => {
            const target = event.currentTarget;
            if (
              target.selectionStart !== draft.cursor ||
              target.selectionEnd !== draft.cursorEnd
            )
              onDraft({
                ...draft,
                cursor: target.selectionStart,
                cursorEnd: target.selectionEnd,
              });
          }}
          onKeyDown={(event) => {
            if (event.key === "/") {
              event.preventDefault();
              onDraft({
                ...draft,
                cursor: event.currentTarget.selectionStart,
                cursorEnd: event.currentTarget.selectionEnd,
              });
              setFilesOpen(true);
              setFileIndex(0);
              return;
            }
            if (filesOpen && ["ArrowDown", "ArrowUp"].includes(event.key)) {
              event.preventDefault();
              setFileIndex((index) =>
                Math.max(
                  0,
                  Math.min(
                    documents.length - 1,
                    index + (event.key === "ArrowDown" ? 1 : -1),
                  ),
                ),
              );
            }
            if (filesOpen && event.key === "Enter") {
              event.preventDefault();
              if (documents[fileIndex]) choose(documents[fileIndex].id);
            }
            if (filesOpen && event.key === "Escape") {
              event.preventDefault();
              setFilesOpen(false);
              event.stopPropagation();
            }
          }}
        />
        <button
          className="icon-button"
          aria-label="Выбрать число из файла"
          onClick={() => setFilesOpen(!filesOpen)}
          disabled={busy}
        >
          <FilePlus2 size={18} />
        </button>
        <button
          className="division-button"
          title="Вставить деление"
          onClick={() => operator(" / ")}
          disabled={busy}
        >
          ÷
        </button>
      </div>
      {filesOpen && (
        <div
          className="formula-file-picker"
          aria-label="Выбор файла для переменной"
        >
          <input
            aria-label="Поиск файла для переменной"
            placeholder="Найти файл"
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
              setFileIndex(0);
            }}
          />
          <div role="listbox" aria-label="Файлы переменных">
            {documents.map((doc, index) => (
              <button
                role="option"
                aria-selected={index === fileIndex}
                key={doc.id}
                className={index === fileIndex ? "active" : ""}
                onClick={() => choose(doc.id)}
              >
                <span className={`file-type-label ${doc.format}`}>
                  {doc.format.toUpperCase()}
                </span>
                <span>{doc.name}</span>
                {doc.id === result.report_id && <small>Отчет</small>}
              </button>
            ))}
          </div>
          <p>
            Выберите файл, выделите число и нажмите «Использовать как
            переменную».
          </p>
        </div>
      )}
      <div className="formula-draft-variables">
        {draft.inputs.map((input) => {
          const variable = draft.variables.find(
            (item) => `${item.document_id}:${item.key}` === input.ref,
          );
          const value = variable?.value || result.nodes[input.ref]?.expected;
          const used = rule.inputs.some((item) => item.name === input.name);
          return (
            <button
              key={input.name}
              className={used ? "" : "unused"}
              title={used ? "Показать источник" : "Не используется в формуле"}
              onClick={() => onVariable(input.ref, input.name)}
            >
              <strong>{input.name}</strong>
              <span>{value ?? "—"}</span>
              {variable && <small>новая</small>}
            </button>
          );
        })}
      </div>
      {notice && (
        <p className="formula-editor-notice" role="status">
          {notice}
        </p>
      )}
      <footer>
        <input
          aria-label="Причина изменения формулы"
          placeholder="Причина изменения"
          value={draft.reason}
          onChange={(event) =>
            onDraft({ ...draft, reason: event.target.value })
          }
          disabled={busy}
        />
        <div className="formula-preview-result" role="status">
          {previewing ? (
            "Пересчет…"
          ) : error ? (
            <span className="formula-preview-error">{error}</span>
          ) : preview ? (
            <span>
              <Check size={13} />
              По формуле: <strong>{amount(preview.expected)}</strong>
            </span>
          ) : null}
        </div>
        <button className="button secondary" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        <button
          className="button primary"
          onClick={onSave}
          disabled={
            busy ||
            previewing ||
            !!error ||
            !preview ||
            draft.reason.trim().length < 3
          }
        >
          <Save size={14} />
          {busy ? "Сохранение…" : "Сохранить"}
        </button>
      </footer>
    </section>
  );
}
