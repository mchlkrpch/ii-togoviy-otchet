import { useEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import { Sparkles } from "lucide-react";
import { amount, request } from "./api";
import type { CaseResult, Evidence, FormulaDraft, Input, NewVariable, Rule } from "./types";

type Proposal = {
  rule: Rule;
  reason: string;
  expected: string | null;
  delta: string | null;
  reported_preview?: string | null;
  inputs: Input[];
  variables: NewVariable[];
  warnings: string[];
};
type Prediction = {
  revision: number;
  model: string;
  fields_considered: number;
  elapsed_ms: number;
  ambiguous?: boolean;
  abstention?: string;
  suggestions: Proposal[];
};
export type PredictionSession = { key: string; prediction?: Prediction; selectedDocuments: string[] };

export default function FormulaPrediction({ result, draft, onEdit, onSave, onSource, busy, predictOnOpen, session }: {
  result: CaseResult;
  draft: FormulaDraft;
  onEdit: (draft: FormulaDraft) => void;
  onSave: (draft: FormulaDraft) => Promise<void>;
  onSource: (evidence: Evidence) => void;
  busy: boolean;
  predictOnOpen: boolean;
  session: MutableRefObject<PredictionSession>;
}) {
  const [prediction, setPrediction] = useState<Prediction>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedDocuments, setSelectedDocuments] = useState<string[]>([]);
  const generation = useRef(0);
  const sessionKey = `${result.id}:${result.revision}:${draft.target}`;
  useEffect(() => {
    generation.current++;
    const saved = session.current.key === sessionKey ? session.current : { key: sessionKey, selectedDocuments: [] };
    session.current = saved;
    setPrediction(saved.prediction);
    setSelectedDocuments(saved.selectedDocuments);
    setLoading(false);
    setError("");
  }, [sessionKey]);
  useEffect(() => () => { generation.current++; }, []);
  useEffect(() => {
    if (predictOnOpen && !session.current.prediction) {
      const timer = window.setTimeout(() => void predict(), 0);
      return () => window.clearTimeout(timer);
    }
  }, [predictOnOpen]);
  async function predict() {
    const current = ++generation.current;
    setLoading(true);
    setPrediction(undefined);
    setError("");
    try {
      const data = await request<Prediction>(`/api/cases/${result.id}/formula-prediction`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: draft.target, revision: result.revision,
          document_ids: selectedDocuments.length ? selectedDocuments : null }),
      });
      if (current === generation.current) {
        session.current = { key: sessionKey, prediction: data, selectedDocuments };
        setPrediction(data);
      }
    } catch (reason) {
      if (current === generation.current) setError(String(reason).replace(/^Error: /, ""));
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }
  function proposalDraft(proposal: Proposal): FormulaDraft {
    return { ...draft, expression: proposal.rule.expression, inputs: proposal.rule.inputs,
      variables: proposal.variables, reason: draft.reason.trim().length >= 3 ? draft.reason : "Принято предложение ИИ",
      cursor: proposal.rule.expression.length, cursorEnd: proposal.rule.expression.length };
  }
  return <div className="formula-prediction">
    <div className="formula-prediction-actions">
      <button className="button secondary" onClick={() => void predict()} disabled={loading || busy}>
        <Sparkles size={15} />{loading ? "Анализ метаданных…" : "Предсказать ИИ"}
      </button>
      <details>
        <summary>Источники · {selectedDocuments.length || "автовыбор"}</summary>
        {result.documents.filter(document => document.role !== "reference").map(document =>
          <label key={document.id}>
            <input type="checkbox" checked={selectedDocuments.includes(document.id)} onChange={event => {
              const next = event.target.checked ? [...selectedDocuments, document.id] : selectedDocuments.filter(id => id !== document.id);
              session.current = { key: sessionKey, selectedDocuments: next };
              setSelectedDocuments(next);
              generation.current++;
              setLoading(false);
              setPrediction(undefined);
            }} />{document.name}
          </label>)}
      </details>
    </div>
    {error && <p role="alert" className="formula-preview-error">{error}</p>}
    {prediction && <div aria-label="Предложенные формулы">
      {prediction.abstention && <p role="status">{prediction.abstention}</p>}
      {prediction.suggestions.map((proposal, index) => <article key={index}>
        <code>{proposal.rule.expression}</code>
        <p>{proposal.reason}</p>
        <div className="prediction-sources">{proposal.inputs.map(input => {
          const document = result.documents.find(document => document.id === input.evidence?.document_id);
          return <button key={input.name} disabled={!input.evidence} onClick={() => input.evidence && onSource(input.evidence)}>
            <b>{input.name} = {amount(input.value)}</b>
            <span>{document?.name} · {input.evidence?.locator}</span>
            <small>{input.label}</small>
          </button>;
        })}</div>
        {proposal.expected != null ? (
          <p>Расчет {amount(proposal.expected)} · расхождение {amount(proposal.delta)}</p>
        ) : (
          <p>Ожидаемое значение пока недоступно{proposal.reported_preview != null &&
            ` · по значениям в отчете ${amount(proposal.reported_preview)}`}</p>
        )}
        {proposal.warnings.map(warning => <p key={warning}>{warning}</p>)}
        <button className="button secondary" disabled={busy || prediction.revision !== result.revision}
          onClick={() => onEdit(proposalDraft(proposal))}>
          Редактировать предложение
        </button>
        <button className="button primary" disabled={busy || prediction.revision !== result.revision}
          onClick={() => void onSave(proposalDraft(proposal))}>
          Сохранить предложение
        </button>
      </article>)}
    </div>}
  </div>;
}
