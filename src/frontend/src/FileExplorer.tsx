import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileSpreadsheet,
  FileText,
  Image,
  Files,
} from "lucide-react";
import type { CaseResult, EvidenceAnnotation } from "./types";

export default function FileExplorer({
  result,
  activeId,
  onSelect,
  bindings,
}: {
  result: CaseResult;
  activeId: string;
  onSelect: (id: string) => void;
  bindings: Map<string, EvidenceAnnotation[]>;
}) {
  const [expanded, setExpanded] = useState(true);
  const report = result.documents.find((doc) => doc.id === result.report_id)!;
  const documents = result.documents.filter(
    (doc) => doc.id !== result.report_id && doc.role === "source",
  );
  const icon = (format: string) =>
    format === "xlsx" ? (
      <FileSpreadsheet size={15} />
    ) : ["png", "jpg", "jpeg", "tif", "tiff"].includes(format) ? (
      <Image size={15} />
    ) : (
      <FileText size={15} />
    );
  return (
    <aside className="verification-file-panel" aria-label="Файлы проверки">
      <header>
        <Files size={15} />
        <h2>Файлы</h2>
        <span>{result.documents.length}</span>
      </header>
      <div
        className="file-explorer-tree"
        role="tree"
        aria-label="Отчет и первичные файлы"
      >
        <div
          role="treeitem"
          aria-expanded={expanded}
          className={`explorer-report-row ${activeId === report.id ? "active" : ""}`}
        >
          <button
            className="explorer-expander"
            aria-label={
              expanded ? "Свернуть первичные файлы" : "Раскрыть первичные файлы"
            }
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
          <button
            className="explorer-file"
            title={report.name}
            aria-current={activeId === report.id ? "page" : undefined}
            onClick={() => onSelect(report.id)}
          >
            {icon(report.format)}
            <span>{report.name}</span>
          </button>
        </div>
        {expanded && (
          <div role="group" className="explorer-primary-files">
            {documents.map((doc) => (
              <div
                role="treeitem"
                key={doc.id}
                className={`explorer-file-row ${activeId === doc.id ? "active" : ""} ${doc.included ? "" : "excluded-file"}`}
              >
                <button
                  className={`explorer-file format-${doc.format}`}
                  title={`${doc.name}${!doc.included ? " · исключен из расчета" : ""}`}
                  aria-current={activeId === doc.id ? "page" : undefined}
                  onClick={() => onSelect(doc.id)}
                >
                  {icon(doc.format)}
                  <span>{doc.name}</span>
                  {!!bindings.get(doc.id)?.length && (
                    <small title="Сохраненные переменные">
                      {bindings.get(doc.id)!.length}
                    </small>
                  )}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
