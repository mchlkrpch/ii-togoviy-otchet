import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CircleAlert,
  FileCheck2,
  FolderOpen,
  History,
  Loader2,
  Pencil,
  Plus,
  X,
} from "lucide-react";
import { dateTime, request } from "./api";
import {
  DocumentDrawer,
  DocumentsView,
  EditDialog,
  Empty,
  GraphView,
  RuleDialog,
  SettingsView,
  UploadDialog,
} from "./components";
import VerificationWorkbench from "./VerificationWorkbench";
import { ExplorerSash, ProjectHistory, WorkspaceActions, projectHref } from "./ProjectNavigation";
import type {
  CaseResult,
  CaseSummary,
  ChangeRequest,
  Edit,
  Evidence,
  Job,
  Node,
} from "./types";

type View = "checks" | "documents" | "graph" | "history" | "settings";

export default function App() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [result, setResult] = useState<CaseResult | null>(null);
  const selectedCase = useRef<string | undefined>(undefined);
  const caseRevisions = useRef(new Map<string, number>());
  const openRequest = useRef(0);
  const pendingChanges = useRef(0);
  const [view, setView] = useState<View>("checks");
  const [selectedId, setSelectedId] = useState("");
  const [activeDocumentId, setActiveDocumentId] = useState("");
  const [loading, setLoading] = useState(true);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [showUpload, setShowUpload] = useState(false);
  const [evidence, setEvidence] = useState<{
    documentId: string;
    evidence?: Evidence;
  } | null>(null);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [ruleEdit, setRuleEdit] = useState<Node | null>(null);
  const [busy, setBusy] = useState(false);
  const [projectsHidden, setProjectsHidden] = useState(
    () => localStorage.getItem("projects-hidden") === "true",
  );
  const [projectsWidth, setProjectsWidth] = useState(() =>
    Math.max(
      150,
      Math.min(480, Number(localStorage.getItem("projects-width")) || 220),
    ),
  );
  useEffect(
    () => localStorage.setItem("projects-hidden", String(projectsHidden)),
    [projectsHidden],
  );
  useEffect(
    () => localStorage.setItem("projects-width", String(projectsWidth)),
    [projectsWidth],
  );
  useEffect(() => {
    if (!result?.id || !activeDocumentId) return;
    const href = projectHref(result.id, activeDocumentId);
    const { location, history } = window;
    if (href !== location.pathname + location.search + location.hash)
      history.replaceState(history.state, "", href);
  }, [result?.id, activeDocumentId]);
  function resizeProjects(width: number) {
    if (width <= 48) {
      setProjectsHidden(true);
      return;
    }
    setProjectsHidden(false);
    setProjectsWidth(Math.max(150, Math.min(480, width)));
  }
  async function renameProject(id: string, name: string) {
    const project = cases.find((item) => item.id === id);
    if (!project) return false;
    try {
      const data = await request<CaseResult>(`/api/cases/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          revision: result?.id === id ? result.revision : project.revision,
        }),
      });
      caseRevisions.current.set(id, data.revision);
      setResult((current) => (current?.id === id ? data : current));
      await refreshCases();
      return true;
    } catch (reason) {
      setError(String(reason));
      await refreshCases();
      return false;
    }
  }

  async function openCase(id: string, documentId?: string) {
    const requestId = ++openRequest.current;
    selectedCase.current = id;
    setLoading(true);
    try {
      const data = await request<CaseResult>(`/api/cases/${id}`);
      if (requestId !== openRequest.current) return;
      caseRevisions.current.set(id, data.revision);
      setResult(data);
      const requestedDocument = data.documents.find((doc) => doc.id === documentId);
      setActiveDocumentId(requestedDocument?.id ?? data.report_id);
      const reportDocumentId =
        requestedDocument?.role === "report" ? requestedDocument.id : data.report_id;
      setSelectedId(
        data.checks.find(
          (check) =>
            check.code === "0800" &&
            check.row === data.context.target_year &&
            check.reported_evidence?.document_id === reportDocumentId,
        )?.id ||
          data.checks.find((check) => check.reported_evidence?.document_id === reportDocumentId)?.id ||
          data.checks[0]?.id ||
          "",
      );
      localStorage.setItem("verification-case", id);
      setView("checks");
    } catch (reason) {
      if (requestId === openRequest.current) setError(String(reason));
    } finally {
      if (requestId === openRequest.current) setLoading(false);
    }
  }

  async function refreshCases() {
    const data = await request<CaseSummary[]>("/api/cases");
    setCases(data);
    return data;
  }

  useEffect(() => {
    let active = true;
    request<CaseSummary[]>("/api/cases")
      .then(async (data) => {
        if (!active) return;
        setCases(data);
        const params = new URLSearchParams(window.location.search);
        const linked = data.find((item) => item.id === params.get("project"));
        const saved = localStorage.getItem("verification-case");
        const selected = linked || data.find((item) => item.id === saved) || data[0];
        if (selected) await openCase(selected.id, linked ? params.get("document") ?? undefined : undefined);
        else setLoading(false);
        const pendingId = localStorage.getItem("verification-job");
        if (pendingId) {
          try {
            const pending = await request<Job>(`/api/jobs/${pendingId}`);
            if (["queued", "processing"].includes(pending.status))
              setJob(pending);
            else {
              localStorage.removeItem("verification-job");
              if (pending.status === "completed") {
                await refreshCases();
                await openCase(pending.case_id);
              } else if (pending.error) setError(pending.error);
            }
          } catch {
            localStorage.removeItem("verification-job");
          }
        }
      })
      .catch((reason) => {
        if (active) {
          setError(String(reason));
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (job) localStorage.setItem("verification-job", job.id);
  }, [job?.id]);

  useEffect(() => {
    if (!job || !["queued", "processing"].includes(job.status)) return;
    const timer = window.setInterval(() => {
      request<Job>(`/api/jobs/${job.id}`)
        .then(async (data) => {
          setJob(data);
          if (data.status === "completed") {
            localStorage.removeItem("verification-job");
            await refreshCases();
            await openCase(data.case_id);
            setJob(null);
          } else if (data.status === "failed") {
            localStorage.removeItem("verification-job");
            setError(data.error || "Не удалось обработать документы");
            setJob(null);
          }
        })
        .catch((reason) => {
          setError(String(reason));
          setJob(null);
        });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status]);

  async function startDemo() {
    setBusy(true);
    try {
      setJob(await request<Job>("/api/cases/demo", { method: "POST" }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function change(requested: ChangeRequest) {
    if (!result) return;
    const caseId = result.id;
    pendingChanges.current += 1;
    setError("");
    setBusy(true);
    try {
      const data = await request<CaseResult>(
        `/api/cases/${caseId}/changes`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...requested,
            revision: caseRevisions.current.get(caseId) ?? result.revision,
          }),
        },
      );
      caseRevisions.current.set(caseId, data.revision);
      setResult((current) =>
        current?.id === caseId && selectedCase.current === caseId ? data : current,
      );
      return true;
    } catch (reason) {
      if (selectedCase.current === caseId) setError(String(reason));
      return false;
    } finally {
      pendingChanges.current -= 1;
      setBusy(pendingChanges.current > 0);
    }
  }

  function pickNode(id: string) {
    setSelectedId(id);
  }

  return (
    <div className="application-shell">
      <ExplorerSash width={projectsWidth} collapsed={projectsHidden}
        onWidth={resizeProjects} onToggle={() => setProjectsHidden(!projectsHidden)} />
      {error && (
        <div className="error-banner" role="alert">
          <CircleAlert size={17} />
          <span>{error}</span>
          <button
            className="icon-button"
            onClick={() => setError("")}
            aria-label="Закрыть ошибку"
          >
            <X size={17} />
          </button>
        </div>
      )}
        <div
          id="verification-panel"
          aria-label="Проверка документов"
          className="verification-tab-panel"
        >
          <div
            className="verification-layout"
            style={{
              gridTemplateColumns: projectsHidden
                ? "minmax(0,1fr)"
                : `${projectsWidth}px minmax(0,1fr)`,
            }}
          >
            {!projectsHidden && (
              <ProjectHistory
                cases={cases}
                activeId={result?.id}
                activeResult={result}
                activeDocumentId={activeDocumentId}
                onDocument={(caseId, documentId) => {
                  if (result?.id === caseId) {
                    setActiveDocumentId(documentId);
                    if (
                      result.documents.some(
                        (doc) => doc.id === documentId && doc.role === "report",
                      ) &&
                      result.nodes[selectedId]?.reported_evidence
                        ?.document_id !== documentId
                    ) {
                      const node =
                        result.checks.find(
                          (node) =>
                            node.reported_evidence?.document_id ===
                              documentId &&
                            node.row === result.context.target_year &&
                            node.code === "0800",
                        ) ||
                        result.checks.find(
                          (node) =>
                            node.reported_evidence?.document_id === documentId,
                        );
                      if (node) setSelectedId(node.id);
                    }
                    setView("checks");
                  } else void openCase(caseId, documentId);
                }}
                onSelect={openCase}
                onCreate={() => setShowUpload(true)}
                onHide={() => setProjectsHidden(true)}
                width={projectsWidth}
                onWidth={resizeProjects}
                onRename={renameProject}
                resizeHandle={false}
              />
            )}
            <div className="project-workspace">
              {(view !== "checks" || !result) && (
                <WorkspaceActions
                  result={result}
                  busy={busy || !!job}
                  onDemo={startDemo}
                  onView={setView}
                  onRefresh={() => result && openCase(result.id)}
                />
              )}
              {job && (
                <div className="job-banner" role="status">
                  <Loader2 size={18} className="spin" />
                  <div>
                    <strong>Обработка документов · {job.progress}%</strong>
                    <span>{job.current_file || "Подготовка файлов"}</span>
                    <progress max="100" value={job.progress} />
                  </div>
                </div>
              )}
              {loading ? (
                <div className="loading-page">
                  <Loader2 className="spin" size={25} />
                  Открываю проверку…
                </div>
              ) : !result ? (
                <main className="welcome">
                  <span className="welcome-icon">
                    <FileCheck2 size={34} />
                  </span>
                  <h1>Проверка отчетности по документам</h1>
                  <p>
                    Загрузите отчет и первичные документы, чтобы проверить
                    числа, формулы и их источники.
                  </p>
                  <div className="welcome-actions">
                    <button
                      className="button primary large"
                      onClick={() => setShowUpload(true)}
                    >
                      <Plus size={18} />
                      Новая проверка
                    </button>
                    <button
                      className="button secondary large"
                      onClick={startDemo}
                      disabled={!!job || busy}
                    >
                      <FolderOpen size={18} />
                      Открыть набор из «вводные»
                    </button>
                  </div>
                </main>
              ) : view === "checks" ? (
                <VerificationWorkbench
                  key={result.id}
                  result={result}
                  selectedId={selectedId}
                  activeDocumentId={activeDocumentId}
                  onActiveDocument={setActiveDocumentId}
                  onSelect={pickNode}
                  onEdit={setEdit}
                  onRule={setRuleEdit}
                  onChange={change}
                  busy={busy}
                  actions={
                    <WorkspaceActions
                      result={result}
                      busy={busy || !!job}
                      onDemo={startDemo}
                      onView={setView}
                      onRefresh={() => result && openCase(result.id)}
                    />
                  }
                />
              ) : (
                <main className="verification-utility-page">
                  <div className="utility-page-heading">
                    <button
                      className="button text"
                      onClick={() => setView("checks")}
                    >
                      <ArrowLeft size={15} />К отчету и источникам
                    </button>
                    <h1>
                      {view === "documents"
                        ? "Реестр документов"
                        : view === "history"
                          ? "История изменений"
                          : view === "graph"
                            ? "Граф зависимостей"
                            : "Параметры проверки"}
                    </h1>
                  </div>
                  {view === "documents" && (
                    <DocumentsView
                      result={result}
                      onOpen={(documentId) => setEvidence({ documentId })}
                      onChange={change}
                      busy={busy}
                    />
                  )}
                  {view === "settings" && (
                    <SettingsView
                      context={result.context}
                      onChange={change}
                      busy={busy}
                      onDemo={startDemo}
                    />
                  )}
                  {view === "graph" && (
                    <GraphView
                      result={result}
                      selected={result.nodes[selectedId]}
                      onSelect={pickNode}
                    />
                  )}
                  {view === "history" && <HistoryView result={result} />}
                </main>
              )}
            </div>
          </div>
        </div>
      {showUpload && (
        <UploadDialog
          busy={!!job}
          onClose={() => setShowUpload(false)}
          onDemo={() => {
            setShowUpload(false);
            startDemo();
          }}
          onSubmit={async (form) => {
            try {
              setJob(
                await request<Job>("/api/cases", {
                  method: "POST",
                  body: form,
                }),
              );
              setShowUpload(false);
            } catch (reason) {
              setError(String(reason));
            }
          }}
        />
      )}
      {evidence && result && (
        <DocumentDrawer
          key={evidence.documentId}
          result={result}
          selection={evidence}
          onClose={() => setEvidence(null)}
          onEdit={setEdit}
          onChange={change}
          busy={busy}
        />
      )}
      {edit && (
        <EditDialog
          edit={edit}
          busy={busy}
          onClose={() => setEdit(null)}
          onSave={async (value, reason) => {
            if (
              await change({
                kind: edit.kind,
                target: edit.target,
                values: { value },
                reason,
              })
            )
              setEdit(null);
          }}
        />
      )}
      {ruleEdit && result && (
        <RuleDialog
          node={ruleEdit}
          result={result}
          busy={busy}
          onClose={() => setRuleEdit(null)}
          onSave={async (rule, reason, reset) => {
            if (
              await change({
                kind: "check",
                target: ruleEdit.id,
                values: { rule },
                reason,
                reset,
              })
            )
              setRuleEdit(null);
          }}
        />
      )}
    </div>
  );
}

function HistoryView({ result }: { result: CaseResult }) {
  return (
    <section className="content-panel">
      <div className="panel-heading">
        <h2>Журнал ручных исправлений</h2>
        <span className="muted">{result.audit.length} изменений</span>
      </div>
      {!result.audit.length ? (
        <Empty>
          <History size={28} />
          <p>Ручных изменений пока нет.</p>
        </Empty>
      ) : (
        <div className="audit-list">
          {[...result.audit].reverse().map((event) => {
            const node = result.nodes[event.target];
            const document = result.documents.find(
              (document) => document.id === node?.document_id,
            );
            return (
              <article key={event.id}>
                <span className="audit-icon">
                  <Pencil size={15} />
                </span>
                <div>
                  <h3>
                    {event.kind === "project"
                      ? "Название проекта"
                      : node?.title || node?.label || event.target}
                  </h3>
                  {document && <small>{document.name}</small>}
                  <p>{event.reason}</p>
                  <div className="audit-values">
                    <code>{JSON.stringify(event.before)}</code>
                    <ArrowRight size={14} />
                    <code>{JSON.stringify(event.after)}</code>
                  </div>
                  <small>
                    {dateTime(event.at)} · ревизия {event.revision}
                  </small>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
