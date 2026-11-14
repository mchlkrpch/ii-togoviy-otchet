import { useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  GitBranch,
  History,
  Image,
  ListChecks,
  PanelLeftClose,
  Plus,
  RefreshCw,
  Settings2,
} from "lucide-react";
import type { CaseResult, CaseSummary } from "./types";

function ExplorerDocumentIcon({ format }: { format: string }) {
  const Icon = format === "xlsx"
    ? FileSpreadsheet
    : ["png", "jpg", "jpeg", "tif", "tiff"].includes(format)
      ? Image
      : FileText;
  return <Icon className="explorer-file-icon" size={15} aria-hidden="true" />;
}

export function projectHref(projectId: string, documentId?: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("project", projectId);
  if (documentId) url.searchParams.set("document", documentId);
  else url.searchParams.delete("document");
  return url.pathname + url.search + url.hash;
}

export function ExplorerSash({ width, collapsed, onWidth, onToggle }: {
  width: number;
  collapsed: boolean;
  onWidth: (width: number) => void;
  onToggle: () => void;
}) {
  const drag = useRef<{ x: number; width: number; moved: boolean; collapsed: boolean } | null>(null);
  return <div className={`explorer-sash ${collapsed ? "collapsed" : ""}`}
    style={{ left: collapsed ? 0 : width - 3 }}
    role="separator" tabIndex={0} aria-label="Ширина Explorer"
    aria-orientation="vertical" aria-valuemin={0} aria-valuemax={480}
    aria-valuenow={collapsed ? 0 : width}
    aria-valuetext={collapsed ? "Explorer свернут. Потяните вправо или нажмите Enter" : `${width} пикселей`}
    title={collapsed ? "Потяните вправо, чтобы раскрыть Explorer" : "Потяните к левому краю, чтобы свернуть Explorer"}
    onKeyDown={event => {
      if (event.key === "Enter") { event.preventDefault(); onToggle(); }
      else if (event.key === "Home") { event.preventDefault(); onWidth(0); }
      else if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
        event.preventDefault();
        onWidth(collapsed ? (event.key === "ArrowRight" ? width : 0) : width + (event.key === "ArrowRight" ? 16 : -16));
      }
    }}
    onDoubleClick={onToggle}
    onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault();
      drag.current = { x: event.clientX, width: collapsed ? 0 : width, moved: false, collapsed };
      event.currentTarget.setPointerCapture?.(event.pointerId);
    }}
    onPointerMove={event => {
      const start = drag.current;
      if (!start) return;
      if (Math.abs(event.clientX - start.x) > 3) start.moved = true;
      if (start.moved) onWidth(start.width + event.clientX - start.x);
    }}
    onPointerUp={() => {
      const start = drag.current;
      drag.current = null;
      if (start?.collapsed && !start.moved) onToggle();
    }}
    onPointerCancel={() => { drag.current = null; }}
  />;
}

export function ProjectHistory({
  cases,
  activeId,
  activeDocumentId,
  activeResult,
  onDocument,
  onSelect,
  onCreate,
  onHide,
  width,
  onWidth,
  onRename,
  resizeHandle = true,
}: {
  cases: CaseSummary[];
  activeId?: string;
  activeDocumentId?: string;
  activeResult?: CaseResult | null;
  onDocument?: (caseId: string, documentId: string) => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onHide: () => void;
  width: number;
  onWidth: (width: number) => void;
  onRename: (id: string, name: string) => Promise<boolean>;
  resizeHandle?: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string>();
  const [draft, setDraft] = useState("");
  const finishing = useRef(false),
    resizing = useRef<{ x: number; width: number } | null>(null);
  useEffect(() => {
    if (
      activeResult?.documents.some(
        (doc) => doc.id === activeDocumentId && doc.role === "source",
      )
    )
      setExpanded((current) => new Set([...current, activeResult.id]));
  }, [activeDocumentId, activeResult?.id]);
  async function finish(id: string) {
    if (finishing.current) return;
    finishing.current = true;
    setEditing(undefined);
    if (draft.trim()) await onRename(id, draft.trim());
  }
  function beginRename(project: CaseSummary) {
    finishing.current = false;
    setDraft(project.name);
    setEditing(project.id);
  }
  function choose(event: MouseEvent<HTMLAnchorElement>, project: CaseSummary, id?: string) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey ||
      event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (id && onDocument) onDocument(project.id, id);
    else if (project.id !== activeId) onSelect(project.id);
  }
  return (
    <aside
      className="project-history unified-explorer"
      aria-label="Explorer"
    >
      <header>
        <h2>Explorer</h2>
        <button
          className="icon-button"
          title="Новая проверка"
          onClick={onCreate}
        >
          <Plus size={17} />
        </button>
        <button
          className="icon-button"
          aria-label="Скрыть проекты"
          title="Скрыть панель"
          onClick={onHide}
        >
          <PanelLeftClose size={16} />
        </button>
      </header>
      <nav className="projects-list" aria-label="Проекты отчетов">
        {cases.map((project) => {
          const detail =
            activeResult?.id === project.id ? activeResult : undefined;
          const reports =
            detail?.documents.filter((doc) => doc.role === "report") ||
            project.reports ||
            [];
          const sources =
            detail?.documents.filter((doc) => doc.role === "source") ||
            project.sources ||
            [];
          const open = expanded.has(project.id);
          const active = project.id === activeId;
          return (
            <section
              key={project.id}
              className={`unified-project${active ? " current-project" : ""}`}
              aria-label={project.name}
            >
              <header className="explorer-project-heading">
                <button
                  className="explorer-expander"
                  aria-label={
                    open
                      ? "Свернуть первичные файлы"
                      : "Раскрыть первичные файлы"
                  }
                  aria-expanded={open}
                  onClick={() =>
                    setExpanded((current) => {
                      const next = new Set(current);
                      if (open) next.delete(project.id);
                      else next.add(project.id);
                      return next;
                    })
                  }
                >
                  {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </button>
                {editing === project.id ? (
                  <input
                    className="project-rename-input"
                    aria-label="Название проекта"
                    autoFocus
                    maxLength={200}
                    value={draft}
                    onFocus={(event) => event.currentTarget.select()}
                    onChange={(event) => setDraft(event.target.value)}
                    onBlur={() => void finish(project.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void finish(project.id);
                      }
                      if (event.key === "Escape") {
                        finishing.current = true;
                        setEditing(undefined);
                      }
                    }}
                  />
                ) : (
                  <a
                    className="explorer-project-name project-entry-button"
                    href={projectHref(project.id, reports[0]?.id)}
                    title={`${project.name} · двойной клик или F2 для переименования`}
                    onClick={(event) => choose(event, project, reports[0]?.id)}
                    onDoubleClick={() => beginRename(project)}
                    onKeyDown={(event) => {
                      if (event.key !== "F2") return;
                      event.preventDefault();
                      beginRename(project);
                    }}
                  >
                    {project.name}
                  </a>
                )}
              </header>
              {reports.map((report) => (
                <a
                  key={report.id}
                  className={`explorer-document-link unified-report-row${active && (!activeDocumentId || report.id === activeDocumentId) ? " active" : ""}`}
                  href={projectHref(project.id, report.id)}
                  title={report.name}
                  data-format={report.format}
                  data-document-id={report.id}
                  aria-current={active && report.id === activeDocumentId ? "page" : undefined}
                  onClick={(event) => choose(event, project, report.id)}
                  onDoubleClick={() => beginRename(project)}
                >
                  <ExplorerDocumentIcon format={report.format} />
                  <span>{report.name}</span>
                </a>
              ))}
              {open && (
                <div className="unified-source-files">
                  {sources.map((doc) => {
                    const selected = active && doc.id === activeDocumentId;
                    return (
                      <a
                        className={`explorer-document-link unified-source-row${selected ? " active" : ""}`}
                        key={doc.id}
                        href={projectHref(project.id, doc.id)}
                        data-format={doc.format}
                        data-document-id={doc.id}
                        title={doc.name}
                        aria-current={selected ? "page" : undefined}
                        onClick={(event) => choose(event, project, doc.id)}
                      >
                        <ExplorerDocumentIcon format={doc.format} />
                        <span>{doc.name}</span>
                      </a>
                    );
                  })}
                  {!sources.length && (
                    <p className="navigation-empty">Нет первичных файлов</p>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </nav>
      {resizeHandle && <div
        className="project-resizer"
        role="separator"
        aria-label="Ширина панели проектов"
        aria-orientation="vertical"
        aria-valuemin={150}
        aria-valuemax={480}
        aria-valuenow={width}
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            onWidth(width + (event.key === "ArrowRight" ? 16 : -16));
          }
        }}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.preventDefault();
          resizing.current = { x: event.clientX, width };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (resizing.current)
            onWidth(
              resizing.current.width + event.clientX - resizing.current.x,
            );
        }}
        onPointerUp={() => {
          resizing.current = null;
        }}
        onPointerCancel={() => {
          resizing.current = null;
        }}
      />}
    </aside>
  );
}

export function WorkspaceActions({
  result,
  busy,
  onDemo,
  onView,
  onRefresh,
  onSets,
}: {
  result: CaseResult | null;
  busy: boolean;
  onDemo: () => void;
  onView: (
    view: "checks" | "documents" | "graph" | "history" | "settings",
  ) => void;
  onRefresh: () => void;
  onSets?: () => void;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const heading = useRef<HTMLElement>(null);
  return (
    <div className="workspace-actions viewer-pill">
      <button
        className="icon-button"
        onClick={onRefresh}
        disabled={!result || busy}
        aria-label="Обновить проверку"
        title="Обновить проверку"
      >
        <RefreshCw size={14} />
      </button>
      <details
        className="workspace-menu"
        ref={menu}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
        }}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || !event.currentTarget.open) return;
          event.preventDefault();
          event.currentTarget.open = false;
          heading.current?.focus();
        }}
      >
        <summary aria-label="Действия с проектом" ref={heading}>
          <Settings2 size={15} aria-hidden="true" />
          <ChevronDown size={12} aria-hidden="true" />
        </summary>
        <div onClick={(event) => {
          if (!(event.target as HTMLElement).closest("button:not(:disabled)") || !menu.current) return;
          menu.current.open = false;
          heading.current?.focus();
        }}>
          <button onClick={() => onView("documents")}>
            <ListChecks size={14} />
            Реестр документов
          </button>
          <button onClick={() => onView("settings")}>
            <Settings2 size={14} />
            Параметры проверки
          </button>
          <button onClick={() => onView("history")}>
            <History size={14} />
            История изменений
          </button>
          <button onClick={() => onView("graph")}>
            <GitBranch size={14} />
            Граф зависимостей
          </button>
          <button onClick={onDemo} disabled={busy}>
            <FolderOpen size={14} />
            Набор из «вводные»
          </button>
          {onSets && <button onClick={onSets} disabled={busy}><FolderOpen size={14} />Наборы документов МФТИ</button>}
        </div>
      </details>
    </div>
  );
}
