import { useEffect, useMemo, useRef, useState } from "react";
import { Modal } from "./components";
import CalculationInspector from "./CalculationInspector";
import DocumentViewer from "./DocumentViewer";
import FormulaOverlay from "./FormulaOverlay";
import FormulaEditor from "./FormulaEditor";

import {
  buildReportGrid,
  evidenceKey,
  reportChecks,
  selectionRange,
} from "./workbenchModel";
import {
  bindSelection,
  draftRule,
  fileBindings,
  insertVariable,
  nextVariableName,
  usedNewVariables,
  variableSources,
} from "./formulaModel";
import type {
  CaseResult,
  ChangeRequest,
  Edit,
  Evidence,
  EvidenceAnnotation,
  FormulaDraft,
  Input,
  Node,
  NumberSelection,
} from "./types";

type Props = {
  result: CaseResult;
  selectedId: string;
  activeDocumentId: string;
  onActiveDocument: (id: string) => void;
  onSelect: (id: string) => void;
  onEdit: (edit: Edit) => void;
  onRule?: (node: Node) => void;
  onChange: (change: ChangeRequest) => Promise<boolean | undefined>;
  busy: boolean;
  actions?: React.ReactNode;
};
export default function VerificationWorkbench({
  result,
  selectedId,
  activeDocumentId: activeId,
  onActiveDocument: setActiveId,
  onSelect,
  onEdit,
  onChange,
  busy,
  actions,
}: Props) {
  const checks = useMemo(() => reportChecks(result), [result]);
  const grid = useMemo(() => buildReportGrid(checks), [checks]);
  const [selectedCells, setSelectedCells] = useState<string[]>(
    checks.some((check) => check.id === selectedId) ? [selectedId] : [],
  );
  const [focusedEvidence, setFocusedEvidence] = useState<Evidence>();
  const [draft, setDraft] = useState<FormulaDraft>();
  const [focusVersion, setFocusVersion] = useState(0);
  const [message, setMessage] = useState("");
  const [calculationOpen, setCalculationOpen] = useState(false);
  const anchor = useRef(selectedCells[0]);
  const selected = result.nodes[selectedId];
  const bindings = useMemo(() => fileBindings(result), [result]);
  const current =
    result.documents.find((doc) => doc.id === activeId) ||
    result.documents.find((doc) => doc.id === result.report_id)!;
  const isReport = current.role === "report";

  useEffect(() => {
    if (draft) return;
    const node = result.nodes[selectedId];
    if (node?.reported_parts) setSelectedCells(node.reported_parts);
    else if (checks.some((check) => check.id === selectedId))
      setSelectedCells((current) =>
        current.includes(selectedId) ? current : [selectedId],
      );
  }, [selectedId, checks, result.nodes, draft]);
  useEffect(() => {
    if (draft) return;
    setFocusedEvidence(undefined);
    setMessage("");
  }, [selectedId]);
  const annotations: EvidenceAnnotation[] = (
    bindings.get(current.id) || []
  ).map((item) => ({ ...item }));
  if (draft)
    for (const input of draft.inputs) {
      const variable = draft.variables.find(
        (variable) => `${variable.document_id}:${variable.key}` === input.ref,
      );
      const evidence =
        variable?.evidence ||
        result.nodes[input.ref]?.evidence ||
        result.nodes[input.ref]?.reported_evidence;
      if (evidence?.document_id === current.id) {
        const existing = annotations.find(
          (item) => evidenceKey(item.evidence) === evidenceKey(evidence),
        );
        if (existing) existing.label = input.name;
        else
          annotations.push({
            evidence,
            label: input.name + (variable ? " · черновик" : ""),
          });
      }
    }
  const sourceHighlights = annotations.map((item) => item.evidence);
  const highlights = isReport
    ? selectedCells
        .map((id) => result.nodes[id]?.reported_evidence)
        .filter((e): e is Evidence => !!e && e.document_id === current.id)
    : sourceHighlights;
  function chooseFile(id: string) {
    setActiveId(id);
    setMessage("");
  }
  function selectCell(
    node: Node,
    event: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean },
  ) {
    if (draft) return;
    let next: string[];
    if (event.shiftKey && anchor.current)
      next = selectionRange(grid, anchor.current, node.id);
    else if (event.ctrlKey || event.metaKey) {
      next = selectedCells.includes(node.id)
        ? selectedCells.filter((id) => id !== node.id)
        : [...selectedCells, node.id];
      anchor.current = node.id;
    } else {
      next = [node.id];
      anchor.current = node.id;
    }
    setSelectedCells(next);
    setFocusedEvidence(undefined);
    onSelect(next.includes(node.id) ? node.id : next.at(-1) || "");
  }
  function showVariable(ref: string, name: string) {
    const variable = draft?.variables.find(
      (variable) => `${variable.document_id}:${variable.key}` === ref,
    );
    if (variable) {
      setActiveId(variable.document_id);
      setFocusedEvidence(variable.evidence);
      return;
    }
    const node = result.nodes[ref];
    const direct = node?.evidence || node?.reported_evidence;
    const sources = variableSources(result, ref);
    const evidence = direct?.document_id ? direct : sources[0]?.evidence[0];
    if (!evidence?.document_id) {
      setMessage(`Для ${name} нет привязанного документа`);
      return;
    }
    setActiveId(evidence.document_id);
    setFocusedEvidence(evidence);
    setMessage("");
  }
  function variable(input: Input) {
    showVariable(input.ref, input.name);
  }
  function startEditor(node: Node) {
    if (node.kind !== "check") return;
    const expression = node.rule?.expression || "";
    setDraft({
      target: node.id,
      expression,
      inputs: (node.rule?.inputs || node.inputs || []).map((input) => ({
        name: input.name,
        ref: input.ref,
      })),
      variables: [],
      reason: "",
      cursor: expression.length,
      cursorEnd: expression.length,
    });
    setCalculationOpen(false);
    setMessage("");
    setFocusVersion((version) => version + 1);
  }
  function useNumber(selection: NumberSelection) {
    if (!draft) return;
    const doc = result.documents.find(
      (doc) => doc.id === selection.document_id,
    );
    if (!doc || (doc.role !== "report" && !doc.included)) {
      setMessage(
        "Документ исключен из расчета. Сначала включите его в реестре.",
      );
      return;
    }
    let ref = bindSelection(result, selection);
    let next = draft;
    if (ref === draft.target) {
      setMessage("Ячейка не может ссылаться сама на себя.");
      return;
    }
    if (!ref) {
      if (doc.role === "report") {
        setMessage("Выберите проверяемую числовую ячейку отчета.");
        return;
      }
      const key = "manual_" + crypto.randomUUID().replace(/-/g, "");
      ref = `${doc.id}:${key}`;
      next = {
        ...next,
        variables: [
          ...next.variables,
          {
            key,
            document_id: doc.id,
            value: selection.value,
            evidence: {
              ...selection.evidence,
              document_id: doc.id,
              method: "manual-selection",
              confidence: 100,
            },
          },
        ],
      };
    }
    const name = nextVariableName(next.inputs);
    next = insertVariable(next, name, ref);
    setDraft(next);
    setMessage("");
    setFocusedEvidence(selection.evidence);
    setFocusVersion((version) => version + 1);
  }
  async function save() {
    if (!draft) return;
    const saved = await onChange({
      kind: "check",
      target: draft.target,
      values: { rule: draftRule(draft), variables: usedNewVariables(draft) },
      reason: draft.reason.trim(),
    });
    if (saved) {
      onSelect(draft.target);
      setDraft(undefined);
      setMessage("");
    }
  }
  function evidence(location?: Evidence) {
    if (location?.document_id) {
      setActiveId(location.document_id);
      setFocusedEvidence(location);
    }
  }
  return (
    <main className="file-workbench">
      <section
        className="single-document-stage"
        aria-label="Оригинал выбранного файла"
      >
        <DocumentViewer
          key={current.id}
          caseId={result.id}
          revision={result.revision}
          onWorkbookChange={onChange}
          document={current}
          title={isReport ? "Итоговый отчет" : "Первичный документ"}
          highlights={highlights}
          activeEvidence={
            focusedEvidence?.document_id === current.id
              ? focusedEvidence
              : isReport &&
                  selected?.reported_evidence?.document_id === current.id
                ? selected?.reported_evidence
                : undefined
          }
          reportNodes={
            isReport
              ? checks.filter(
                  (node) => node.reported_evidence?.document_id === current.id,
                )
              : []
          }
          selectedIds={selectedCells}
          onSelectReport={selectCell}
          onEditField={onEdit}
          busy={busy}
          annotations={annotations}
          onSelectEvidence={setFocusedEvidence}
          onUseNumber={draft ? useNumber : undefined}
          actions={actions}
          overlay={
            draft ? (
              <FormulaEditor
                result={result}
                draft={draft}
                onDraft={setDraft}
                onChooseFile={chooseFile}
                onSave={() => void save()}
                onCancel={() => {
                  setDraft(undefined);
                  setMessage("");
                }}
                busy={busy}
                focusVersion={focusVersion}
                onVariable={showVariable}
                notice={message}
              />
            ) : (
              selected && (
                <FormulaOverlay
                  node={selected}
                  onVariable={variable}
                  onDetails={() => setCalculationOpen(true)}
                  onResult={() =>
                    setActiveId(
                      result.documents.some(
                        (doc) =>
                          doc.id === selected.reported_evidence?.document_id &&
                          doc.role === "report",
                      )
                        ? selected.reported_evidence!.document_id!
                        : result.report_id,
                    )
                  }
                  onEdit={
                    selected.kind === "check"
                      ? () => startEditor(selected)
                      : undefined
                  }
                  message={message}
                />
              )
            )
          }
        />
      </section>
      {calculationOpen && selected && (
        <Modal
          title="Расчет выбранного значения"
          onClose={() => setCalculationOpen(false)}
        >
          <CalculationInspector
            result={result}
            selected={selected}
            onClose={() => setCalculationOpen(false)}
            onRule={startEditor}
            onEdit={(edit) => {
              setCalculationOpen(false);
              onEdit(edit);
            }}
            onEvidence={(location) => {
              setCalculationOpen(false);
              evidence(location);
            }}
            onSelect={(id) => {
              setCalculationOpen(false);
              setActiveId(result.report_id);
              onSelect(id);
            }}
          />
        </Modal>
      )}
    </main>
  );
}
