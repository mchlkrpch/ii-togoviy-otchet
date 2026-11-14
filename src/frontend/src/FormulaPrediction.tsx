import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { amount, request } from "./api";
import type { CaseResult, Evidence, FormulaDraft, Input, NewVariable, Rule } from "./types";

type Proposal = {
  rule: Rule;
  reason: string;
  expected: string | null;
  delta: string | null;
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

export default function FormulaPrediction({ result, draft, onDraft, onSource, busy }: {
  result: CaseResult;
  draft: FormulaDraft;
  onDraft: (draft: FormulaDraft) => void;
  onSource: (evidence: Evidence) => void;
  busy: boolean;
}) {
  const [prediction, setPrediction] = useState<Prediction>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedDocuments, setSelectedDocuments] = useState<string[]>([]);
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    setPrediction(undefined);
    setLoading(false);
    setError("");
  }, [result.id, result.revision, draft.target]);
  useEffect(() => () => { generation.current++; }, []);
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
      if (current === generation.current) setPrediction(data);
    } catch (reason) {
      if (current === generation.current) setError(String(reason).replace(/^Error: /, ""));
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }
  function apply(proposal: Proposal) {
    onDraft({ ...draft, expression: proposal.rule.expression, inputs: proposal.rule.inputs,
      variables: proposal.variables, reason: draft.reason || "Принята предложенная ИИ формула после проверки источников",
      cursor: proposal.rule.expression.length, cursorEnd: proposal.rule.expression.length });
  }
  return <div className="formula-prediction">
    <div className="formula-prediction-actions">
      <button className="button secondary" onClick={() => void predict()} disabled={loading || busy}>
        <Sparkles size={15} />{loading ? "Анализ метаданных…" : "Предсказать ИИ"}
      </button>
      <details>
        <summary>Источники · {selectedDocuments.length || "все первичные"}</summary>
        {result.documents.filter(document => document.role !== "reference").map(document =>
          <label key={document.id}>
            <input type="checkbox" checked={selectedDocuments.includes(document.id)} onChange={event => {
              setSelectedDocuments(previous => event.target.checked ? [...previous, document.id] : previous.filter(id => id !== document.id));
              generation.current++;
              setLoading(false);
              setPrediction(undefined);
            }} />{document.name}
          </label>)}
      </details>
    </div>
    {error && <p role="alert" className="formula-preview-error">{error}</p>}
    {prediction && <div aria-label="Предложенные формулы">
      <small>{prediction.model} · CPU · локально · {prediction.fields_considered} полей · {prediction.elapsed_ms} мс</small>
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
        <p>Расчет {amount(proposal.expected)} · расхождение {amount(proposal.delta)}</p>
        {proposal.warnings.map(warning => <p key={warning}>{warning}</p>)}
        <button className="button secondary" disabled={busy || prediction.revision !== result.revision} onClick={() => apply(proposal)}>
          Использовать в черновике
        </button>
      </article>)}
    </div>}
  </div>;
}
