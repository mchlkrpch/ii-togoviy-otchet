import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import type { Editor, JSONContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { TextStyleKit } from "@tiptap/extension-text-style";
import TextAlign from "@tiptap/extension-text-align";
import Highlight from "@tiptap/extension-highlight";
import { TableKit } from "@tiptap/extension-table";
import Subscript from "@tiptap/extension-subscript";
import Superscript from "@tiptap/extension-superscript";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import Typography from "@tiptap/extension-typography";
import {
  AlignCenter,
  AlignJustify,
  AlignLeft,
  AlignRight,
  Bold,
  Check,
  ChevronDown,
  FileText,
  Highlighter,
  ImagePlus,
  IndentDecrease,
  IndentIncrease,
  Italic,
  List,
  ListOrdered,
  Lock,
  LockOpen,
  Paintbrush,
  Printer,
  Redo2,
  Search,
  Strikethrough,
  Table2,
  Underline,
  Undo2,
} from "lucide-react";
import { request } from "./api";
import { canonicalNumber } from "./numericText";
import {
  auditHighlights,
  DocumentAttributes,
  DocumentImage,
  findMatches,
  PageBreak,
} from "./richDocumentExtensions";
import type { SearchState } from "./richDocumentExtensions";
import type { DocumentViewerProps } from "./DocumentViewer";
import type { Evidence } from "./types";
import "./rich-document.css";

export type DocumentSettings = {
  pageSize: "A4" | "Letter";
  orientation: "portrait" | "landscape";
  margins: { top: number; right: number; bottom: number; left: number };
  viewMode?: "paged" | "pageless";
  header?: string;
  footer?: string;
  footerPageNumber?: boolean;
};
type Snapshot = {
  content: JSONContent;
  settings: DocumentSettings;
  warnings?: string[];
};
type MenuItem = { label: string; edit?: boolean; action: () => unknown };
type FieldDialog = {
  title: string;
  label: string;
  value: string;
  apply: (value: string) => unknown;
};
type NumberPick = { value: string; evidence: Evidence; x: number; y: number };
type DocumentCamera = { zoom: number; x: number; y: number; outline: boolean };
const DEFAULT_SETTINGS: DocumentSettings = {
  pageSize: "A4",
  orientation: "portrait",
  margins: { top: 25.4, right: 25.4, bottom: 25.4, left: 25.4 },
  viewMode: "paged",
};
const cameras = new Map<string, DocumentCamera>();

function cameraFor(key: string) {
  if (cameras.has(key)) return cameras.get(key);
  try {
    return JSON.parse(
      sessionStorage.getItem(key) || "null",
    ) as DocumentCamera | null;
  } catch {
    return null;
  }
}

function textDownload(text: string, name: string, type: string) {
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(new Blob([text], { type }));
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(anchor.href), 1000);
}

export default function RichDocumentEditor(props: DocumentViewerProps) {
  const { document: source, caseId } = props;
  const endpoint = `/api/cases/${caseId}/documents/${source.id}`;
  const draftKey = `rich-draft:${caseId}:${source.id}`;
  const cameraKey = `rich-camera:${caseId}:${source.id}`;
  const initialCamera = useRef(cameraFor(cameraKey)).current;
  const viewport = useRef<HTMLDivElement>(null);
  const paper = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const loaded = useRef(false),
    alive = useRef(true),
    dirty = useRef(false);
  const savePromise = useRef<Promise<void> | undefined>(undefined);
  const editVersion = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const settingsRef = useRef<DocumentSettings>(DEFAULT_SETTINGS);
  const snapshotRef = useRef<Snapshot | undefined>(undefined);
  const flushRef = useRef<() => Promise<void>>(async () => {});
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [locked, setLocked] = useState(true);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [saveState, setSaveState] = useState("Все изменения сохранены");
  const [zoom, setZoom] = useState(initialCamera?.zoom || 100);
  const [paperHeight, setPaperHeight] = useState(1123);
  const zoomRef = useRef(zoom);
  const pendingZoom = useRef<{ x: number; y: number } | undefined>(undefined);
  const [outline, setOutline] = useState(initialCamera?.outline ?? true);
  const [menu, setMenu] = useState<string>();
  useEffect(() => {
    if (!menu) return;
    const outside = (event: PointerEvent) => {
      if (
        !(event.target instanceof Element) ||
        !event.target.closest(".rich-menu")
      )
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
  const [dialog, setDialog] = useState<FieldDialog>();
  const [pageDialog, setPageDialog] = useState(false);
  const [pageDraft, setPageDraft] = useState(DEFAULT_SETTINGS);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState<SearchState>({
    query: "",
    caseSensitive: false,
  });
  const searchRef = useRef(search);
  searchRef.current = search;
  const [replacement, setReplacement] = useState("");
  const [searchIndex, setSearchIndex] = useState(0);
  const [pick, setPick] = useState<NumberPick>();
  const [revision, rerender] = useState(0);

  const markChanged = useCallback(
    (editor: Editor) => {
      if (!loaded.current) return;
      snapshotRef.current = {
        content: editor.getJSON(),
        settings: settingsRef.current,
      };
      dirty.current = true;
      editVersion.current++;
      setSaveState("Есть изменения…");
      clearTimeout(timer.current);
      try {
        localStorage.setItem(
          draftKey,
          JSON.stringify({
            revision: latest.current.revision,
            snapshot: snapshotRef.current,
          }),
        );
      } catch {
        setError("Не удалось записать локальный черновик. Сохраните документ.");
      }
      timer.current = setTimeout(() => void flushRef.current(), 850);
      rerender((value) => value + 1);
    },
    [draftKey],
  );

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ link: { openOnClick: false } }),
      TextStyleKit,
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      Highlight.configure({ multicolor: true }),
      TableKit.configure({ table: { resizable: true } }),
      DocumentImage,
      Subscript,
      Superscript,
      TaskList,
      TaskItem.configure({ nested: true }),
      Typography,
      DocumentAttributes,
      PageBreak,
    ],
    content: { type: "doc", content: [{ type: "paragraph" }] },
    editable: false,
    immediatelyRender: false,
    editorProps: {
      attributes: {
        class: "rich-document-content",
        spellcheck: "true",
        "aria-label": "Содержимое документа",
      },
      handlePaste(view, event) {
        const file = Array.from(event.clipboardData?.files || []).find((item) =>
          item.type.startsWith("image/"),
        );
        if (!file || !view.editable) return false;
        const reader = new FileReader();
        reader.onload = () => {
          const image = view.state.schema.nodes.image.create({
            src: String(reader.result),
            alt: file.name,
          });
          view.dispatch(view.state.tr.replaceSelectionWith(image));
        };
        reader.readAsDataURL(file);
        event.preventDefault();
        return true;
      },
    },
    onUpdate: ({ editor }) => markChanged(editor),
    onSelectionUpdate: () => rerender((value) => value + 1),
  });

  const flush = useCallback(async () => {
    if (savePromise.current) {
      await savePromise.current;
      return flushRef.current();
    }
    const change = latest.current.onWorkbookChange;
    const snapshot = snapshotRef.current;
    if (!change || !snapshot || !dirty.current) return;
    const version = editVersion.current;
    if (alive.current) setSaveState("Сохранение…");
    const operation = (async () => {
      try {
        const ok = await change({
          kind: "rich_document",
          target: source.id,
          values: { snapshot },
          reason: "Редактирование документа",
        });
        if (ok && editVersion.current === version) {
          dirty.current = false;
          localStorage.removeItem(draftKey);
          if (alive.current) {
            setSaveState("Все изменения сохранены");
            setError("");
          }
        } else if (!ok && alive.current) {
          setSaveState("Есть несохраненные изменения");
          setError(
            "Не удалось сохранить документ. Локальный черновик доступен в меню «Файл».",
          );
        }
      } catch (reason) {
        if (alive.current) {
          setError(String(reason));
          setSaveState("Есть несохраненные изменения");
        }
      } finally {
        if (dirty.current && editVersion.current !== version && alive.current)
          timer.current = setTimeout(() => void flushRef.current(), 850);
      }
    })();
    savePromise.current = operation;
    try {
      await operation;
    } finally {
      if (savePromise.current === operation) savePromise.current = undefined;
    }
  }, [source.id, draftKey]);
  flushRef.current = flush;

  function rememberCamera() {
    const element = viewport.current;
    if (!element) return;
    const camera = {
      zoom: zoomRef.current,
      x: element.scrollLeft,
      y: element.scrollTop,
      outline,
    };
    cameras.set(cameraKey, camera);
    try {
      sessionStorage.setItem(cameraKey, JSON.stringify(camera));
    } catch {
      /* The in-memory camera remains available. */
    }
  }

  useEffect(() => {
    if (!editor) return;
    alive.current = true;
    let cancelled = false;
    request<Snapshot>(`${endpoint}/rich-document`)
      .then((snapshot) => {
        if (cancelled || editor.isDestroyed) return;
        const pageSettings = {
          ...DEFAULT_SETTINGS,
          ...snapshot.settings,
          margins: {
            ...DEFAULT_SETTINGS.margins,
            ...snapshot.settings?.margins,
          },
        };
        settingsRef.current = pageSettings;
        setWarnings(snapshot.warnings || []);
        setSettings(pageSettings);
        editor.commands.setContent(snapshot.content, { emitUpdate: false });
        snapshotRef.current = {
          content: editor.getJSON(),
          settings: pageSettings,
        };
        loaded.current = true;
        setReady(true);
        if (localStorage.getItem(draftKey))
          setError(
            "На этом устройстве есть локальный черновик. Восстановление доступно в меню «Файл».",
          );
      })
      .catch((reason) => {
        if (!cancelled) setError(String(reason));
      });
    return () => {
      cancelled = true;
      alive.current = false;
      clearTimeout(timer.current);
      rememberCamera();
      if (dirty.current && snapshotRef.current) {
        try {
          localStorage.setItem(
            draftKey,
            JSON.stringify({
              revision: latest.current.revision,
              snapshot: snapshotRef.current,
            }),
          );
        } catch {
          /* The server save is also attempted. */
        }
        void flushRef.current();
      }
      loaded.current = false;
    };
  }, [editor, endpoint]);

  useLayoutEffect(() => {
    const element = viewport.current;
    const page = paper.current?.getBoundingClientRect();
    if (!ready || !element || !page) return;
    const space = element.querySelector<HTMLElement>(
      ".rich-document-paper-space",
    );
    const bounds = element.getBoundingClientRect();
    const pageStart = page.left - bounds.left + element.scrollLeft;
    const leftInset = Math.max(16, (element.clientWidth - page.width) / 2);
    element.scrollLeft = initialCamera?.x ?? pageStart - leftInset;
    element.scrollTop =
      initialCamera?.y ??
      Number.parseFloat(space ? getComputedStyle(space).paddingTop : "0");
  }, [ready]);

  useEffect(() => {
    if (!editor) return;
    const plugin = auditHighlights(
      () => latest.current.annotations || [],
      () => searchRef.current,
    );
    editor.registerPlugin(plugin);
    return () => {
      if (!editor.isDestroyed) editor.unregisterPlugin(plugin.spec.key!);
    };
  }, [editor]);
  useEffect(() => {
    if (editor && !editor.isDestroyed)
      editor.view.dispatch(editor.state.tr.setMeta("auditHighlight", true));
  }, [editor, props.annotations, search]);
  useEffect(() => {
    if (props.onUseNumber) {
      setLocked(true);
      editor?.setEditable(false);
      void flushRef.current();
    } else setPick(undefined);
  }, [props.onUseNumber, editor]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);

  function scaleAt(value: number, clientX?: number, clientY?: number) {
    const element = viewport.current,
      canvas = paper.current;
    if (!element || !canvas) return;
    const next = Math.max(25, Math.min(400, value));
    const bounds = element.getBoundingClientRect();
    const content = canvas.getBoundingClientRect();
    const x = clientX == null ? bounds.width / 2 : clientX - bounds.left;
    const y = clientY == null ? bounds.height / 2 : clientY - bounds.top;
    const originX = content.left - bounds.left + element.scrollLeft;
    const originY = content.top - bounds.top + element.scrollTop;
    const cameraX = pendingZoom.current?.x ?? element.scrollLeft;
    const cameraY = pendingZoom.current?.y ?? element.scrollTop;
    pendingZoom.current = {
      x: originX + ((cameraX + x - originX) * next) / zoomRef.current - x,
      y: originY + ((cameraY + y - originY) * next) / zoomRef.current - y,
    };
    zoomRef.current = next;
    setZoom(next);
  }
  useLayoutEffect(() => {
    if (pendingZoom.current && viewport.current) {
      viewport.current.scrollLeft = pendingZoom.current.x;
      viewport.current.scrollTop = pendingZoom.current.y;
      pendingZoom.current = undefined;
      rememberCamera();
    }
  }, [zoom]);
  useLayoutEffect(() => {
    const element = paper.current;
    if (!element) return;
    const observer = new ResizeObserver(() =>
      setPaperHeight(element.offsetHeight),
    );
    observer.observe(element);
    setPaperHeight(element.offsetHeight);
    return () => observer.disconnect();
  }, [editor]);
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      if (event.ctrlKey || event.metaKey)
        scaleAt(
          zoomRef.current * Math.exp(-event.deltaY * 0.006),
          event.clientX,
          event.clientY,
        );
      else {
        element.scrollLeft +=
          (event.shiftKey && event.deltaX === 0 ? event.deltaY : event.deltaX) *
          2;
        element.scrollTop +=
          (event.shiftKey && event.deltaX === 0 ? 0 : event.deltaY) * 2;
      }
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [ready]);

  function run(action: () => unknown) {
    setMenu(undefined);
    try {
      const result = action();
      if (result instanceof Promise)
        result.catch((reason) => setError(String(reason)));
    } catch (reason) {
      setError(String(reason));
    }
  }
  function updateSettings(next: DocumentSettings) {
    settingsRef.current = next;
    setSettings(next);
    if (editor) markChanged(editor);
  }
  function paragraphStyle(attributes: Record<string, unknown>) {
    if (!editor) return;
    editor
      .chain()
      .focus()
      .command(({ tr, state }) => {
        state.doc.nodesBetween(
          state.selection.from,
          state.selection.to,
          (node, position) => {
            if (node.type.name === "paragraph" || node.type.name === "heading")
              tr.setNodeMarkup(position, undefined, {
                ...node.attrs,
                ...attributes,
              });
          },
        );
        return true;
      })
      .run();
  }
  function indent(delta: number) {
    if (!editor) return;
    if (
      editor.isActive("listItem") &&
      (delta > 0
        ? editor.commands.sinkListItem("listItem")
        : editor.commands.liftListItem("listItem"))
    )
      return;
    const attrs = editor.getAttributes(
      editor.isActive("heading") ? "heading" : "paragraph",
    );
    paragraphStyle({
      indent: Math.max(0, Math.min(100, Number(attrs.indent || 0) + delta)),
    });
  }
  function prompt(
    title: string,
    label: string,
    value: string,
    apply: (value: string) => unknown,
  ) {
    setDialog({ title, label, value, apply });
  }
  function link() {
    prompt(
      "Вставить ссылку",
      "Адрес ссылки",
      editor?.getAttributes("link").href || "",
      (value) =>
        value
          ? editor
              ?.chain()
              .focus()
              .extendMarkRange("link")
              .setLink({ href: value })
              .run()
          : editor?.chain().focus().unsetLink().run(),
    );
  }
  function insertImage(file: File) {
    if (file.size > 15 * 1024 * 1024) {
      setError("Изображение должно быть меньше 15 МБ.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () =>
      editor
        ?.chain()
        .focus()
        .setImage({ src: String(reader.result), alt: file.name })
        .run();
    reader.readAsDataURL(file);
  }
  function printDocument() {
    if (!editor) return;
    const frame = document.createElement("iframe");
    frame.style.display = "none";
    document.body.appendChild(frame);
    const target = frame.contentDocument!;
    const margin = settingsRef.current.margins;
    target.open();
    target.write(
      `<html><head><title>${source.name.replaceAll("<", "&lt;")}</title><style>@page{size:${settingsRef.current.pageSize} ${settingsRef.current.orientation};margin:${margin.top}mm ${margin.right}mm ${margin.bottom}mm ${margin.left}mm}body{font:11pt Arial;line-height:1.15}table{border-collapse:collapse;width:100%}td,th{border:1px solid #999;padding:4px}img{max-width:100%}[data-page-break]{break-before:page}p{margin:0 0 8pt}</style></head><body>${editor.getHTML()}</body></html>`,
    );
    target.close();
    setTimeout(() => {
      frame.contentWindow?.print();
      setTimeout(() => frame.remove(), 2000);
    }, 150);
  }
  async function downloadDocx() {
    await flush();
    if (dirty.current)
      throw new Error("Сохраните изменения перед выгрузкой DOCX.");
    const anchor = document.createElement("a");
    anchor.href = `${endpoint}/edited-file`;
    anchor.click();
  }
  async function downloadExport(format: "pdf" | "html") {
    await flush();
    if (dirty.current) throw new Error("Сохраните изменения перед выгрузкой.");
    const anchor = document.createElement("a");
    anchor.href = `${endpoint}/rich-document/export/${format}`;
    anchor.click();
  }
  function restoreDraft() {
    const draft = JSON.parse(localStorage.getItem(draftKey) || "null");
    if (!draft?.snapshot || !editor)
      throw new Error("Локального черновика нет.");
    settingsRef.current = { ...DEFAULT_SETTINGS, ...draft.snapshot.settings };
    setSettings(settingsRef.current);
    editor.commands.setContent(draft.snapshot.content);
    setError("");
  }
  async function copySelection(cut = false) {
    if (!editor) return;
    const slice = editor.state.selection.content();
    const serialized = editor.view.serializeForClipboard(slice);
    if (navigator.clipboard.write && window.ClipboardItem) {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/plain": new Blob([serialized.text], { type: "text/plain" }),
          "text/html": new Blob([serialized.dom.innerHTML], {
            type: "text/html",
          }),
        }),
      ]);
    } else {
      await navigator.clipboard.writeText(serialized.text);
    }
    if (cut) editor.chain().focus().deleteSelection().run();
  }
  async function pasteSelection() {
    if (!editor) return;
    if (navigator.clipboard.read) {
      const items = await navigator.clipboard.read();
      const formatted = items.find((item) => item.types.includes("text/html"));
      if (formatted) {
        const html = await (await formatted.getType("text/html")).text();
        const parsed = new DOMParser().parseFromString(html, "text/html");
        parsed.querySelectorAll("[data-audit-id]").forEach((element) => {
          element.removeAttribute("data-audit-id");
        });
        editor.chain().focus().insertContent(parsed.body.innerHTML).run();
        return;
      }
    }
    const text = await navigator.clipboard.readText();
    editor
      .chain()
      .focus()
      .insertContent(
        text.split("\n").map((line) => ({
          type: "paragraph",
          content: line ? [{ type: "text", text: line }] : [],
        })),
      )
      .run();
  }
  function readSelection() {
    if (!editor || !latest.current.onUseNumber) return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const value = canonicalNumber(selection.toString().trim());
    if (value === undefined) return;
    const element =
      selection.anchorNode instanceof Element
        ? selection.anchorNode
        : selection.anchorNode?.parentElement;
    const block =
      element?.closest("tr[data-audit-id]") ||
      element?.closest("[data-audit-id]");
    if (!block || !paper.current?.contains(block)) return;
    const box = selection.getRangeAt(0).getBoundingClientRect();
    setPick({
      value,
      evidence: {
        document_id: source.id,
        locator: block.getAttribute("data-audit-id")!,
        text: selection.toString().trim(),
        method: "manual-selection",
        confidence: 100,
      },
      x: box.left,
      y: box.bottom + 6,
    });
  }
  const matches = editor ? findMatches(editor.state.doc, search) : [];
  function navigateMatch(direction: number) {
    if (!editor || !matches.length) return;
    const index = (searchIndex + direction + matches.length) % matches.length;
    setSearchIndex(index);
    editor
      .chain()
      .focus()
      .setTextSelection(matches[index])
      .scrollIntoView()
      .run();
  }
  function replace(all: boolean) {
    if (!editor || !matches.length) return;
    const transaction = editor.state.tr;
    const targets = all
      ? [...matches].reverse()
      : [matches[Math.min(searchIndex, matches.length - 1)]];
    for (const match of targets)
      transaction.insertText(replacement, match.from, match.to);
    editor.view.dispatch(transaction);
  }
  const headings = useMemo(() => {
    const result: { text: string; position: number; level: number }[] = [];
    editor?.state.doc.descendants((node, position) => {
      if (node.type.name === "heading")
        result.push({
          text: node.textContent,
          position,
          level: node.attrs.level,
        });
    });
    return result;
  }, [editor, revision]);

  const menus: Record<string, MenuItem[]> = {
    Файл: [
      { label: "Сохранить · ⌘S", action: flush },
      { label: "Скачать DOCX", action: downloadDocx },
      { label: "Скачать PDF", action: () => downloadExport("pdf") },
      {
        label: "Скачать текст TXT",
        action: () =>
          textDownload(
            editor?.getText() || "",
            source.name.replace(/\.docx$/i, ".txt"),
            "text/plain;charset=utf-8",
          ),
      },
      {
        label: "Скачать HTML",
        action: () => downloadExport("html"),
      },
      {
        label: "Восстановить локальный черновик",
        edit: true,
        action: restoreDraft,
      },
      {
        label: "Параметры страницы",
        edit: true,
        action: () => {
          setPageDraft(settings);
          setPageDialog(true);
        },
      },
      { label: "Печать · ⌘P", action: printDocument },
    ],
    Правка: [
      {
        label: "Отменить · ⌘Z",
        edit: true,
        action: () => editor?.chain().focus().undo().run(),
      },
      {
        label: "Повторить · ⌘⇧Z",
        edit: true,
        action: () => editor?.chain().focus().redo().run(),
      },
      {
        label: "Вырезать · ⌘X",
        edit: true,
        action: () => copySelection(true),
      },
      {
        label: "Копировать · ⌘C",
        action: () => copySelection(),
      },
      {
        label: "Вставить · ⌘V",
        edit: true,
        action: pasteSelection,
      },
      {
        label: "Выделить все · ⌘A",
        action: () => editor?.chain().focus().selectAll().run(),
      },
      { label: "Найти и заменить · ⌘H", action: () => setSearchOpen(true) },
    ],
    Вид: [
      {
        label: outline
          ? "Скрыть структуру документа"
          : "Показать структуру документа",
        action: () => setOutline(!outline),
      },
      { label: "Масштаб 100%", action: () => scaleAt(100) },
      { label: "Масштаб 150%", action: () => scaleAt(150) },
      {
        label:
          settings.viewMode === "pageless"
            ? "Показать страницы"
            : "Без разбивки по страницам",
        action: () =>
          updateSettings({
            ...settings,
            viewMode: settings.viewMode === "pageless" ? "paged" : "pageless",
          }),
      },
    ],
    Вставка: [
      {
        label: "Изображение с устройства",
        edit: true,
        action: () => fileInput.current?.click(),
      },
      { label: "Ссылка · ⌘K", edit: true, action: link },
      {
        label: "Таблица…",
        edit: true,
        action: () =>
          prompt("Вставить таблицу", "Строки × столбцы", "3 × 3", (value) => {
            const size = value.match(/(\d+)\D+(\d+)/);
            if (
              !size ||
              Number(size[1]) < 1 ||
              Number(size[2]) < 1 ||
              Number(size[1]) > 50 ||
              Number(size[2]) > 20
            )
              throw new Error(
                "Укажите от 1 до 50 строк и от 1 до 20 столбцов.",
              );
            editor
              ?.chain()
              .focus()
              .insertTable({
                rows: Number(size[1]),
                cols: Number(size[2]),
                withHeaderRow: true,
              })
              .run();
          }),
      },
      {
        label: "Горизонтальная линия",
        edit: true,
        action: () => editor?.chain().focus().setHorizontalRule().run(),
      },
      {
        label: "Разрыв страницы",
        edit: true,
        action: () =>
          editor?.chain().focus().insertContent({ type: "pageBreak" }).run(),
      },
      {
        label: "Специальный символ…",
        edit: true,
        action: () =>
          prompt("Специальные символы", "Символы для вставки", "©", (value) =>
            editor
              ?.chain()
              .focus()
              .insertContent({ type: "text", text: value })
              .run(),
          ),
      },
      {
        label: "Дата",
        edit: true,
        action: () =>
          editor
            ?.chain()
            .focus()
            .insertContent(
              new Date().toLocaleDateString("ru-RU", {
                timeZone: "Europe/Moscow",
              }),
            )
            .run(),
      },
      {
        label: settings.footerPageNumber
          ? "Убрать номер страницы"
          : "Номер страницы",
        edit: true,
        action: () =>
          updateSettings({
            ...settings,
            footerPageNumber: !settings.footerPageNumber,
          }),
      },
    ],
    Формат: [
      {
        label: "Полужирный · ⌘B",
        edit: true,
        action: () => editor?.chain().focus().toggleBold().run(),
      },
      {
        label: "Курсив · ⌘I",
        edit: true,
        action: () => editor?.chain().focus().toggleItalic().run(),
      },
      {
        label: "Подчеркнутый · ⌘U",
        edit: true,
        action: () => editor?.chain().focus().toggleUnderline().run(),
      },
      {
        label: "Зачеркнутый",
        edit: true,
        action: () => editor?.chain().focus().toggleStrike().run(),
      },
      {
        label: "Верхний индекс",
        edit: true,
        action: () => editor?.chain().focus().toggleSuperscript().run(),
      },
      {
        label: "Нижний индекс",
        edit: true,
        action: () => editor?.chain().focus().toggleSubscript().run(),
      },
      {
        label: "Очистить форматирование",
        edit: true,
        action: () =>
          editor?.chain().focus().unsetAllMarks().clearNodes().run(),
      },
      {
        label: "Межстрочный интервал…",
        edit: true,
        action: () =>
          prompt("Межстрочный интервал", "Множитель", "1.15", (value) => {
            const lineHeight = Number(value.replace(",", "."));
            if (!(lineHeight >= 0.5 && lineHeight <= 5))
              throw new Error("Допустимый интервал от 0,5 до 5.");
            paragraphStyle({ lineHeight: String(lineHeight) });
          }),
      },
      {
        label: "Интервалы перед / после абзаца…",
        edit: true,
        action: () =>
          prompt("Интервалы абзаца", "Перед / после, пт", "0 / 8", (value) => {
            const parts = value.match(/\d+(?:[.,]\d+)?/g);
            if (!parts || parts.length !== 2)
              throw new Error("Укажите два интервала в пунктах.");
            paragraphStyle({
              spacingBefore: Number(parts[0].replace(",", ".")),
              spacingAfter: Number(parts[1].replace(",", ".")),
            });
          }),
      },
      { label: "Увеличить отступ", edit: true, action: () => indent(12.7) },
      { label: "Уменьшить отступ", edit: true, action: () => indent(-12.7) },
    ],
    Таблица: [
      {
        label: "Строка выше",
        edit: true,
        action: () => editor?.chain().focus().addRowBefore().run(),
      },
      {
        label: "Строка ниже",
        edit: true,
        action: () => editor?.chain().focus().addRowAfter().run(),
      },
      {
        label: "Столбец слева",
        edit: true,
        action: () => editor?.chain().focus().addColumnBefore().run(),
      },
      {
        label: "Столбец справа",
        edit: true,
        action: () => editor?.chain().focus().addColumnAfter().run(),
      },
      {
        label: "Удалить строку",
        edit: true,
        action: () => editor?.chain().focus().deleteRow().run(),
      },
      {
        label: "Удалить столбец",
        edit: true,
        action: () => editor?.chain().focus().deleteColumn().run(),
      },
      {
        label: "Объединить / разделить ячейки",
        edit: true,
        action: () => editor?.chain().focus().mergeOrSplit().run(),
      },
      {
        label: "Строка заголовков",
        edit: true,
        action: () => editor?.chain().focus().toggleHeaderRow().run(),
      },
      {
        label: "Цвет фона ячейки…",
        edit: true,
        action: () =>
          prompt("Фон ячейки", "Цвет HEX", "#e8f0fe", (value) =>
            editor
              ?.chain()
              .focus()
              .setCellAttribute("backgroundColor", value)
              .run(),
          ),
      },
      {
        label: "Цвет границ…",
        edit: true,
        action: () =>
          prompt("Границы ячейки", "Цвет HEX", "#808080", (value) =>
            editor
              ?.chain()
              .focus()
              .setCellAttribute("borderColor", value)
              .setCellAttribute("borderWidth", 1)
              .setCellAttribute("borderStyle", "solid")
              .run(),
          ),
      },
      {
        label: "Удалить таблицу",
        edit: true,
        action: () => editor?.chain().focus().deleteTable().run(),
      },
    ],
    Инструменты: [
      { label: "Найти и заменить", action: () => setSearchOpen(true) },
      {
        label: "Количество слов и знаков",
        action: () => {
          const text = editor?.getText() || "";
          prompt(
            "Количество слов и знаков",
            "Статистика документа",
            `Слов ${text.match(/\p{L}[\p{L}\p{N}]*/gu)?.length || 0}, знаков ${text.length}`,
            () => {},
          );
        },
      },
      {
        label: "Проверка орфографии браузером",
        action: () => {
          const element = editor?.view.dom;
          if (element) {
            element.spellcheck = !element.spellcheck;
            rerender((value) => value + 1);
          }
        },
      },
    ],
  };
  const font = editor?.getAttributes("textStyle").fontFamily || "Arial";
  const fontSize = String(
    editor?.getAttributes("textStyle").fontSize || "11pt",
  ).replace(/[^\d.]/g, "");
  const disabled = !ready || locked;
  const dimensions =
    settings.pageSize === "Letter" ? [215.9, 279.4] : [210, 297];
  const pageWidth =
    settings.orientation === "landscape" ? dimensions[1] : dimensions[0];
  const pageHeight =
    settings.orientation === "landscape" ? dimensions[0] : dimensions[1];
  const toolbarButton = (
    label: string,
    icon: React.ReactNode,
    action: () => unknown,
    active = false,
    readOnly = false,
  ) => (
    <button
      key={label}
      className={active ? "active" : ""}
      aria-label={label}
      title={label}
      disabled={!ready || (locked && !readOnly)}
      onClick={() => run(action)}
    >
      {icon}
    </button>
  );

  return (
    <div
      className="rich-document-editor"
      aria-label="Редактор документа"
      onKeyDownCapture={(event) => {
        if (!(event.metaKey || event.ctrlKey)) return;
        if (event.key.toLowerCase() === "s") {
          event.preventDefault();
          event.stopPropagation();
          void flush();
        }
        if (event.key.toLowerCase() === "p") {
          event.preventDefault();
          event.stopPropagation();
          printDocument();
        }
        if (
          event.key.toLowerCase() === "h" ||
          event.key.toLowerCase() === "f"
        ) {
          event.preventDefault();
          event.stopPropagation();
          setSearchOpen(true);
        }
        if (event.key.toLowerCase() === "k" && !locked) {
          event.preventDefault();
          event.stopPropagation();
          link();
        }
        if (["+", "=", "-", "0"].includes(event.key)) {
          event.preventDefault();
          event.stopPropagation();
          scaleAt(
            event.key === "0"
              ? 100
              : zoomRef.current * Math.exp(event.key === "-" ? -0.1 : 0.1),
          );
        }
      }}
    >
      <header className="rich-document-titlebar">
        <FileText size={31} className="rich-file-icon" />
        <div className="rich-document-heading">
          <strong title={source.name}>
            {source.name.replace(/\.docx$/i, "")}
          </strong>
          <nav aria-label="Меню документа">
            {Object.entries(menus)
              .filter(
                ([name]) => name !== "Таблица" || editor?.isActive("table"),
              )
              .map(([name, items]) => (
                <div className="rich-menu" key={name}>
                  <button
                    aria-expanded={menu === name}
                    onClick={() => setMenu(menu === name ? undefined : name)}
                  >
                    {name}
                  </button>
                  {menu === name && (
                    <div role="menu">
                      {items.map((item) => (
                        <button
                          key={item.label}
                          role="menuitem"
                          disabled={!ready || Boolean(locked && item.edit)}
                          onClick={() => run(item.action)}
                        >
                          {item.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
          </nav>
        </div>
        <span className="rich-save-state" role="status">
          <Check size={14} />
          {saveState}
        </span>
        <button
          className="rich-lock"
          aria-label={
            locked
              ? "Разрешить редактирование документа"
              : "Заблокировать редактирование документа"
          }
          disabled={
            !ready || !props.onWorkbookChange || Boolean(props.onUseNumber)
          }
          onClick={() => {
            editor?.setEditable(locked);
            setLocked(!locked);
          }}
        >
          {locked ? <Lock size={15} /> : <LockOpen size={15} />}
          {locked ? "Просмотр" : "Редактирование"}
        </button>
      </header>
      <div
        className="rich-document-toolbar"
        role="toolbar"
        aria-label="Форматирование документа"
      >
        {toolbarButton(
          "Найти",
          <Search size={16} />,
          () => setSearchOpen(true),
          false,
          true,
        )}
        {toolbarButton("Отменить", <Undo2 size={16} />, () =>
          editor?.chain().focus().undo().run(),
        )}
        {toolbarButton("Повторить", <Redo2 size={16} />, () =>
          editor?.chain().focus().redo().run(),
        )}
        {toolbarButton(
          "Печать",
          <Printer size={16} />,
          printDocument,
          false,
          true,
        )}
        {toolbarButton(
          "Очистить форматирование",
          <Paintbrush size={16} />,
          () => editor?.chain().focus().unsetAllMarks().clearNodes().run(),
        )}
        <label className="rich-zoom-field">
          <input
            aria-label="Масштаб документа в процентах"
            type="number"
            min="25"
            max="400"
            step="0.1"
            value={Number(zoom.toFixed(1))}
            onChange={(event) => scaleAt(Number(event.target.value))}
          />
          %<ChevronDown size={12} />
        </label>
        <span className="rich-toolbar-divider" />
        <select
          aria-label="Стиль абзаца"
          disabled={disabled}
          value={
            editor?.isActive("heading")
              ? String(editor.getAttributes("heading").level)
              : "0"
          }
          onChange={(event) => {
            const level = Number(event.target.value);
            if (level)
              editor
                ?.chain()
                .focus()
                .setHeading({ level: level as 1 | 2 | 3 | 4 | 5 | 6 })
                .run();
            else editor?.chain().focus().setParagraph().run();
          }}
        >
          <option value="0">Обычный текст</option>
          {[1, 2, 3, 4, 5, 6].map((level) => (
            <option value={level} key={level}>
              Заголовок {level}
            </option>
          ))}
        </select>
        <select
          aria-label="Шрифт документа"
          disabled={disabled}
          value={font}
          onChange={(event) =>
            editor?.chain().focus().setFontFamily(event.target.value).run()
          }
        >
          {Array.from(
            new Set([
              font,
              "Arial",
              "Times New Roman",
              "Calibri",
              "Georgia",
              "Verdana",
              "Courier New",
              "Inter",
              "Roboto",
            ]),
          ).map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
        <input
          key={fontSize}
          className="rich-font-size"
          aria-label="Размер шрифта документа"
          type="number"
          min="1"
          max="400"
          step="0.5"
          defaultValue={fontSize}
          disabled={disabled}
          onBlur={(event) => {
            const size = Number(event.target.value);
            if (size >= 1 && size <= 400)
              editor?.chain().focus().setFontSize(`${size}pt`).run();
            else event.currentTarget.value = fontSize;
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              event.currentTarget.blur();
            }
          }}
        />
        {toolbarButton(
          "Полужирный",
          <Bold size={16} />,
          () => editor?.chain().focus().toggleBold().run(),
          editor?.isActive("bold"),
        )}
        {toolbarButton(
          "Курсив",
          <Italic size={16} />,
          () => editor?.chain().focus().toggleItalic().run(),
          editor?.isActive("italic"),
        )}
        {toolbarButton(
          "Подчеркнутый",
          <Underline size={16} />,
          () => editor?.chain().focus().toggleUnderline().run(),
          editor?.isActive("underline"),
        )}
        {toolbarButton(
          "Зачеркнутый",
          <Strikethrough size={16} />,
          () => editor?.chain().focus().toggleStrike().run(),
          editor?.isActive("strike"),
        )}
        <label className="rich-color-input" title="Цвет текста">
          <span>A</span>
          <input
            aria-label="Цвет текста документа"
            type="color"
            disabled={disabled}
            value={editor?.getAttributes("textStyle").color || "#202124"}
            onChange={(event) =>
              editor?.chain().focus().setColor(event.target.value).run()
            }
          />
        </label>
        <label className="rich-color-input" title="Цвет выделения">
          <Highlighter size={16} />
          <input
            aria-label="Цвет выделения документа"
            type="color"
            disabled={disabled}
            value={editor?.getAttributes("highlight").color || "#fff2a8"}
            onChange={(event) =>
              editor
                ?.chain()
                .focus()
                .toggleHighlight({ color: event.target.value })
                .run()
            }
          />
        </label>
        <span className="rich-toolbar-divider" />
        {toolbarButton(
          "Вставить ссылку",
          <span className="rich-link-icon">∞</span>,
          link,
        )}
        {toolbarButton("Вставить изображение", <ImagePlus size={16} />, () =>
          fileInput.current?.click(),
        )}
        {toolbarButton(
          "Выровнять слева",
          <AlignLeft size={16} />,
          () => editor?.chain().focus().setTextAlign("left").run(),
          editor?.isActive({ textAlign: "left" }),
        )}
        {toolbarButton(
          "По центру",
          <AlignCenter size={16} />,
          () => editor?.chain().focus().setTextAlign("center").run(),
          editor?.isActive({ textAlign: "center" }),
        )}
        {toolbarButton(
          "Выровнять справа",
          <AlignRight size={16} />,
          () => editor?.chain().focus().setTextAlign("right").run(),
          editor?.isActive({ textAlign: "right" }),
        )}
        {toolbarButton(
          "По ширине",
          <AlignJustify size={16} />,
          () => editor?.chain().focus().setTextAlign("justify").run(),
          editor?.isActive({ textAlign: "justify" }),
        )}
        {toolbarButton(
          "Маркированный список",
          <List size={16} />,
          () => editor?.chain().focus().toggleBulletList().run(),
          editor?.isActive("bulletList"),
        )}
        {toolbarButton(
          "Нумерованный список",
          <ListOrdered size={16} />,
          () => editor?.chain().focus().toggleOrderedList().run(),
          editor?.isActive("orderedList"),
        )}
        {toolbarButton(
          "Список задач",
          <Check size={16} />,
          () => editor?.chain().focus().toggleTaskList().run(),
          editor?.isActive("taskList"),
        )}
        {toolbarButton("Уменьшить отступ", <IndentDecrease size={16} />, () =>
          indent(-12.7),
        )}
        {toolbarButton("Увеличить отступ", <IndentIncrease size={16} />, () =>
          indent(12.7),
        )}
        {toolbarButton("Вставить таблицу", <Table2 size={16} />, () =>
          editor
            ?.chain()
            .focus()
            .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
            .run(),
        )}
      </div>
      {(props.annotations?.length || 0) > 0 && (
        <div
          className="rich-document-bindings"
          aria-label="Переменные документа"
        >
          {props.annotations?.map((item, index) => (
            <span key={index} title={item.evidence.locator}>
              {item.label}
            </span>
          ))}
        </div>
      )}
      {warnings.length > 0 && (
        <details className="rich-document-import-warnings">
          <summary>Особенности исходного документа · {warnings.length}</summary>
          <ul>
            {warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </details>
      )}
      {error && (
        <div className="rich-document-error" role="alert">
          <span>{error}</span>
          <button onClick={() => void flush()}>Сохранить</button>
          <button onClick={() => setError("")}>×</button>
        </div>
      )}
      {searchOpen && (
        <div className="rich-document-search">
          <input
            autoFocus
            aria-label="Найти в документе"
            placeholder="Найти в документе"
            value={search.query}
            onChange={(event) => {
              setSearch({ ...search, query: event.target.value });
              setSearchIndex(0);
            }}
          />
          <span>{matches.length} совпадений</span>
          <button onClick={() => navigateMatch(-1)}>↑</button>
          <button onClick={() => navigateMatch(1)}>↓</button>
          <label>
            <input
              type="checkbox"
              checked={search.caseSensitive}
              onChange={(event) =>
                setSearch({ ...search, caseSensitive: event.target.checked })
              }
            />
            Учитывать регистр
          </label>
          <input
            aria-label="Заменить на"
            placeholder="Заменить на"
            value={replacement}
            disabled={locked}
            onChange={(event) => setReplacement(event.target.value)}
          />
          <button
            disabled={locked || !matches.length}
            onClick={() => replace(false)}
          >
            Заменить
          </button>
          <button
            disabled={locked || !matches.length}
            onClick={() => replace(true)}
          >
            Заменить все
          </button>
          <button
            aria-label="Закрыть поиск"
            onClick={() => {
              setSearchOpen(false);
              setSearch({ ...search, query: "" });
            }}
          >
            ×
          </button>
        </div>
      )}
      <div className="rich-document-workspace">
        {outline && (
          <aside className="rich-document-outline">
            <h2>Структура документа</h2>
            {headings.map((heading, index) => (
              <button
                key={index}
                style={{ paddingLeft: 12 + (heading.level - 1) * 10 }}
                onClick={() =>
                  editor
                    ?.chain()
                    .focus()
                    .setTextSelection(heading.position + 1)
                    .scrollIntoView()
                    .run()
                }
              >
                {heading.text || "Заголовок"}
              </button>
            ))}
            {!headings.length && <p>Заголовки документа появятся здесь.</p>}
          </aside>
        )}
        <div
          className="rich-document-viewport"
          ref={viewport}
          onScroll={() => {
            setPick(undefined);
            rememberCamera();
          }}
          onMouseUp={readSelection}
        >
          <div className="rich-document-paper-space">
            <div
              className="rich-ruler"
              aria-hidden="true"
              style={{ width: (pageWidth * 3.7795 * zoom) / 100 }}
            >
              {Array.from(
                { length: Math.floor(pageWidth / 10) },
                (_, index) => (
                  <span key={index}>{index + 1}</span>
                ),
              )}
            </div>
            <div
              className="rich-document-paper-scale"
              style={{
                width: (pageWidth * 3.7795 * zoom) / 100,
                height: (paperHeight * zoom) / 100,
              }}
            >
              <div
                ref={paper}
                className={`rich-document-paper ${settings.viewMode === "pageless" ? "pageless" : ""}`}
                style={{
                  width: `${pageWidth}mm`,
                  minHeight:
                    settings.viewMode === "pageless"
                      ? "450px"
                      : `${pageHeight}mm`,
                  padding: `${settings.margins.top}mm ${settings.margins.right}mm ${settings.margins.bottom}mm ${settings.margins.left}mm`,
                  transform: `scale(${zoom / 100})`,
                }}
              >
                {settings.header && (
                  <div className="rich-paper-header">{settings.header}</div>
                )}
                <EditorContent editor={editor} />
                {settings.footer && (
                  <div className="rich-paper-footer">{settings.footer}</div>
                )}
                {settings.footerPageNumber && (
                  <div className="rich-page-number">1</div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="rich-document-status">
        <span>
          {editor?.getText().match(/\p{L}[\p{L}\p{N}]*/gu)?.length || 0} слов
        </span>
        <label>
          Масштаб
          <input
            aria-label="Плавный масштаб документа"
            type="range"
            min="25"
            max="400"
            step="0.1"
            value={zoom}
            onChange={(event) => scaleAt(Number(event.target.value))}
          />
          {zoom.toFixed(zoom % 1 ? 1 : 0)}%
        </label>
      </div>
      {props.overlay && (
        <div className="rich-audit-overlay">{props.overlay}</div>
      )}
      {!ready && !error && (
        <div className="rich-document-loading">Открываю документ…</div>
      )}
      <input
        ref={fileInput}
        type="file"
        hidden
        accept="image/*"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) insertImage(file);
          event.target.value = "";
        }}
      />
      {pick && props.onUseNumber && (
        <div
          className="number-selection-popover rich-number-pick"
          style={{ left: pick.x, top: pick.y }}
        >
          <button
            onClick={() => {
              props.onUseNumber?.({
                document_id: source.id,
                value: pick.value,
                evidence: pick.evidence,
              });
              setPick(undefined);
            }}
          >
            Использовать как переменную · {pick.value}
          </button>
        </div>
      )}
      {dialog && (
        <div className="rich-dialog-backdrop">
          <form
            className="rich-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={dialog.title}
            onSubmit={(event) => {
              event.preventDefault();
              run(() => dialog.apply(dialog.value));
              setDialog(undefined);
            }}
          >
            <h2>{dialog.title}</h2>
            <label>
              {dialog.label}
              <input
                autoFocus
                aria-label={dialog.label}
                value={dialog.value}
                onChange={(event) =>
                  setDialog({ ...dialog, value: event.target.value })
                }
              />
            </label>
            <footer>
              <button type="button" onClick={() => setDialog(undefined)}>
                Отмена
              </button>
              <button type="submit">Применить</button>
            </footer>
          </form>
        </div>
      )}
      {pageDialog && (
        <div className="rich-dialog-backdrop">
          <form
            className="rich-dialog rich-page-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Параметры страницы"
            onSubmit={(event) => {
              event.preventDefault();
              updateSettings(pageDraft);
              setPageDialog(false);
            }}
          >
            <h2>Параметры страницы</h2>
            <label>
              Формат бумаги
              <select
                aria-label="Формат бумаги"
                value={pageDraft.pageSize}
                onChange={(event) =>
                  setPageDraft({
                    ...pageDraft,
                    pageSize: event.target.value as "A4" | "Letter",
                  })
                }
              >
                <option>A4</option>
                <option>Letter</option>
              </select>
            </label>
            <label>
              Ориентация
              <select
                aria-label="Ориентация страницы"
                value={pageDraft.orientation}
                onChange={(event) =>
                  setPageDraft({
                    ...pageDraft,
                    orientation: event.target.value as "portrait" | "landscape",
                  })
                }
              >
                <option value="portrait">Книжная</option>
                <option value="landscape">Альбомная</option>
              </select>
            </label>
            <div className="rich-page-margins">
              {(
                [
                  ["top", "Сверху"],
                  ["bottom", "Снизу"],
                  ["left", "Слева"],
                  ["right", "Справа"],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  {label}, мм
                  <input
                    type="number"
                    min="0"
                    max="70"
                    step="any"
                    aria-label={`Поле ${label}`}
                    value={pageDraft.margins[key]}
                    onChange={(event) =>
                      setPageDraft({
                        ...pageDraft,
                        margins: {
                          ...pageDraft.margins,
                          [key]: Number(event.target.value),
                        },
                      })
                    }
                  />
                </label>
              ))}
            </div>
            <label>
              Верхний колонтитул
              <input
                aria-label="Верхний колонтитул"
                value={pageDraft.header || ""}
                onChange={(event) =>
                  setPageDraft({ ...pageDraft, header: event.target.value })
                }
              />
            </label>
            <label>
              Нижний колонтитул
              <input
                aria-label="Нижний колонтитул"
                value={pageDraft.footer || ""}
                onChange={(event) =>
                  setPageDraft({ ...pageDraft, footer: event.target.value })
                }
              />
            </label>
            <label className="rich-page-number-control">
              <input
                type="checkbox"
                checked={Boolean(pageDraft.footerPageNumber)}
                onChange={(event) =>
                  setPageDraft({
                    ...pageDraft,
                    footerPageNumber: event.target.checked,
                  })
                }
              />
              Номер страницы внизу
            </label>
            <footer>
              <button type="button" onClick={() => setPageDialog(false)}>
                Отмена
              </button>
              <button type="submit">Применить</button>
            </footer>
          </form>
        </div>
      )}
    </div>
  );
}
