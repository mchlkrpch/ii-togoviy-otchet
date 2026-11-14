import { Badge } from "./components";
import { amount } from "./api";
import type { CaseResult, Node, Status } from "./types";

export default function ConclusionView({ result, onSelect }: { result: CaseResult; onSelect: (node: Node) => void }) {
  const groups: { statuses: Status[]; label: string }[] = [
    { statuses: ["mismatch"], label: "Расхождения" },
    { statuses: ["verified"], label: "Подтвержденные показатели" },
    { statuses: ["review", "unverified", "error"], label: "Требуют проверки или дополнительных данных" },
  ];
  return <section className="content-panel conclusion-panel">
    <p>{result.summary.conclusion}</p>
    <nav className="conclusion-exports" aria-label="Экспорт заключения">
      <a className="button secondary" href={`/api/cases/${result.id}/export/html`} target="_blank" rel="noreferrer">HTML для печати</a>
      <a className="button secondary" href={`/api/cases/${result.id}/export/csv`}>CSV</a>
      <a className="button secondary" href={`/api/cases/${result.id}/export/json`}>JSON с источниками и историей</a>
    </nav>
    {groups.map(group => {
      const checks = result.checks.filter(check => group.statuses.includes(check.status));
      return <section key={group.label}><h2>{group.label} · {checks.length}</h2>
        {checks.map(check => <button key={check.id} className="conclusion-check" onClick={() => onSelect(check)}>
          <span>{check.row_title} · {check.title}</span><Badge status={check.status} />
          <span>В отчете {amount(check.reported, check.unit)} · ожидается {amount(check.expected, check.unit)} · разница {amount(check.delta, check.unit)}</span>
          <small>{check.explanation?.join(" ")}</small>
        </button>)}
      </section>;
    })}
    <h2>Проблемы документов · {result.issues.length}</h2>
    {result.issues.map(issue => <p key={issue.id}>{issue.document_name} · {issue.title}</p>)}
  </section>;
}
