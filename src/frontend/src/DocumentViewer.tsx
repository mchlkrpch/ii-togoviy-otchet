import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Hand,
  TextCursorInput,
  Lock,
  LockOpen,
  ListTree,
} from "lucide-react";
import {
  StructureTree,
  StructureEditor,
  StructureRegions,
  structureNodeAt,
  useDocumentStructure,
} from "./DocumentStructure";
import type { Box, StructureNode } from "./DocumentStructure";
import { SliceControls, SliceOverlay } from "./StructureSlice";
import type { SliceCuts } from "./StructureSlice";
import { loadCamera } from "./viewCamera";
import { useDocumentCamera } from "./useDocumentCamera";
import { request } from "./api";
import { canonicalNumber } from "./numericText";
import { calculationExpression } from "./calculationDisplay";
import { cellPosition, evidenceKey, isSameEvidence } from "./workbenchModel";
import { SpreadsheetOriginal, WordOriginal } from "./OfficeOriginal";
import type { OriginalSheet } from "./OfficeOriginal";
import type {
  Document,
  Edit,
  PDFField,
  Evidence,
  EvidenceAnnotation,
  NumericRegion,
  NumericTarget,
  NumberSelection,
  Node,
  ChangeRequest,
} from "./types";

export type DocumentViewerProps = {
  revision?: number;
  onWorkbookChange?: (change: ChangeRequest) => Promise<boolean | undefined>;
  onEditField?: (edit: Edit) => void;
  busy?: boolean;
  onUseNumber?: (selection: NumberSelection) => void;
  title?: string;
  actions?: React.ReactNode;
  overlay?: React.ReactNode;
  annotations?: EvidenceAnnotation[];
  caseId: string;
  document: Document;
  highlights?: Evidence[];
  activeEvidence?: Evidence;
  onSelectEvidence?: (evidence: Evidence) => void;
  reportNodes?: Node[];
  selectedIds?: string[];
  onSelectReport?: (
    node: Node,
    event: { shiftKey: boolean; metaKey: boolean; ctrlKey: boolean },
  ) => void;
};
const clampZoom = (value: number) =>
  Math.max(25, Math.min(500, Math.round(value)));
const SCROLL_SENSITIVITY = 2;
const SpreadsheetEditor = lazy(() => import("./SpreadsheetEditor"));
const RichDocumentEditor = lazy(() => import("./RichDocumentEditor"));

export default function DocumentViewer(props: DocumentViewerProps) {
  if (props.document.format === "xlsx") {
    return (
      <Suspense fallback={<div className="viewer-loading">Открываю табличный редактор…</div>}>
        <SpreadsheetEditor {...props} />
      </Suspense>
    );
  }
  if (props.document.format === "docx") {
    return (
      <Suspense fallback={<div className="viewer-loading">Открываю редактор документа…</div>}>
        <RichDocumentEditor {...props} />
      </Suspense>
    );
  }
  return <OriginalDocumentViewer {...props} />;
}

function OriginalDocumentViewer({
  caseId,
  onEditField,
  busy = false,
  onUseNumber,
  title,
  actions,
  overlay,
  annotations = [],
  document: source,
  highlights = [],
  activeEvidence,
  onSelectEvidence,
  reportNodes = [],
  selectedIds = [],
  onSelectReport,
}: DocumentViewerProps) {
  const cameraKey = `${caseId}:${source.id}`;
  const initialCamera = useRef(loadCamera(cameraKey)).current;
  const [locked, setLocked] = useState(true);
  const [page, setPage] = useState(
    initialCamera?.page || activeEvidence?.page || 1,
  );
  const [wordPages, setWordPages] = useState(1);
  const pageCount = source.format === "docx" ? wordPages : source.page_count;
  const [sheet, setSheet] = useState(
    initialCamera?.sheet ||
      activeEvidence?.sheet ||
      Object.values(source.cells)[0]?.sheet ||
      "",
  );
  const [zoom, setZoom] = useState(initialCamera?.zoom || 100);
  const [pan, setPan] = useState(initialCamera?.pan || false);
  const [ratio, setRatio] = useState(1.414);
  const [row, setRow] = useState(initialCamera?.row || 1);
  const [column, setColumn] = useState(initialCamera?.column || 1);
  const [layout, setLayout] = useState<OriginalSheet>();
  const [imageError, setImageError] = useState(false);
  const [numericRegions, setNumericRegions] = useState<NumericRegion[]>([]);
  const [fields, setFields] = useState<PDFField[]>(source.pdf_fields || []);
  const viewport = useRef<HTMLDivElement>(null);
  const [numberSelection, setNumberSelection] = useState<
    NumberSelection & { x: number; y: number }
  >();
  useEffect(() => {
    if (!onUseNumber) setNumberSelection(undefined);
    else setLocked(true);
  }, [onUseNumber]);
  const container = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(zoom);
  const pendingZoom = useRef<{ left: number; top: number } | null>(null);
  const drag = useRef<{
    x: number;
    y: number;
    left: number;
    top: number;
  } | null>(null);
  const down = useRef<{ x: number; y: number } | null>(null);
  const url = `/api/cases/${caseId}/documents/${source.id}`;
  const imageFormat = ["pdf", "png", "jpg", "jpeg", "tif", "tiff"].includes(
    source.format,
  );
  const [structureOpen, setStructureOpen] = useState(false);
  const [structureSelection, setStructureSelection] = useState<{ page: number; id: string }>();
  const [fieldFocus, setFieldFocus] = useState<{ box: Box; size: [number, number] }>();
  const [sliceDraft, setSliceDraft] = useState<(SliceCuts & { page: number; id: string; box: Box; pickingParent: boolean })>();
  const [sliceSaving, setSliceSaving] = useState(false);
  const [sliceError, setSliceError] = useState("");
  const showStructure = imageFormat && structureOpen && !onUseNumber;
  const structure = useDocumentStructure(url, showStructure, page);
  const structurePage = showStructure ? structure.page : undefined;
  const selectedField = structureSelection?.page === page
    ? structurePage?.nodes.find((node) => node.id === structureSelection.id)
    : undefined;
  const sliceBox = selectedField?.field_bbox;
  const slice = !locked && sliceDraft?.page === page && sliceDraft?.id === selectedField?.id &&
    sliceDraft.box.every((coordinate, index) => coordinate === sliceBox?.[index])
    ? sliceDraft : undefined;
  const pieces = slice ? (slice.xCuts.length + 1) * (slice.yCuts.length + 1) : 1;
  function cancelSlice() {
    setSliceDraft(undefined);
    setSliceError("");
  }
  function selectField(node: StructureNode, reveal = false) {
    if (slice) return;
    setStructureSelection({ page, id: node.id });
    if (reveal && structurePage)
      setFieldFocus({ box: node.field_bbox ?? node.bbox, size: structurePage.size });
  }
  function startSlice() {
    if (!selectedField || !sliceBox || locked) return;
    setPan(false);
    setSliceError("");
    setSliceDraft({ page, id: selectedField.id, box: sliceBox, xCuts: [], yCuts: [], pickingParent: false });
  }
  function pickParent() {
    if (pieces > 1) setSliceDraft((draft) => draft && { ...draft, pickingParent: true });
  }
  async function commitSlice(index: number) {
    if (!slice || !selectedField || sliceSaving) return;
    setSliceError("");
    setSliceSaving(true);
    try {
      await structure.slice(selectedField, slice.xCuts, slice.yCuts, index);
      cancelSlice();
    } catch (error) {
      setSliceError(error instanceof Error ? error.message : "Не удалось сохранить разбиение");
    } finally {
      setSliceSaving(false);
    }
  }
  useEffect(() => {
    if (!slice) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !sliceSaving) {
        event.preventDefault();
        cancelSlice();
      }
      if (event.key === "Enter" && !slice.pickingParent && pieces > 1 &&
          !(event.target instanceof Element && event.target.closest("button,input,textarea,select,a"))) {
        event.preventDefault();
        pickParent();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [slice, pieces, sliceSaving]);

  const camera = useDocumentCamera(
    cameraKey,
    viewport,
    { zoom, page, sheet, row, column, pan },
    imageFormat,
  );
  useLayoutEffect(() => {
    if (!fieldFocus) return;
    const element = viewport.current;
    const paper = element?.querySelector(".file-page-canvas")?.getBoundingClientRect();
    const { box, size: [width, height] } = fieldFocus;
    if (!element || !paper?.height || !width || !height) return;
    const bounds = element.getBoundingClientRect();
    const x = paper.left + (box[0] + box[2]) * paper.width / (2 * width);
    const y = paper.top + (box[1] + box[3]) * paper.height / (2 * height);
    element.scrollLeft += x - bounds.left - element.clientWidth / 2;
    element.scrollTop += y - bounds.top - element.clientHeight / 2;
    camera.save();
  }, [fieldFocus]);
  useLayoutEffect(() => {
    const position = pendingZoom.current;
    const element = viewport.current;
    if (!position || !element) return;
    // Resize and reposition in the same paint. A wheel burst may contain
    // multiple zoom events before React has committed the new dimensions.
    element.scrollLeft = position.left;
    element.scrollTop = position.top;
    pendingZoom.current = null;
    camera.save();
  }, [zoom]);
  useEffect(() => {
    setImageError(false);
    if (initialCamera) return;
    if (activeEvidence?.page) setPage(activeEvidence.page);
    if (activeEvidence?.sheet) setSheet(activeEvidence.sheet);
    const pos = activeEvidence?.cell && cellPosition(activeEvidence.cell);
    if (pos) {
      setRow(Math.floor((pos.row - 1) / 200) * 200 + 1);
      setColumn(Math.floor((pos.column - 1) / 100) * 100 + 1);
    }
  }, [source.id]);
  useEffect(() => setImageError(false), [page]);
  useEffect(() => {
    if (source.format !== "docx") return;
    const element = viewport.current;
    const target =
      element?.querySelectorAll<HTMLElement>("section.docx")[page - 1];
    if (element && target)
      element.scrollTop +=
        target.getBoundingClientRect().top -
        element.getBoundingClientRect().top -
        75;
  }, [page]);
  function zoomAt(next: number, x?: number, y?: number) {
    const element = viewport.current;
    if (!element) return;
    const bounds = element.getBoundingClientRect();
    const px = x == null ? element.clientWidth / 2 : x - bounds.left;
    const py = y == null ? element.clientHeight / 2 : y - bounds.top;
    const previous = zoomRef.current;
    const target = clampZoom(next);
    if (target === previous) return;
    const margin = camera.metricRef.current;
    const position = pendingZoom.current;
    const scrollLeft = position?.left ?? element.scrollLeft;
    const scrollTop = position?.top ?? element.scrollTop;
    const left =
      margin.x +
      ((scrollLeft + px - margin.x) * target) / previous -
      px;
    const top =
      margin.y + ((scrollTop + py - margin.y) * target) / previous - py;
    pendingZoom.current = { left, top };
    zoomRef.current = target;
    setZoom(target);
  }
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        zoomAt(
          zoomRef.current * Math.exp(-event.deltaY * 0.008),
          event.clientX,
          event.clientY,
        );
      } else {
        // Apply both axes from each event. Native trackpad scrolling can lock
        // onto one axis during a gesture and turn curved paths into steps.
        event.preventDefault();
        const horizontal = event.shiftKey && event.deltaX === 0;
        const x = horizontal ? event.deltaY : event.deltaX;
        const y = horizontal ? 0 : event.deltaY;
        const unitX = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientWidth : 1;
        const unitY = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1;
        element.scrollLeft += x * unitX * SCROLL_SENSITIVITY;
        element.scrollTop += y * unitY * SCROLL_SENSITIVITY;
      }
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, []);
  useEffect(() => {
    let alive = true;
    setNumericRegions([]);
    if (!imageFormat || source.role === "report") return;
    request<NumericRegion[]>(`${url}/numeric-regions`)
      .then((data) => {
        if (alive && Array.isArray(data)) setNumericRegions(data);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [url, imageFormat, source.role]);
  useEffect(() => {
    let alive = true;
    if (source.format !== "pdf") return;
    if (source.pdf_fields) {
      setFields(source.pdf_fields);
      return;
    }
    request<PDFField[]>(`${url}/fields`)
      .then((fields) => {
        if (alive && Array.isArray(fields)) setFields(fields);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [url, source.pdf_fields]);
  const numericTargets: NumericTarget[] = useMemo(
    () =>
      Object.values(source.facts)
        .filter((fact) => fact.kind === "money" && fact.value != null)
        .map((fact) => ({
          evidence: fact.evidence,
          value: fact.value!,
        })),
    [source.facts],
  );
  const preciseRegion = (evidence: Evidence) =>
    numericRegions.find(
      (region) => evidenceKey(region.evidence) === evidenceKey(evidence),
    ) ||
    (evidence.method === "manual-selection" && evidence.rect
      ? {
          evidence,
          rect: evidence.rect,
          text: evidence.text,
          value: evidence.text,
          method: "manual-selection",
        }
      : undefined);
  function offerNumber(
    evidence: Evidence,
    box?: DOMRect,
    explicitRef?: string,
  ) {
    if (!onUseNumber) return;
    const fact = Object.values(source.facts).find(
      (fact) =>
        fact.kind === "money" &&
        ((evidence.cell &&
          fact.evidence.cell === evidence.cell &&
          fact.evidence.sheet === evidence.sheet) ||
          (evidenceKey(fact.evidence) === evidenceKey(evidence) &&
            (canonicalNumber(evidence.text) === undefined ||
              canonicalNumber(fact.original_value ?? fact.value ?? "") ===
                canonicalNumber(evidence.text)))),
    );
    const numeric = preciseRegion(fact?.evidence || evidence);
    const text =
      canonicalNumber(evidence.text) !== undefined
        ? evidence.text
        : numeric?.text || fact?.original_value || fact?.value || "";
    const value = canonicalNumber(text);
    if (value === undefined) {
      setNumberSelection(undefined);
      return;
    }
    const region = numeric?.rect || evidence.rect;
    const location: Evidence = {
      ...evidence,
      document_id: source.id,
      text,
      method: "manual-selection",
      confidence: 100,
      ...(region ? { rect: region } : {}),
    };
    const root = container.current?.getBoundingClientRect();
    if (!root) return;
    let target = box;
    if (!target && evidence.cell)
      target = Array.from(
        viewport.current?.querySelectorAll<HTMLElement>("[data-cell]") || [],
      )
        .find(
          (element) =>
            element.dataset.cell === evidence.cell &&
            element.dataset.sheet === evidence.sheet,
        )
        ?.getBoundingClientRect();
    if (!target && region) {
      const canvas = viewport.current
        ?.querySelector<HTMLElement>(".file-page-canvas")
        ?.getBoundingClientRect();
      if (canvas)
        target = {
          left: canvas.left + region[0] * canvas.width,
          top: canvas.top + region[1] * canvas.height,
          width: (region[2] - region[0]) * canvas.width,
          height: (region[3] - region[1]) * canvas.height,
        } as DOMRect;
    }
    const x = Math.max(
      12,
      Math.min(root.width - 220, (target?.left ?? root.left + 20) - root.left),
    );
    const y = Math.max(
      66,
      Math.min(
        root.height - 55,
        (target?.top ?? root.bottom - 80) - root.top - 42,
      ),
    );
    setNumberSelection({
      document_id: source.id,
      value,
      evidence: location,
      ref: explicitRef || fact?.id,
      x,
      y,
    });
  }
  function readNumberSelection() {
    if (!onUseNumber || pan) return;
    const selection = window.getSelection();
    if (!selection?.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (!viewport.current?.contains(range.commonAncestorContainer)) return;
    const text = selection.toString().trim();
    if (canonicalNumber(text) === undefined) {
      setNumberSelection(undefined);
      return;
    }
    const box = range.getBoundingClientRect();
    const elementFor = (node: globalThis.Node) =>
      node.nodeType === 1 ? (node as HTMLElement) : node.parentElement;
    const startElement = elementFor(range.startContainer),
      endElement = elementFor(range.endContainer);
    const startCell = startElement?.closest("[data-cell]"),
      endCell = endElement?.closest("[data-cell]");
    const startBlock = startElement?.closest("[data-locator]"),
      endBlock = endElement?.closest("[data-locator]");
    if (startCell !== endCell || startBlock !== endBlock) {
      setNumberSelection(undefined);
      return;
    }
    const evidence: Evidence = {
      document_id: source.id,
      text,
      method: "manual-selection",
      confidence: 100,
    };
    if (imageFormat) {
      const canvas = viewport.current
        .querySelector<HTMLElement>(".file-page-canvas")
        ?.getBoundingClientRect();
      if (!canvas?.width || !canvas.height) return;
      evidence.page = page;
      const formElement = elementFor(
        range.startContainer,
      )?.closest<HTMLElement>("[data-pdf-field]");
      if (formElement) evidence.pdf_field = formElement.dataset.pdfField;
      evidence.rect = [
        Math.max(0, (box.left - canvas.left) / canvas.width),
        Math.max(0, (box.top - canvas.top) / canvas.height),
        Math.min(1, (box.right - canvas.left) / canvas.width),
        Math.min(1, (box.bottom - canvas.top) / canvas.height),
      ];
      evidence.locator = `Страница ${page}`;
    } else {
      const element =
        range.startContainer.nodeType === 1
          ? (range.startContainer as HTMLElement)
          : range.startContainer.parentElement;
      const cell = element?.closest<HTMLElement>("[data-cell]");
      const block = element?.closest<HTMLElement>("[data-locator]");
      if (cell) {
        evidence.cell = cell.dataset.cell;
        evidence.sheet = cell.dataset.sheet;
        evidence.locator = `${evidence.sheet}!${evidence.cell}`;
      } else if (block) evidence.locator = block.dataset.locator;
      else return;
    }
    const node =
      source.id === reportNodes[0]?.reported_evidence?.document_id
        ? reportNodes.find(
            (node) =>
              canonicalNumber(node.original_reported ?? node.reported ?? "") ===
                canonicalNumber(text) &&
              ((evidence.cell &&
                node.reported_evidence?.cell === evidence.cell &&
                node.reported_evidence.sheet === evidence.sheet) ||
                (evidence.rect &&
                  node.reported_evidence?.rect &&
                  (() => {
                    const r = node.reported_evidence!.rect!,
                      e = evidence.rect!;
                    return (
                      (e[0] + e[2]) / 2 >= r[0] &&
                      (e[0] + e[2]) / 2 <= r[2] &&
                      (e[1] + e[3]) / 2 >= r[1] &&
                      (e[1] + e[3]) / 2 <= r[3]
                    );
                  })())),
          )
        : undefined;
    offerNumber(evidence, box, node?.id);
  }
  const select = (evidence: Evidence) => {
    onSelectEvidence?.({ ...evidence, document_id: source.id });
    offerNumber(evidence);
  };
  function originalClick(event: React.MouseEvent<HTMLDivElement>) {
    if (slice || pan || window.getSelection()?.toString().trim()) return;
    if (
      down.current &&
      Math.hypot(
        event.clientX - down.current.x,
        event.clientY - down.current.y,
      ) > 5
    )
      return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width,
      y = (event.clientY - bounds.top) / bounds.height;
    if (structurePage) {
      const field = structureNodeAt(structurePage, x, y);
      if (field) {
        selectField(field);
        return;
      }
    }
    const node = reportNodes.find((node) => {
      const e = node.reported_evidence,
        r = e?.rect;
      return (
        r &&
        (e?.page || 1) === page &&
        x >= r[0] &&
        x <= r[2] &&
        y >= r[1] &&
        y <= r[3]
      );
    });
    if (node) {
      if (onUseNumber)
        offerNumber(
          {
            ...node.reported_evidence!,
            text: node.original_reported ?? node.reported ?? "",
            document_id: source.id,
          },
          undefined,
          node.id,
        );
      else onSelectReport?.(node, event);
    } else {
      const evidence = highlights.find((e) => {
        const r = e.rect;
        return (
          r &&
          (e.page || 1) === page &&
          x >= r[0] &&
          x <= r[2] &&
          y >= r[1] &&
          y <= r[3]
        );
      });
      if (evidence) select(evidence);
    }
  }
  return (
    <div
      className={`file-viewer original-viewer format-${source.format}${showStructure ? " structure-sidecar" : ""}`}
    >
      {showStructure && (
        <StructureTree
          key={page}
          page={structurePage}
          selected={selectedField?.id}
          onSelect={(node) => selectField(node, true)}
          onClose={() => { cancelSlice(); setStructureOpen(false); }}
          loading={structure.loading}
          error={structure.error}
          retry={structure.retry}
        />
      )}
      <div ref={container} className="structure-viewer-stage">
      <div className="viewer-floating-toolbar" role="group" aria-label="Инструменты просмотра документа">
        <div className="viewer-pill viewer-name-pill">
          <div>
            <strong>
              {title ||
                (source.role === "report"
                  ? "Итоговый отчет"
                  : "Первичный документ")}
            </strong>
            <span title={source.name}>{source.name}</span>
          </div>
        </div>
        <div className="viewer-pill viewer-page-pill">
          {imageFormat || source.format === "docx" ? (
            <>
              <span>
                Страница {page} / {pageCount}
              </span>
              <button
                className="icon-button"
                aria-label="Предыдущая страница"
                disabled={page <= 1 || sliceSaving}
                onClick={() => { cancelSlice(); setPage(page - 1); }}
              >
                <ArrowLeft size={14} />
              </button>
              <button
                className="icon-button"
                aria-label="Следующая страница"
                disabled={page >= pageCount || sliceSaving}
                onClick={() => { cancelSlice(); setPage(page + 1); }}
              >
                <ArrowRight size={14} />
              </button>
            </>
          ) : source.format === "xlsx" ? (
            <select
              value={sheet}
              aria-label="Лист документа"
              onChange={(event) => {
                setSheet(event.target.value);
                setRow(1);
                setColumn(1);
              }}
            >
              {(
                layout?.sheets || [
                  ...new Set(
                    Object.values(source.cells).map((cell) => cell.sheet),
                  ),
                ]
              ).map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
          ) : (
            <span>{source.format.toUpperCase()}</span>
          )}
        </div>
        <div className="viewer-pill viewer-navigation-pill">
          <button
            className={`icon-button ${!pan ? "active" : ""}`}
            aria-label="Выделять текст"
            aria-pressed={!pan}
            onClick={() => setPan(false)}
          >
            <TextCursorInput size={15} />
          </button>
          <button
            className={`icon-button ${pan ? "active" : ""}`}
            aria-label="Перемещать документ"
            aria-pressed={pan}
            disabled={!!slice}
            onClick={() => setPan(true)}
          >
            <Hand size={15} />
          </button>
        </div>
        <div
          className={`viewer-pill document-lock-pill ${locked ? "locked" : "unlocked"}`}
        >
          <button
            className="document-lock-button"
            aria-label={
              locked
                ? "Разрешить редактирование документа"
                : "Заблокировать редактирование документа"
            }
            aria-pressed={!locked}
            title={
              locked
                ? "Только просмотр. Нажмите для изменения полей"
                : "Изменения разрешены: двойной клик по ячейке или полю"
            }
            disabled={(!onEditField && !showStructure) || busy || !!onUseNumber || sliceSaving}
            onClick={() => { cancelSlice(); setLocked(!locked); }}
          >
            {locked ? <Lock size={15} /> : <LockOpen size={15} />}
            <span>{locked ? "Просмотр" : "Изменение"}</span>
          </button>
        </div>
        {actions}
        {imageFormat && (
          <div className="viewer-pill structure-toolbar-pill">
            <button
              type="button"
              className="structure-open-button"
              title="Показать иерархию полей, подсветку и пары ключ → значение"
              aria-pressed={showStructure}
              aria-controls={showStructure ? "document-structure" : undefined}
              disabled={!!onUseNumber || sliceSaving}
              onClick={() => { cancelSlice(); setStructureOpen(!structureOpen); }}
            >
              <ListTree size={16} aria-hidden="true" />
              <span>Иерархия полей</span>
            </button>
          </div>
        )}
      </div>
      <div
        className={`original-viewport ${pan ? "pan-mode" : ""}${structurePage ? " structure-select-mode" : ""}`}
        ref={viewport}
        tabIndex={slice ? -1 : undefined}
        onScroll={() => {
          setNumberSelection(undefined);
          camera.save();
        }}
        onKeyUp={() => {
          if (onUseNumber) window.setTimeout(readNumberSelection, 0);
        }}
        aria-label="Оригинал документа: прокрутка и масштабирование"
        onClickCapture={(event) => {
          if (pan) {
            event.preventDefault();
            event.stopPropagation();
          }
        }}
        onPointerDown={(event) => {
          down.current = { x: event.clientX, y: event.clientY };
          if (!pan || event.button !== 0) return;
          event.preventDefault();
          const element = event.currentTarget;
          drag.current = {
            x: event.clientX,
            y: event.clientY,
            left: element.scrollLeft,
            top: element.scrollTop,
          };
          element.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          event.currentTarget.scrollLeft =
            drag.current.left + drag.current.x - event.clientX;
          event.currentTarget.scrollTop =
            drag.current.top + drag.current.y - event.clientY;
        }}
        onPointerUp={() => {
          drag.current = null;
          if (onUseNumber) window.setTimeout(readNumberSelection, 0);
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        <div
          className="viewport-pan-space"
          style={{
            padding: `${camera.metrics.y}px ${camera.metrics.x}px`,
            minWidth: camera.metrics.width + 2 * camera.metrics.x,
            minHeight: camera.metrics.height + 2 * camera.metrics.y,
          }}
        >
          {imageFormat &&
            (imageError ? (
              <div className="viewer-error">
                Не удалось открыть страницу.{" "}
                <button onClick={() => setImageError(false)}>Повторить</button>
              </div>
            ) : (
              <div
                className="file-page-canvas"
                style={{
                  width: (Math.max(1, camera.metrics.width - 32) * zoom) / 100,
                }}
                onClick={originalClick}
                onDoubleClick={(event) => {
                  if (slice || locked || pan || !onEditField || onUseNumber) return;
                  const box = event.currentTarget.getBoundingClientRect(),
                    x = (event.clientX - box.left) / box.width,
                    y = (event.clientY - box.top) / box.height;
                  if (structurePage && structureNodeAt(structurePage, x, y)) return;
                  const node = reportNodes.find((node) => {
                    const e = node.reported_evidence,
                      r = e?.rect;
                    return (
                      r &&
                      (e?.page || 1) === page &&
                      x >= r[0] &&
                      x <= r[2] &&
                      y >= r[1] &&
                      y <= r[3]
                    );
                  });
                  if (node) {
                    onEditField({
                      kind: "reported",
                      target: node.id,
                      title: node.title || "Ячейка отчета",
                      value: node.reported || "",
                      evidence: node.reported_evidence,
                    });
                    return;
                  }
                  const fact = Object.values(source.facts).find((fact) => {
                    const e = fact.evidence,
                      r = e.rect;
                    return (
                      fact.kind === "money" &&
                      !fact.formula &&
                      !fact.dependencies?.length &&
                      r &&
                      (e.page || 1) === page &&
                      x >= r[0] &&
                      x <= r[2] &&
                      y >= r[1] &&
                      y <= r[3]
                    );
                  });
                  if (fact)
                    onEditField({
                      kind: "fact",
                      target: fact.id,
                      title: fact.label,
                      value: fact.value || "",
                      evidence: fact.evidence,
                    });
                }}
              >
                <img
                  src={`${url}/preview?page=${page}`}
                  alt={`Страница ${page} · ${source.name}`}
                  draggable={false}
                  onLoad={(event) => {
                    setRatio(
                      event.currentTarget.naturalHeight /
                        event.currentTarget.naturalWidth,
                    );
                    camera.restore();
                  }}
                  onError={() => setImageError(true)}
                />
                <div className="document-text-layer">
                  {source.blocks
                    .filter((block) => block.rect && (block.page || 1) === page)
                    .map((block, index) => (
                      <span
                        key={block.id || index}
                        style={{
                          left: `${block.rect![0] * 100}%`,
                          top: `${block.rect![1] * 100}%`,
                          width: `${(block.rect![2] - block.rect![0]) * 100}%`,
                          height: `${(block.rect![3] - block.rect![1]) * 100}%`,
                          fontSize: `${(block.rect![3] - block.rect![1]) * ratio * 82}cqw`,
                        }}
                      >
                        {block.text}
                      </span>
                    ))}
                </div>
                {highlights
                  .filter((e) => e.rect && (e.page || 1) === page)
                  .map((e, index) => (
                    <span
                      key={index}
                      className={`document-region ${source.role === "source" ? "field-context-region" : ""} ${isSameEvidence(e, activeEvidence) ? "active" : ""}`}
                      style={{
                        left: `${e.rect![0] * 100}%`,
                        top: `${e.rect![1] * 100}%`,
                        width: `${(e.rect![2] - e.rect![0]) * 100}%`,
                        height: `${(e.rect![3] - e.rect![1]) * 100}%`,
                      }}
                    />
                  ))}
                {source.role === "source" &&
                  highlights
                    .filter((e) => e.rect && (e.page || 1) === page)
                    .map((e, index) => {
                      const region = preciseRegion(e);
                      if (!region) return null;
                      const r = region.rect;
                      return (
                        <span
                          key={`number-${index}`}
                          className={`numeric-document-region ${isSameEvidence(e, activeEvidence) ? "active" : ""}`}
                          title={region.text}
                          style={{
                            left: `${r[0] * 100}%`,
                            top: `${r[1] * 100}%`,
                            width: `${(r[2] - r[0]) * 100}%`,
                            height: `${(r[3] - r[1]) * 100}%`,
                          }}
                        />
                      );
                    })}
                {Object.values(source.facts)
                  .filter(
                    (fact) =>
                      fact.kind === "money" &&
                      fact.manual &&
                      fact.original_value != null &&
                      fact.value != null &&
                      (fact.evidence.page || 1) === page,
                  )
                  .map((fact) => {
                    const r = preciseRegion(fact.evidence)?.rect;
                    if (!r) return null;
                    return (
                      <span
                        key={`corrected-${fact.id}`}
                        className="corrected-number-overlay"
                        style={{
                          left: `${r[0] * 100}%`,
                          top: `${r[1] * 100}%`,
                          width: `${(r[2] - r[0]) * 100}%`,
                          height: `${(r[3] - r[1]) * 100}%`,
                          fontSize: `${Math.min((r[3] - r[1]) * ratio * 0.75, ((r[2] - r[0]) / Math.max(1, String(fact.value).length)) * 1.6) * 100}cqw`,
                        }}
                      >
                        {fact.value}
                      </span>
                    );
                  })}
                {reportNodes
                  .filter(
                    (node) =>
                      node.manual &&
                      node.reported_evidence?.rect &&
                      (node.reported_evidence.page || 1) === page,
                  )
                  .map((node) => {
                    const r = node.reported_evidence!.rect!;
                    return (
                      <span
                        key={`corrected-report-${node.id}`}
                        className="corrected-number-overlay"
                        style={{
                          left: `${r[0] * 100}%`,
                          top: `${r[1] * 100}%`,
                          width: `${(r[2] - r[0]) * 100}%`,
                          height: `${(r[3] - r[1]) * 100}%`,
                          fontSize: `${Math.min((r[3] - r[1]) * ratio * 0.75, ((r[2] - r[0]) / Math.max(1, String(node.reported).length)) * 1.6) * 100}cqw`,
                        }}
                      >
                        {node.reported}
                      </span>
                    );
                  })}
                {fields
                  .filter((field) => field.page === page)
                  .map((field) => {
                    const r = field.rect;
                    return (
                      <span
                        key={field.name}
                        data-pdf-field={field.name}
                        className={`pdf-form-value ${locked ? "" : "editable"}`}
                        title={
                          locked ? field.name : `Изменить поле ${field.name}`
                        }
                        style={{
                          left: `${r[0] * 100}%`,
                          top: `${r[1] * 100}%`,
                          width: `${(r[2] - r[0]) * 100}%`,
                          height: `${(r[3] - r[1]) * 100}%`,
                          fontSize: `${Math.min((r[3] - r[1]) * ratio * 0.7, ((r[2] - r[0]) / Math.max(1, field.value.length)) * 1.6) * 100}cqw`,
                        }}
                        onClick={() => {
                          if (onUseNumber)
                            offerNumber({
                              document_id: source.id,
                              text: field.value,
                              page: field.page,
                              rect: field.rect,
                              locator: field.name,
                              pdf_field: field.name,
                              method: "pdf-form",
                              confidence: 100,
                            });
                        }}
                        onDoubleClick={(event) => {
                          if (locked || pan || !onEditField) return;
                          event.stopPropagation();
                          onEditField({
                            kind: "pdf_field",
                            target: `${source.id}:${field.name}`,
                            title: `${source.name} · ${field.name}`,
                            value: field.value,
                            evidence: {
                              document_id: source.id,
                              text: field.value,
                              page: field.page,
                              rect: field.rect,
                              locator: field.name,
                              pdf_field: field.name,
                              method: "pdf-form",
                              confidence: 100,
                            },
                          });
                        }}
                      >
                        {field.value || (!locked ? field.name : "")}
                      </span>
                    );
                  })}
                {structurePage && <StructureRegions page={structurePage} selected={selectedField} />}
                {slice && sliceBox && structurePage && (
                  <SliceOverlay
                    box={sliceBox}
                    size={structurePage.size}
                    cuts={slice}
                    pickingParent={slice.pickingParent}
                    saving={sliceSaving}
                    onCut={({ axis, at, tolerance }) => {
                      const current = slice[axis];
                      const match = current.find((value) => Math.abs(value - at) <= tolerance);
                      const position = Math.round(at * 1000) / 1000;
                      const indices = axis === "xCuts" ? [0, 2] : [1, 3];
                      const next = match !== undefined
                        ? current.filter((value) => value !== match)
                        : [...current, position].sort((a, b) => a - b);
                      if (match === undefined && (position - sliceBox[indices[0]] < .001 ||
                          sliceBox[indices[1]] - position < .001)) return;
                      const newCount = pieces / (current.length + 1) * (next.length + 1);
                      if (next.length > 8 || newCount > 36) {
                        setSliceError("Можно сделать не больше 8 разрезов по каждой оси и получить до 36 частей.");
                        return;
                      }
                      setSliceError("");
                      setSliceDraft({ ...slice, [axis]: next });
                      viewport.current?.focus({ preventScroll: true });
                    }}
                    onChoose={(index) => void commitSlice(index)}
                  />
                )}
                {annotations
                  .filter(
                    (item) =>
                      item.evidence.rect && (item.evidence.page || 1) === page,
                  )
                  .map((item, index) => {
                    const r =
                      preciseRegion(item.evidence)?.rect || item.evidence.rect!;
                    return (
                      <span
                        key={`variable-${index}`}
                        className="variable-region"
                        style={{
                          left: `${r[0] * 100}%`,
                          top: `${r[1] * 100}%`,
                          width: `${(r[2] - r[0]) * 100}%`,
                          height: `${(r[3] - r[1]) * 100}%`,
                        }}
                      >
                        <span className="variable-cell-label">
                          {item.label}
                        </span>
                      </span>
                    );
                  })}
                {reportNodes
                  .filter(
                    (node) =>
                      node.reported_evidence?.rect &&
                      (node.reported_evidence.page || 1) === page,
                  )
                  .map((node) => {
                    const r = node.reported_evidence!.rect!;
                    return (
                      <button
                        key={node.id}
                        className={`report-image-cell ${calculationExpression(node) ? node.status : "value-only"} ${selectedIds.includes(node.id) ? "selected" : ""}`}
                        style={{
                          left: `${r[0] * 100}%`,
                          top: `${r[1] * 100}%`,
                          width: `${(r[2] - r[0]) * 100}%`,
                          height: `${(r[3] - r[1]) * 100}%`,
                        }}
                        title={node.title}
                        aria-label={calculationExpression(node) ? `${node.title} · ${node.status === "verified" ? "Подтверждено" : "Вопрос"}` : `${node.title} · ${node.reported ?? node.value ?? ""}`}
                        aria-pressed={selectedIds.includes(node.id)}
                        onClick={(event) => {
                          if (event.detail === 0) {
                            if (onUseNumber)
                              offerNumber(
                                {
                                  ...node.reported_evidence!,
                                  document_id: source.id,
                                  text:
                                    node.original_reported ??
                                    node.reported ??
                                    "",
                                },
                                undefined,
                                node.id,
                              );
                            else onSelectReport?.(node, event);
                          }
                        }}
                      >
                        {calculationExpression(node) && <span className="original-status-dot" />}
                      </button>
                    );
                  })}
              </div>
            ))}
          {!imageFormat && (
            <div
              className="original-office-canvas"
              style={{ zoom: zoom / 100 }}
            >
              {source.format === "xlsx" ? (
                <SpreadsheetOriginal
                  url={url}
                  sheet={sheet}
                  row={row}
                  column={column}
                  document={source}
                  zoom={zoom}
                  highlights={highlights}
                  activeEvidence={activeEvidence}
                  reportNodes={reportNodes}
                  selectedIds={selectedIds}
                  onSelect={select}
                  onReport={(node, event) => {
                    if (onUseNumber)
                      offerNumber(
                        {
                          ...node.reported_evidence!,
                          document_id: source.id,
                          text: node.original_reported ?? node.reported ?? "",
                        },
                        undefined,
                        node.id,
                      );
                    else onSelectReport?.(node, event);
                  }}
                  onLayout={(layout) => {
                    setLayout(layout);
                    camera.restore();
                  }}
                  annotations={annotations}
                  onEdit={
                    locked || pan || onUseNumber ? undefined : onEditField
                  }
                />
              ) : source.format === "docx" ? (
                <WordOriginal
                  url={url}
                  highlights={highlights}
                  activeEvidence={activeEvidence}
                  onSelect={select}
                  onReady={camera.restore}
                  numericTargets={numericTargets}
                  onPages={setWordPages}
                  annotations={annotations}
                  source={source}
                  onEdit={
                    locked || pan || onUseNumber ? undefined : onEditField
                  }
                />
              ) : (
                <div className="plain-original-document">
                  {source.blocks.map((block, index) => (
                    <p
                      key={index}
                      onDoubleClick={() => {
                        if (!locked && onEditField)
                          onEditField({
                            kind: "text",
                            target: `${source.id}:${block.locator}`,
                            title: source.name + " · " + block.locator,
                            value: block.text,
                            evidence: block,
                          });
                      }}
                      className={
                        highlights.some((e) => e.locator === block.locator)
                          ? "original-word-highlight"
                          : ""
                      }
                    >
                      {block.text}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      {source.format === "xlsx" &&
        layout &&
        (layout.lastRow > 200 || layout.lastColumn > 100) && (
          <div className="sheet-pagination">
            <button
              disabled={row <= 1}
              onClick={() => setRow(Math.max(1, row - 200))}
            >
              ← Строки
            </button>
            <span>
              {row}–{Math.min(row + 199, layout.lastRow)}
            </span>
            <button
              disabled={row + 199 >= layout.lastRow}
              onClick={() => setRow(row + 200)}
            >
              Строки →
            </button>
            <button
              disabled={column <= 1}
              onClick={() => setColumn(Math.max(1, column - 100))}
            >
              ← Столбцы
            </button>
            <button
              disabled={column + 99 >= layout.lastColumn}
              onClick={() => setColumn(column + 100)}
            >
              Столбцы →
            </button>
          </div>
        )}
      {numberSelection && onUseNumber && (
        <div
          className="number-selection-popover"
          style={{ left: numberSelection.x, top: numberSelection.y }}
        >
          <button
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => {
              onUseNumber(numberSelection);
              setNumberSelection(undefined);
              window.getSelection()?.removeAllRanges();
            }}
          >
            Использовать как переменную
          </button>
        </div>
      )}
      {overlay && <div className="viewer-bottom-overlay">{overlay}</div>}
      {selectedField && (
        <StructureEditor
          key={`${page}:${selectedField.id}:${sliceBox?.join(",") ?? ""}`}
          node={selectedField}
          save={structure.save}
          locked={locked}
          slicing={!!slice}
          tools={sliceBox && sliceBox[2] > sliceBox[0] && sliceBox[3] > sliceBox[1] && (
            <SliceControls
              active={!!slice}
              pickingParent={!!slice?.pickingParent}
              saving={sliceSaving}
              error={sliceError}
              count={pieces}
              disabled={locked}
              onStart={startSlice}
              onNext={pickParent}
              onCancel={cancelSlice}
              onChoose={(index) => void commitSlice(index)}
            />
          )}
          onClose={() => setStructureSelection(undefined)}
        />
      )}
      </div>
    </div>
  );
}
