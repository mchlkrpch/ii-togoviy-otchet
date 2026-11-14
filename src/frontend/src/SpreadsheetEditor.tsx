import { useEffect, useRef, useState } from "react";
import { Lock, LockOpen, FileSpreadsheet, Check, Loader2 } from "lucide-react";
import {
  createUniver,
  LocaleType,
  mergeLocales,
  CommandType,
  IUniverInstanceService,
} from "@univerjs/presets";
import {
  UniverSheetsCorePreset,
  IRenderManagerService,
  SetZoomRatioOperation,
  SetScrollRelativeCommand,
  ScrollCommand,
  CopyCommand,
  CutCommand,
  PasteCommand,
} from "@univerjs/preset-sheets-core";
import type { FWorkbook, FRange } from "@univerjs/preset-sheets-core";
import type { IWorkbookData, IDisposable, ICellData } from "@univerjs/core";
import { UniverSheetsFilterPreset } from "@univerjs/preset-sheets-filter";
import { UniverSheetsSortPreset } from "@univerjs/preset-sheets-sort";
import { UniverSheetsConditionalFormattingPreset } from "@univerjs/preset-sheets-conditional-formatting";
import { UniverSheetsDataValidationPreset } from "@univerjs/preset-sheets-data-validation";
import { UniverSheetsFindReplacePreset } from "@univerjs/preset-sheets-find-replace";
import { UniverSheetsHyperLinkPreset } from "@univerjs/preset-sheets-hyper-link";
import { UniverSheetsNotePreset } from "@univerjs/preset-sheets-note";
import { UniverSheetsTablePreset } from "@univerjs/preset-sheets-table";
import coreRU from "@univerjs/preset-sheets-core/locales/ru-RU";
import filterRU from "@univerjs/preset-sheets-filter/locales/ru-RU";
import sortRU from "@univerjs/preset-sheets-sort/locales/ru-RU";
import conditionalRU from "@univerjs/preset-sheets-conditional-formatting/locales/ru-RU";
import validationRU from "@univerjs/preset-sheets-data-validation/locales/ru-RU";
import findRU from "@univerjs/preset-sheets-find-replace/locales/ru-RU";
import hyperlinkRU from "@univerjs/preset-sheets-hyper-link/locales/ru-RU";
import noteRU from "@univerjs/preset-sheets-note/locales/ru-RU";
import tableRU from "@univerjs/preset-sheets-table/locales/ru-RU";
import "@univerjs/preset-sheets-core/lib/index.css";
import "@univerjs/preset-sheets-filter/lib/index.css";
import "@univerjs/preset-sheets-sort/lib/index.css";
import "@univerjs/preset-sheets-conditional-formatting/lib/index.css";
import "@univerjs/preset-sheets-data-validation/lib/index.css";
import "@univerjs/preset-sheets-find-replace/lib/index.css";
import "@univerjs/preset-sheets-hyper-link/lib/index.css";
import "@univerjs/preset-sheets-note/lib/index.css";
import "@univerjs/preset-sheets-table/lib/index.css";
import "./spreadsheet.css";
import { request } from "./api";
import { canonicalNumber } from "./numericText";
import { calculationExpression } from "./calculationDisplay";
import type { DocumentViewerProps } from "./DocumentViewer";

type Runtime = ReturnType<typeof createUniver> & { book: FWorkbook };
const sharedEditor: {
  engine?: ReturnType<typeof createUniver>;
  container?: HTMLDivElement;
  books: Map<string, FWorkbook>;
  closing: Promise<unknown>;
} = { books: new Map(), closing: Promise.resolve() };
type MenuItem = { title: string; action: () => unknown; edit?: boolean };
const views = new Map<
  string,
  {
    sheet: string;
    zoom: number;
    scroll: {
      offsetX: number;
      offsetY: number;
      sheetViewStartRow: number;
      sheetViewStartColumn: number;
    };
  }
>();

export default function SpreadsheetEditor(props: DocumentViewerProps) {
  const { document: source, caseId } = props;
  const url = `/api/cases/${caseId}/documents/${source.id}`;
  const draftKey = `sheet-draft:${caseId}:${source.id}`;
  const host = useRef<HTMLDivElement>(null);
  const runtime = useRef<Runtime | undefined>(undefined);
  const latest = useRef(props);
  latest.current = props;
  const lockedRef = useRef(true);
  const dirty = useRef(false),
    saving = useRef(false),
    alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const saveTask = useRef<Promise<boolean> | undefined>(undefined);
  const [locked, setLocked] = useState(true);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [saveState, setSaveState] = useState("Все изменения сохранены");
  const [zoom, setZoom] = useState(100);
  const [menu, setMenu] = useState<string>();
  useEffect(() => {
    if (!menu) return;
    const outside = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest(".sheet-menu"))
        setMenu(undefined);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(undefined);
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", escape);
    };
  }, [menu]);
  const [pick, setPick] = useState<{
    value: string;
    sheet: string;
    cell: string;
    origin?: string;
    x: number;
    y: number;
  }>();
  const version = useRef(0);
  const metadataWrite = useRef(false);
  const cursor = useRef<{ x: number; y: number } | undefined>(undefined);
  const [bindingVersion, setBindingVersion] = useState(0);

  function selection(): FRange | undefined {
    const sheet = runtime.current?.book.getActiveSheet();
    return sheet?.getSelection()?.getActiveRange() || sheet?.getRange("A1");
  }
  function rememberView() {
    const sheet = runtime.current?.book.getActiveSheet();
    if (sheet) {
      const view = {
        sheet: sheet.getSheetName(),
        zoom: sheet.getZoom(),
        scroll: sheet.getScrollState(),
      };
      views.set(draftKey, view);
      try {
        sessionStorage.setItem("sheet-view:" + draftKey, JSON.stringify(view));
      } catch {}
    }
  }
  async function flush(rt = runtime.current): Promise<boolean> {
    if (saveTask.current) {
      const saved = await saveTask.current;
      return saved && dirty.current ? flush(rt) : saved;
    }
    const task = saveWorkbook(rt);
    saveTask.current = task;
    try {
      return await task;
    } finally {
      if (saveTask.current === task) saveTask.current = undefined;
    }
  }

  async function saveWorkbook(rt: Runtime | undefined): Promise<boolean> {
    if (!rt || !latest.current.onWorkbookChange) return false;
    if (rt.book.isCellEditing()) {
      await rt.book.endEditingAsync(true);
      dirty.current = true;
      version.current++;
    }
    if (!dirty.current) return true;
    metadataWrite.current = true;
    try {
      const before = rt.book.save(),
        seen = new Set<string>(),
        preferred = new Map<string, string>();
      for (const id of before.sheetOrder) {
        const sheet = before.sheets[id];
        for (const [row, columns] of Object.entries(sheet.cellData || {}))
          for (const [column, raw] of Object.entries(columns || {})) {
            const cell = raw as ICellData | undefined,
              origin = cell?.custom?.auditOrigin as string | undefined;
            if (!origin) continue;
            const location = rt.book
              .getSheetBySheetId(id)!
              .getRange(Number(row), Number(column))
              .getA1Notation()
              .split("!")
              .at(-1)!;
            const full = sheet.name + "!" + location;
            if (
              full ===
              (latest.current.document.cell_origins?.[origin] || origin)
            )
              preferred.set(origin, full);
          }
      }
      for (const id of before.sheetOrder) {
        const sheet = before.sheets[id],
          facade = rt.book.getSheetBySheetId(id);
        if (!facade) continue;
        for (const [row, columns] of Object.entries(sheet.cellData || {}))
          for (const [column, raw] of Object.entries(columns || {})) {
            const cell = raw as ICellData | undefined;
            if (!cell) continue;
            let origin = cell.custom?.auditOrigin as string | undefined;
            const location =
              sheet.name +
              "!" +
              facade
                .getRange(Number(row), Number(column))
                .getA1Notation()
                .split("!")
                .at(-1)!;
            if (
              !origin ||
              seen.has(origin) ||
              (preferred.has(origin) && preferred.get(origin) !== location)
            ) {
              origin = "added_" + crypto.randomUUID().replaceAll("-", "");
              facade
                .getRange(Number(row), Number(column))
                .setCustomMetaData({ ...cell.custom, auditOrigin: origin });
            }
            seen.add(origin);
          }
      }
    } catch (reason) {
      if (alive.current) setError(String(reason));
      return false;
    } finally {
      metadataWrite.current = false;
    }
    saving.current = true;
    const savedVersion = version.current;
    if (alive.current) setSaveState("Сохранение…");
    const snapshot = rt.book.save();
    try {
      localStorage.setItem(
        draftKey,
        JSON.stringify({ revision: latest.current.revision, snapshot }),
      );
    } catch {
      // A full browser storage does not prevent saving to the local server.
    }
    try {
      const ok = await latest.current.onWorkbookChange({
        kind: "workbook",
        target: source.id,
        values: { snapshot },
        reason: "Изменение в табличном редакторе",
      });
      if (ok && version.current === savedVersion) {
        dirty.current = false;
        localStorage.removeItem(draftKey);
        if (alive.current) {
          setSaveState("Все изменения сохранены");
          setError("");
        }
      } else if (!ok && alive.current) {
        setError(
          "Не удалось сохранить книгу. Черновик сохранен на этом устройстве. Повторите сохранение.",
        );
        setSaveState("Есть несохраненные изменения");
      }
      return Boolean(ok);
    } catch (reason) {
      if (alive.current) {
        setError(String(reason));
        setSaveState("Есть несохраненные изменения");
      }
      return false;
    } finally {
      saving.current = false;
      if (dirty.current && version.current !== savedVersion && alive.current)
        timer.current = setTimeout(() => void flush(), 700);
    }
  }
  function changed() {
    if (lockedRef.current || metadataWrite.current) return;
    dirty.current = true;
    version.current++;
    if (alive.current) {
      setBindingVersion((value) => value + 1);
      setSaveState("Есть изменения…");
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const rt = runtime.current;
      if (!rt) return;
      try {
        localStorage.setItem(
          draftKey,
          JSON.stringify({
            revision: latest.current.revision,
            snapshot: rt.book.save(),
          }),
        );
      } catch {
        setError("Не хватает места для локального черновика. Сохраните книгу.");
      }
      void flush();
    }, 700);
  }
  function setScale(ratio: number, clientX?: number, clientY?: number) {
    const rt = runtime.current;
    if (!rt) return;
    const sheet = rt.book.getActiveSheet();
    const render = rt.univer
      .__getInjector()
      .get(IRenderManagerService)
      .getRenderUnitById(rt.book.getId());
    const viewport = render?.scene.getMainViewport();
    const bounds = render?.engine.getCanvasElement().getBoundingClientRect();
    const previous = sheet.getZoom(),
      next = Math.max(0.25, Math.min(4, ratio));
    if (next === previous) return;
    const px =
      (clientX ?? cursor.current?.x) != null && bounds
        ? (clientX ?? cursor.current!.x) - bounds.left
        : (bounds?.width || 800) / 2;
    const py =
      (clientY ?? cursor.current?.y) != null && bounds
        ? (clientY ?? cursor.current!.y) - bounds.top
        : (bounds?.height || 600) / 2;
    const x = viewport?.viewportScrollX || 0,
      y = viewport?.viewportScrollY || 0;
    const skeleton = sheet.getSkeleton();
    const originX = skeleton?.rowHeaderWidthAndMarginLeft || 0,
      originY = skeleton?.columnHeaderHeightAndMarginTop || 0;
    const focusX = x + (px - originX) / previous,
      focusY = y + (py - originY) / previous;
    rt.univerAPI.syncExecuteCommand(SetZoomRatioOperation.id, {
      unitId: rt.book.getId(),
      subUnitId: sheet.getSheetId(),
      zoomRatio: next,
    });
    if (viewport)
      viewport.scrollToViewportPos({
        viewportScrollX: focusX - (px - originX) / next,
        viewportScrollY: focusY - (py - originY) / next,
      });
    setZoom(next * 100);
    rememberView();
  }
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    let rt: Runtime | undefined;
    let child: HTMLDivElement | undefined;
    const subscriptions: IDisposable[] = [];
    const owner = host.current;
    async function initialize() {
      await sharedEditor.closing;
      if (cancelled || !host.current) return;
      const snapshot = await request<IWorkbookData>(`${url}/workbook`);
      if (cancelled || !host.current) return;
      child = sharedEditor.container || document.createElement("div");
      child.style.height = "100%";
      host.current.appendChild(child);
      const created =
        sharedEditor.engine ||
        createUniver({
          locale: LocaleType.RU_RU,
          locales: {
            [LocaleType.RU_RU]: mergeLocales(
              coreRU,
              filterRU,
              sortRU,
              conditionalRU,
              validationRU,
              findRU,
              hyperlinkRU,
              noteRU,
              tableRU,
            ),
          },
          presets: [
            UniverSheetsCorePreset({
              container: child,
              ribbonType: "simple",
              disableAutoFocus: true,
              footer: {
                sheetBar: true,
                statisticBar: true,
                menus: true,
                zoomSlider: false,
              },
            }),
            UniverSheetsFilterPreset(),
            UniverSheetsSortPreset(),
            UniverSheetsConditionalFormattingPreset(),
            UniverSheetsDataValidationPreset(),
            UniverSheetsFindReplacePreset(),
            UniverSheetsHyperLinkPreset(),
            UniverSheetsNotePreset(),
            UniverSheetsTablePreset(),
          ],
        });
      sharedEditor.engine = created;
      sharedEditor.container = child;
      let book = sharedEditor.books.get(draftKey);
      if (!book) {
        snapshot.id = caseId + "-" + source.id;
        book = created.univerAPI.createWorkbook(snapshot);
        sharedEditor.books.set(draftKey, book);
      } else {
        const instances = created.univer
          .__getInjector()
          .get(IUniverInstanceService);
        instances.setCurrentUnitForType(book.getId());
        instances.focusUnit(book.getId());
      }
      window.dispatchEvent(new Event("resize"));
      if (!book) throw new Error("Не удалось открыть книгу");
      rt = { ...created, book };
      runtime.current = rt;
      book.setEditable(false);
      let saved = views.get(draftKey);
      if (!saved) {
        try {
          saved =
            JSON.parse(
              sessionStorage.getItem("sheet-view:" + draftKey) || "null",
            ) || undefined;
        } catch {}
      }
      if (saved) {
        const sheet = book.getSheetByName(saved.sheet);
        if (sheet) {
          book.setActiveSheet(sheet);
          sheet.zoom(saved.zoom);
          void created.univerAPI.executeCommand(ScrollCommand.id, {
            ...saved.scroll,
            unitId: book.getId(),
            sheetId: sheet.getSheetId(),
          });
          setZoom(saved.zoom * 100);
        }
      }
      subscriptions.push(
        created.univerAPI.addEvent(
          created.univerAPI.Event.CommandExecuted,
          (event) => {
            if (
              event.type === CommandType.MUTATION &&
              !/formula|calculate|selection|permission|zoom|scroll/.test(
                event.id,
              )
            )
              changed();
          },
        ),
      );
      subscriptions.push(
        created.univerAPI.addEvent(
          created.univerAPI.Event.CellClicked,
          (event) => {
            const sheet = event.worksheet,
              range = sheet.getRange(event.row, event.column);
            const name = sheet.getSheetName(),
              cell = range.getA1Notation().split("!").at(-1)!;
            const value = String(range.getValue() ?? "");
            const evidence = {
              document_id: source.id,
              sheet: name,
              cell,
              locator: `${name}!${cell}`,
              text: value,
              method: "xlsx",
              confidence: 100,
            };
            latest.current.onSelectEvidence?.(evidence);
            const node = latest.current.reportNodes?.find(
              (node) =>
                node.reported_evidence?.sheet === name &&
                node.reported_evidence?.cell === cell,
            );
            if (node)
              latest.current.onSelectReport?.(node, {
                shiftKey: false,
                metaKey: false,
                ctrlKey: false,
              });
            if (
              latest.current.onUseNumber &&
              !dirty.current &&
              canonicalNumber(value) !== undefined
            )
              setPick({
                value,
                sheet: name,
                cell,
                origin: event.workbook.save().sheets[sheet.getSheetId()].cellData?.[
                  event.row
                ]?.[event.column]?.custom?.auditOrigin as string | undefined,
                x: 120,
                y: 175,
              });
          },
        ),
      );
      subscriptions.push(
        created.univerAPI.addEvent(
          created.univerAPI.Event.SheetZoomChanged,
          (event) => {
            setZoom(event.zoom * 100);
            rememberView();
          },
        ),
      );
      subscriptions.push(
        created.univerAPI.addEvent(created.univerAPI.Event.Scroll, () =>
          rememberView(),
        ),
      );
      setReady(true);
      setBindingVersion((value) => value + 1);
      const draft = localStorage.getItem(draftKey);
      if (draft)
        setError(
          "На этом устройстве есть несохраненный черновик. В меню «Файл» можно восстановить его.",
        );
    }
    initialize().catch((reason) => {
      if (!cancelled) setError(String(reason));
    });
    return () => {
      cancelled = true;
      alive.current = false;
      clearTimeout(timer.current);
      rememberView();
      if (rt && (dirty.current || rt.book.isCellEditing())) {
        try {
          localStorage.setItem(
            draftKey,
            JSON.stringify({
              revision: latest.current.revision,
              snapshot: rt.book.save(),
            }),
          );
        } catch {}
        sharedEditor.closing = flush(rt);
      }
      subscriptions.forEach((item) => item.dispose());
      runtime.current = undefined;
      // Keep the provider and workbook models alive across file switches.
      // Disposing the whole injector while its nested React root is leaving
      // can invalidate the next workbook's menu and rendering dependencies.
      queueMicrotask(() => {
        if (child?.parentElement === owner) child.remove();
      });
    };
  }, [url]);
  useEffect(() => {
    const rt = runtime.current;
    if (!rt) return;
    const marks: IDisposable[] = [];
    for (const annotation of props.annotations || []) {
      const evidence = annotation.evidence;
      if (!evidence.sheet || !evidence.cell) continue;
      const sheet = rt.book.getSheetByName(evidence.sheet);
      if (sheet)
        marks.push(
          sheet
            .getRange(evidence.cell)
            .highlight({
              strokeWidth: 0,
              stroke: "transparent",
              fill: "#6fce8c55",
            }),
        );
    }
    for (const node of props.reportNodes || []) {
      const evidence = node.reported_evidence;
      if (!evidence?.sheet || !evidence.cell || !calculationExpression(node))
        continue;
      const sheet = rt.book.getSheetByName(evidence.sheet);
      if (sheet)
        marks.push(
          sheet
            .getRange(evidence.cell)
            .highlight({
              strokeWidth: 0,
              stroke: "transparent",
              fill: node.status === "verified" ? "#6fce8c44" : "#eac56b44",
            }),
        );
    }
    return () => marks.forEach((item) => item.dispose());
  }, [ready, props.annotations, props.reportNodes, bindingVersion]);
  useEffect(() => {
    const sheet =
      props.activeEvidence?.sheet &&
      runtime.current?.book.getSheetByName(props.activeEvidence.sheet);
    if (
      sheet &&
      sheet.getSheetId() !== runtime.current?.book.getActiveSheet().getSheetId()
    )
      runtime.current?.book.setActiveSheet(sheet);
  }, [ready, props.activeEvidence?.sheet]);
  useEffect(() => {
    if (props.onUseNumber) {
      lockedRef.current = true;
      setLocked(true);
      runtime.current?.book.setEditable(false);
    } else setPick(undefined);
  }, [props.onUseNumber]);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if (!(event.target instanceof HTMLCanvasElement)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const rt = runtime.current;
      if (!rt) return;
      if (event.ctrlKey || event.metaKey)
        setScale(
          rt.book.getActiveSheet().getZoom() * Math.exp(-event.deltaY * 0.006),
          event.clientX,
          event.clientY,
        );
      else {
        const unit = event.deltaMode === 1 ? 16 : 1;
        void rt.univerAPI.executeCommand(SetScrollRelativeCommand.id, {
          offsetX:
            (event.shiftKey && event.deltaX === 0
              ? event.deltaY
              : event.deltaX) *
            unit *
            2,
          offsetY:
            (event.shiftKey && event.deltaX === 0 ? 0 : event.deltaY) *
            unit *
            2,
        });
      }
    };
    element.addEventListener("wheel", wheel, { capture: true, passive: false });
    return () => element.removeEventListener("wheel", wheel, true);
  }, []);
  useEffect(() => {
    const before = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", before);
    return () => window.removeEventListener("beforeunload", before);
  }, []);
  function unlock() {
    const next = !locked;
    lockedRef.current = next;
    setLocked(next);
    runtime.current?.book.setEditable(!next);
  }
  async function command(id: string, params?: object) {
    const ok = await runtime.current?.univerAPI.executeCommand(id, params);
    if (ok === false)
      throw new Error("Действие недоступно для выбранной ячейки");
    return ok;
  }
  async function run(item: MenuItem) {
    setMenu(undefined);
    try {
      await item.action();
    } catch (reason) {
      setError(String(reason));
    }
  }
  function exportBlob(text: string, type: string, extension: string) {
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(new Blob([text], { type }));
    anchor.download = source.name.replace(/\.xlsx$/i, "") + "." + extension;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(anchor.href), 1000);
  }
  const menus: Record<string, MenuItem[]> = {
    Файл: [
      { title: "Сохранить", action: flush },
      {
        title: "Скачать XLSX",
        action: async () => {
          await flush();
          if (dirty.current) throw new Error("Сначала сохраните книгу");
          const anchor = document.createElement("a");
          anchor.href = url + "/edited-file";
          anchor.click();
        },
      },
      {
        title: "Скачать CSV · текущий лист",
        action: () => {
          const values = runtime
            .current!.book.getActiveSheet()
            .getDataRange()
            .getValues();
          exportBlob(
            values
              .map((row) =>
                row
                  .map(
                    (value) =>
                      '"' + String(value ?? "").replaceAll('"', '""') + '"',
                  )
                  .join(";"),
              )
              .join("\r\n"),
            "text/csv;charset=utf-8",
            "csv",
          );
        },
      },
      {
        title: "Скачать снимок книги JSON",
        action: () =>
          exportBlob(
            JSON.stringify(runtime.current?.book.save()),
            "application/json",
            "json",
          ),
      },
      {
        title: "Восстановить локальный черновик",
        edit: true,
        action: () => {
          const draft = JSON.parse(localStorage.getItem(draftKey) || "null");
          if (!draft) throw new Error("Сохраненного черновика нет");
          const rt = runtime.current!;
          rt.univerAPI.disposeUnit(rt.book.getId());
          rt.book = rt.univerAPI.createWorkbook(draft.snapshot)!;
          sharedEditor.books.set(draftKey, rt.book);
          rt.book.setEditable(true);
          changed();
          setError("");
        },
      },
      {
        title: "Печать выделения",
        action: () => {
          const content = selection()?.generateHTML();
          if (!content) return;
          const frame = document.createElement("iframe");
          frame.style.display = "none";
          document.body.append(frame);
          const doc = frame.contentDocument!;
          doc.open();
          doc.write(
            "<html><head><title>Печать таблицы</title></head><body>" +
              content +
              "</body></html>",
          );
          doc.close();
          setTimeout(() => {
            frame.contentWindow?.print();
            setTimeout(() => frame.remove(), 1000);
          }, 100);
        },
      },
    ],
    Правка: [
      {
        title: "Отменить · ⌘Z",
        edit: true,
        action: () => runtime.current?.univerAPI.undo(),
      },
      {
        title: "Повторить · ⌘⇧Z",
        edit: true,
        action: () => runtime.current?.univerAPI.redo(),
      },
      {
        title: "Вырезать · ⌘X",
        edit: true,
        action: () => command(CutCommand.id),
      },
      { title: "Копировать · ⌘C", action: () => command(CopyCommand.id) },
      {
        title: "Вставить · ⌘V",
        edit: true,
        action: () => command(PasteCommand.id),
      },
      {
        title: "Очистить значения",
        edit: true,
        action: () => selection()?.clear({ contentsOnly: true }),
      },
      {
        title: "Очистить форматирование",
        edit: true,
        action: () => selection()?.clear({ formatOnly: true }),
      },
      {
        title: "Найти и заменить · ⌘⇧H",
        action: () => command("ui.operation.open-replace-dialog"),
      },
    ],
    Вид: [
      { title: "Масштаб 100%", action: () => setScale(1) },
      {
        title: "Закрепить до выделенной строки",
        action: () =>
          runtime.current?.book
            .getActiveSheet()
            .setFrozenRows((selection()?.getRow() || 0) + 1),
      },
      {
        title: "Закрепить до выделенного столбца",
        action: () =>
          runtime.current?.book
            .getActiveSheet()
            .setFrozenColumns((selection()?.getColumn() || 0) + 1),
      },
      {
        title: "Снять закрепление",
        action: () => runtime.current?.book.getActiveSheet().cancelFreeze(),
      },
      {
        title: "Показать / скрыть сетку",
        action: () => {
          const sheet = runtime.current!.book.getActiveSheet();
          sheet.setHiddenGridlines(!sheet.hasHiddenGridLines());
        },
      },
    ],
    Вставка: [
      {
        title: "Строку выше",
        edit: true,
        action: () =>
          runtime.current?.book
            .getActiveSheet()
            .insertRowBefore(selection()?.getRow() || 0),
      },
      {
        title: "Строку ниже",
        edit: true,
        action: () =>
          runtime.current?.book
            .getActiveSheet()
            .insertRowAfter(selection()?.getRow() || 0),
      },
      {
        title: "Столбец слева",
        edit: true,
        action: () =>
          runtime.current?.book
            .getActiveSheet()
            .insertColumnBefore(selection()?.getColumn() || 0),
      },
      {
        title: "Столбец справа",
        edit: true,
        action: () =>
          runtime.current?.book
            .getActiveSheet()
            .insertColumnAfter(selection()?.getColumn() || 0),
      },
      {
        title: "Новый лист",
        edit: true,
        action: () =>
          runtime.current?.book.create(
            "Лист " + Date.now().toString().slice(-5),
            1000,
            26,
          ),
      },
      {
        title: "Ссылка",
        edit: true,
        action: () => command("sheet.operation.insert-hyper-link-toolbar"),
      },
      {
        title: "Таблица с заголовками",
        edit: true,
        action: () => command("sheet.operation.open-table-selector"),
      },
      {
        title: "Примечание к ячейке",
        edit: true,
        action: () => command("sheet.operation.add-note-popup"),
      },
    ],
    Формат: [
      {
        title: "Число · 1 234,56",
        edit: true,
        action: () => selection()?.setNumberFormat("#,##0.00"),
      },
      {
        title: "Денежный · ₽",
        edit: true,
        action: () => selection()?.setNumberFormat('#,##0.00 \"₽\"'),
      },
      {
        title: "Процент",
        edit: true,
        action: () => selection()?.setNumberFormat("0.00%"),
      },
      {
        title: "Дата",
        edit: true,
        action: () => selection()?.setNumberFormat("dd.mm.yyyy"),
      },
      {
        title: "Время",
        edit: true,
        action: () => selection()?.setNumberFormat("hh:mm:ss"),
      },
      {
        title: "Условное форматирование",
        edit: true,
        action: () =>
          command("sheet.operation.open.conditional.formatting.panel", {
            value: 3,
          }),
      },
      {
        title: "Полужирный · ⌘B",
        edit: true,
        action: () => command("sheet.command.set-range-bold"),
      },
      {
        title: "Курсив · ⌘I",
        edit: true,
        action: () => command("sheet.command.set-range-italic"),
      },
      {
        title: "Подчеркнутый · ⌘U",
        edit: true,
        action: () => command("sheet.command.set-range-underline"),
      },
      {
        title: "Зачеркнутый",
        edit: true,
        action: () => command("sheet.command.set-range-stroke"),
      },
      {
        title: "Перенос текста",
        edit: true,
        action: () => selection()?.setWrap(true),
      },
      {
        title: "Обрезать текст",
        edit: true,
        action: () => selection()?.setWrapStrategy(2),
      },
      {
        title: "Выровнять слева",
        edit: true,
        action: () => selection()?.setHorizontalAlignment("left"),
      },
      {
        title: "По центру",
        edit: true,
        action: () => selection()?.setHorizontalAlignment("center"),
      },
      {
        title: "Выровнять справа",
        edit: true,
        action: () => selection()?.setHorizontalAlignment("normal"),
      },
      {
        title: "Объединить ячейки",
        edit: true,
        action: () => selection()?.merge(),
      },
      {
        title: "Разъединить ячейки",
        edit: true,
        action: () => selection()?.breakApart(),
      },
    ],
    Данные: [
      {
        title: "Проверка данных · выпадающий список",
        edit: true,
        action: () =>
          command("data-validation.operation.open-validation-panel", {
            isAdd: true,
          }),
      },
      {
        title: "Сортировать А → Я",
        edit: true,
        action: () =>
          selection()?.sort({
            column: selection()!.getColumn(),
            ascending: true,
          }),
      },
      {
        title: "Сортировать Я → А",
        edit: true,
        action: () =>
          selection()?.sort({
            column: selection()!.getColumn(),
            ascending: false,
          }),
      },
      {
        title: "Создать фильтр",
        edit: true,
        action: () => selection()?.createFilter(),
      },
      {
        title: "Удалить фильтр",
        edit: true,
        action: () =>
          runtime.current?.book.getActiveSheet().getFilter()?.remove(),
      },
      {
        title: "Удалить выбранные строки",
        edit: true,
        action: () =>
          runtime.current?.book
            .getActiveSheet()
            .deleteRows(selection()!.getRow(), selection()!.getHeight()),
      },
      {
        title: "Удалить выбранные столбцы",
        edit: true,
        action: () =>
          runtime.current?.book
            .getActiveSheet()
            .deleteColumns(selection()!.getColumn(), selection()!.getWidth()),
      },
    ],
    Инструменты: [
      {
        title: "Найти и заменить",
        action: () => command("ui.operation.open-replace-dialog"),
      },
      {
        title: "Автоподбор высоты строк",
        edit: true,
        action: () =>
          runtime.current?.book
            .getActiveSheet()
            .setRowAutoHeight(selection()!.getRow(), selection()!.getHeight()),
      },
      {
        title: "Выделить весь лист",
        action: () =>
          runtime.current?.book.getActiveSheet().getDataRange().activate(),
      },
    ],
  };
  return (
    <div
      className="sheet-editor"
      aria-label="Табличный редактор"
      onPointerMove={(event) => {
        if (event.target instanceof HTMLCanvasElement)
          cursor.current = { x: event.clientX, y: event.clientY };
      }}
      onKeyDownCapture={(event) => {
        if (!(event.metaKey || event.ctrlKey)) return;
        if (event.key.toLowerCase() === "s") {
          event.preventDefault();
          event.stopPropagation();
          void flush();
        } else if (["+", "=", "-", "0"].includes(event.key)) {
          event.preventDefault();
          event.stopPropagation();
          const current = runtime.current?.book.getActiveSheet().getZoom() || 1;
          setScale(
            event.key === "0"
              ? 1
              : current * Math.exp(event.key === "-" ? -0.1 : 0.1),
          );
        }
      }}
    >
      <header className="sheet-titlebar">
        <FileSpreadsheet size={28} />
        <div className="sheet-title">
          <strong title={source.name}>
            {source.name.replace(/\.xlsx$/i, "")}
          </strong>
          <nav aria-label="Меню таблицы">
            {Object.keys(menus).map((name) => (
              <div className="sheet-menu" key={name}>
                <button
                  aria-expanded={menu === name}
                  onClick={() => setMenu(menu === name ? undefined : name)}
                >
                  {name}
                </button>
                {menu === name && (
                    <div role="menu">
                      {menus[name].map((item) => (
                        <button
                          key={item.title}
                          role="menuitem"
                          disabled={!ready || Boolean(item.edit && locked)}
                          onClick={() => void run(item)}
                        >
                          {item.title}
                        </button>
                      ))}
                    </div>
                )}
              </div>
            ))}
          </nav>
        </div>
        <span className="sheet-save-state" role="status">
          {saving.current ? <Loader2 size={14} /> : <Check size={14} />}{" "}
          {saveState}
        </span>
        <button
          className="sheet-lock"
          aria-label={
            locked
              ? "Разрешить редактирование документа"
              : "Заблокировать редактирование документа"
          }
          disabled={
            !ready || !props.onWorkbookChange || Boolean(props.onUseNumber)
          }
          onClick={unlock}
        >
          {locked ? <Lock size={15} /> : <LockOpen size={15} />}{" "}
          {locked ? "Просмотр" : "Изменение"}
        </button>
      </header>
      {error && (
        <div className="sheet-editor-error" role="alert">
          {error}
          <button onClick={() => void flush()}>Повторить сохранение</button>
          <button onClick={() => setError("")}>×</button>
        </div>
      )}
      {(props.annotations?.length || 0) > 0 && (
        <div className="sheet-bindings" aria-label="Переменные проверки">
          {props.annotations?.map((item, index) => (
            <span
              key={index}
              title={
                item.label +
                " · " +
                item.evidence.sheet +
                "!" +
                item.evidence.cell
              }
            >
              {item.label} · {item.evidence.cell}
            </span>
          ))}
        </div>
      )}
      <div className="sheet-engine" ref={host} aria-label="Ячейки таблицы" />
      {!ready && !error && (
        <div className="sheet-loading">Открываю табличный редактор…</div>
      )}
      <label className="sheet-smooth-zoom">
        Масштаб{" "}
        <input
          aria-label="Плавный масштаб таблицы"
          type="range"
          min="25"
          max="400"
          step="0.1"
          value={zoom}
          onChange={(event) => setScale(Number(event.target.value) / 100)}
        />
        <input
          aria-label="Масштаб таблицы в процентах"
          type="number"
          min="25"
          max="400"
          step="0.1"
          value={Number(zoom.toFixed(1))}
          onChange={(event) => setScale(Number(event.target.value) / 100)}
        />
        %
      </label>
      {props.overlay && (
        <div className="sheet-audit-overlay">{props.overlay}</div>
      )}
      {pick && props.onUseNumber && (
        <div
          className="number-selection-popover sheet-number-pick"
          style={{ left: pick.x, top: pick.y }}
        >
          <button
            onClick={() => {
              props.onUseNumber?.({
                document_id: source.id,
                value: canonicalNumber(pick.value)!,
                evidence: {
                  audit_origin: pick.origin,
                  document_id: source.id,
                  sheet: pick.sheet,
                  cell: pick.cell,
                  locator: `${pick.sheet}!${pick.cell}`,
                  text: pick.value,
                  method: "xlsx",
                  confidence: 100,
                },
              });
              setPick(undefined);
            }}
          >
            Использовать как переменную · {pick.value}
          </button>
        </div>
      )}
    </div>
  );
}
