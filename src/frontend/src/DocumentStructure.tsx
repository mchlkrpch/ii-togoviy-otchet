import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import type { CSSProperties, FormEvent } from "react";
import { request, RequestError } from "./api";
import "./document-structure.css";

export type Box = [number, number, number, number];
export type StructureNode = {
  id: string;
  parent: string | null;
  children: string[];
  kind: "table" | "metadata" | "header" | "value" | "key_value" | "text";
  label: string;
  raw?: string;
  key?: string;
  value?: string;
  value_type?: string;
  bbox: Box;
  field_bbox?: Box;
  key_bbox?: Box;
  value_bbox?: Box;
  column?: number;
  column_span?: number;
  color: string;
  row_path?: string[];
  column_path?: string[];
  needs_review?: boolean;
};
export type StructurePage = {
  number: number;
  size: [number, number];
  nodes: StructureNode[];
  roots: string[];
  row_roots?: { children: string[] }[];
};
export type StructureFocus = { id: string; part?: "key" | "value" };
type Structure = { version: number; model: { pages: StructurePage[] } };

const nodeLabel = (node: StructureNode) =>
  node.kind === "key_value"
    ? node.label.trimEnd().replace(/:$/, "") + ": " + (node.value ?? "")
    : node.kind === "value"
      ? node.raw || node.label || "Пустая ячейка"
      : node.kind === "metadata"
        ? node.label.replace("ключ → значение", "ключ: значение")
        : node.label;
const fieldText = (node: StructureNode) =>
  node.kind === "key_value" ? (node.value ?? "") : (node.raw ?? node.label);
const normalized = (value: string) =>
  value.normalize("NFKC").toLocaleLowerCase("ru").replaceAll("ё", "е").trim();

export function useDocumentStructure(url: string, enabled: boolean, pageNumber: number) {
  const [structure, setStructure] = useState<Structure>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled || structure) return;
    const controller = new AbortController();
    setError("");
    request<Structure>(`${url}/structure`, { signal: controller.signal }).then(
      (data) => { if (!controller.signal.aborted) setStructure(data); },
      (err: Error) => {
        if (controller.signal.aborted) return;
        const oldServer = err instanceof RequestError && err.status === 404 && err.message === "Not Found";
        setError(oldServer ? "Карта полей недоступна в этой версии сервера. Перезапустите приложение." : err.message);
      },
    );
    return () => controller.abort();
  }, [enabled, structure, url, attempt]);

  const page = structure?.model.pages.find((entry) => entry.number === pageNumber);
  async function saveChange(node: StructureNode, change: {
    text: string;
  } | { action: "slice"; x_cuts: number[]; y_cuts: number[]; parent_index: number }) {
    if (!structure || !page) return;
    const response = await fetch(`${url}/structure`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        version: structure.version, page: page.number, node_id: node.id, ...change,
      }),
    });
    if (response.status === 409) {
      // Refresh the version and field labels, preserving the unsaved draft.
      setStructure(await request<Structure>(`${url}/structure`));
      throw new Error("Структура изменилась в другой вкладке. Проверьте текст и повторите сохранение.");
    }
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(typeof body?.detail === "string" ? body.detail : "Не удалось сохранить поле");
    }
    setStructure(await response.json() as Structure);
  }
  return {
    page, error,
    save: (node: StructureNode, text: string) => saveChange(node, { text: text.trim() }),
    slice: (node: StructureNode, x_cuts: number[], y_cuts: number[], parent_index: number) =>
      saveChange(node, { action: "slice", x_cuts, y_cuts, parent_index }),
    loading: enabled && !structure && !error,
    retry: () => setAttempt((value) => value + 1),
  };
}

export function structureNodeAt(page: StructurePage, x: number, y: number) {
  const [width, height] = page.size;
  const px = width * x, py = height * y;
  let result: StructureNode | undefined, area = Infinity;
  for (const node of page.nodes) {
    const b = node.field_bbox;
    if (!b || px < b[0] || px > b[2] || py < b[1] || py > b[3]) continue;
    const size = (b[2] - b[0]) * (b[3] - b[1]);
    if (size > 0 && size <= area) {
      result = node;
      area = size;
    }
  }
  return result;
}

function tableAxes(page: StructurePage, active: StructureNode | undefined, nodes: Map<string, StructureNode>) {
  const rows = new Set<string>();
  const columns = new Set<string>();
  const collect = (ids: Set<string>, id: string) => {
    if (ids.has(id)) return;
    ids.add(id);
    for (const childId of nodes.get(id)?.children ?? []) collect(ids, childId);
  };
  // Generated Slice fields inherit their table coordinates from the original cell.
  let cell = active;
  while (cell && cell.column === undefined)
    cell = cell.parent ? nodes.get(cell.parent) : undefined;
  if (cell?.kind !== "value" || cell.column === undefined) return { rows, columns };

  let table = cell;
  while (table.parent) {
    const parent = nodes.get(table.parent);
    if (!parent) break;
    table = parent;
  }
  if (table.kind !== "table") return { rows, columns };
  const tableFields = new Set<string>();
  collect(tableFields, table.id);
  const matchingRows = page.row_roots?.filter((row) => row.children.includes(cell.id)) ?? [];
  for (const row of matchingRows) {
    for (const id of row.children) {
      if (tableFields.has(id)) collect(rows, id);
    }
  }

  const cellBox = cell.field_bbox ?? cell.bbox;
  const start = cell.column;
  const end = start + (cell.column_span ?? 1);
  for (const id of tableFields) {
    const candidate = nodes.get(id);
    if (!candidate || candidate.column === undefined) continue;
    if (candidate.column < end && candidate.column + (candidate.column_span ?? 1) > start) {
      if (candidate.kind === "header") columns.add(id);
      else collect(columns, id);
    }
    if (!matchingRows.length && candidate.kind === "value") {
      // Some native tables omit nonnumeric rows from the projection.
      const bounds = candidate.field_bbox ?? candidate.bbox;
      if (Math.min(cellBox[3], bounds[3]) - Math.max(cellBox[1], bounds[1]) > 0.01)
        collect(rows, id);
    }
  }
  return { rows, columns };
}

export function StructureRegions({
  page, selected, focus,
}: {
  page: StructurePage;
  selected?: StructureNode;
  focus?: StructureFocus;
}) {
  const groupMask = useId();
  if (!page.size[0] || !page.size[1]) return null;
  const nodes = new Map(page.nodes.map((node) => [node.id, node]));
  const focused = focus && nodes.get(focus.id);
  const active = focused || selected;
  const activePart = focused?.kind === "key_value" ? focus?.part : undefined;
  const activePartBox = activePart === "key" ? active?.key_bbox : activePart === "value" ? active?.value_bbox : undefined;
  const { rows, columns } = tableAxes(page, active, nodes);
  const depths = new Map<string, number>();
  const queue = active ? [active.id] : [];
  if (active) depths.set(active.id, 0);
  for (const id of queue) {
    if (activePart) break;
    for (const child of nodes.get(id)?.children ?? []) {
      if (depths.has(child)) continue;
      depths.set(child, depths.get(id)! + 1);
      queue.push(child);
    }
  }
  function region(node: StructureNode, box: Box, name: string, part?: StructureFocus["part"]) {
    const depth = activePart && part !== activePart ? undefined : depths.get(node.id);
    const axis = rows.has(node.id) ? (columns.has(node.id) ? "both" : "row")
      : columns.has(node.id) ? "column" : undefined;
    const highlight = depth === 0 ? "chosen" : depth !== undefined ? "related" : axis ? "axis-related" : "";
    return (
      <rect
        key={node.id + name}
        x={box[0]} y={box[1]} width={Math.max(0, box[2] - box[0])} height={Math.max(0, box[3] - box[1])}
        vectorEffect="non-scaling-stroke"
        data-structure-node={node.id}
        data-structure-axis={axis}
        data-structure-part={part}
        mask={name === "structure-group-fill" ? `url(#${groupMask})` : undefined}
        className={`${name} ${highlight}`}
        style={{
          "--field-color": node.color,
          ...(name !== "structure-outline" && depth !== undefined && { fillOpacity: 0.16 / (2 ** depth) }),
          ...(name === "structure-branch" && highlight && { fillOpacity: 0 }),
        } as CSSProperties}
      />
    );
  }
  return (
    <svg className="structure-regions" aria-hidden="true" viewBox={`0 0 ${page.size.join(" ")}`} preserveAspectRatio="none">
      {active && !active.field_bbox && (
        <>
          <defs>
            <mask id={groupMask} maskUnits="userSpaceOnUse" x={0} y={0} width={page.size[0]} height={page.size[1]}>
              <rect width={page.size[0]} height={page.size[1]} style={{ fill: "white", fillOpacity: 1, stroke: "none" }} />
              {page.nodes.filter((node) => depths.has(node.id) && node.field_bbox).map((node) => {
                const [x, y, right, bottom] = node.field_bbox!;
                return <rect key={node.id} x={x} y={y} width={right - x} height={bottom - y}
                  style={{ fill: "black", fillOpacity: 1, stroke: "none" }} />;
              })}
            </mask>
          </defs>
          {region(active, active.bbox, "structure-group-fill")}
        </>
      )}
      {page.nodes.filter((node) => node.kind === "table" || (node.kind === "header" && nodes.get(node.parent ?? "")?.kind === "table"))
        .map((node) => region(node, node.bbox, "structure-branch"))}
      {page.nodes.flatMap((node) => {
        if (node.kind === "key_value" && (node.key_bbox || node.value_bbox)) {
          return [
            node.key_bbox && region(node, node.key_bbox, "structure-key", "key"),
            node.value_bbox && region(node, node.value_bbox, "structure-field", "value"),
          ];
        }
        return node.field_bbox ? [region(node, node.field_bbox, "structure-field")] : [];
      })}
      {selected && region(selected,
        (selected.id === active?.id && activePartBox) || selected.field_bbox || selected.bbox, "structure-outline")}
    </svg>
  );
}

export function StructureTree({
  page, selected, focus, onFocusField, onSelect, onClose, loading, error, retry,
}: {
  page?: StructurePage;
  selected?: string;
  focus?: StructureFocus;
  onFocusField: (focus?: StructureFocus) => void;
  onSelect: (node: StructureNode) => void;
  onClose: () => void;
  loading: boolean;
  error: string;
  retry: () => void;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const pointerFocus = useRef<StructureFocus | undefined>(undefined);
  const keyboardFocus = useRef<StructureFocus | undefined>(undefined);
  const [query, setQuery] = useState("");
  const [expansion, setExpansion] = useState<Record<string, boolean>>({});
  const nodes = useMemo(() => new Map(page?.nodes.map((node) => [node.id, node])), [page]);
  const tokens = normalized(query).split(/\s+/).filter(Boolean);
  const parentIds = new Set<string>();
  for (let id = selected && nodes.get(selected)?.parent; id && !parentIds.has(id); id = nodes.get(id)?.parent) {
    parentIds.add(id);
  }
  const matches = new Set<string>();
  if (tokens.length) {
    for (const node of nodes.values()) {
      const text = normalized([nodeLabel(node), node.label, node.raw, node.key, ...(node.row_path ?? []), ...(node.column_path ?? [])].join(" "));
      if (!tokens.every((token) => text.includes(token))) continue;
      for (let id: string | null | undefined = node.id; id && !matches.has(id); id = nodes.get(id)?.parent) {
        matches.add(id);
      }
    }
  }

  function previewPointer(next?: StructureFocus) {
    const previous = pointerFocus.current;
    if (previous?.id === next?.id && previous?.part === next?.part) return;
    pointerFocus.current = next;
    onFocusField(next ?? keyboardFocus.current);
  }

  function previewKeyboard(next?: StructureFocus) {
    keyboardFocus.current = next;
    onFocusField(next ?? pointerFocus.current);
  }

  function branch(id: string, depth = 0) {
    const node = nodes.get(id);
    if (!node || (tokens.length && !matches.has(id))) return null;
    const isOpen = !!tokens.length || (expansion[id] ?? (depth === 0 || parentIds.has(id)));
    const pair = node.kind === "key_value";
    return (
      <li key={id}>
        <div
          className={`structure-tree-row${selected === id ? " selected" : ""}${focus?.id === id ? " previewed" : ""}`}
          onPointerOver={(event) => {
            if (event.pointerType === "touch" || !(event.target instanceof Element)) return;
            const part = event.target.closest<HTMLElement>("[data-structure-part]")?.dataset.structurePart;
            previewPointer({ id, part: part === "key" || part === "value" ? part : undefined });
          }}
          onPointerLeave={() => previewPointer()}
          onFocus={(event) => {
            if (event.target.matches(":focus-visible")) previewKeyboard({ id });
          }}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) previewKeyboard();
          }}
        >
          {node.children.length ? (
            <button
              className="structure-branch-toggle"
              type="button"
              aria-label={`${isOpen ? "Свернуть" : "Раскрыть"}: ${nodeLabel(node)}`}
              aria-expanded={isOpen}
              onClick={() => setExpansion((previous) => ({ ...previous, [id]: !isOpen }))}
            >
              {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>
          ) : <span className="structure-branch-toggle" />}
          <button
            type="button"
            className="structure-node"
            aria-pressed={selected === id}
            data-structure-select={id}
            onKeyDown={(event) => {
              if (!pair || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
              event.preventDefault();
              const part = event.key === "ArrowLeft" ? "key" : "value";
              previewKeyboard({ id, part });
            }}
            onClick={() => {
              if (node.children.length && !isOpen)
                setExpansion((previous) => ({ ...previous, [id]: true }));
              onSelect(node);
            }}
          >
            <span className="structure-swatch" style={{ background: node.color }} aria-hidden="true" />
            <span>
              {pair ? (
                <span className="structure-key-value">
                  <span
                    data-structure-part="key"
                    className={focus?.id === id && focus.part === "key" ? "previewed" : undefined}
                  >{node.label}:</span>{" "}
                  <span
                    data-structure-part="value"
                    className={focus?.id === id && focus.part === "value" ? "previewed" : undefined}
                  >{node.value}</span>
                </span>
              ) : nodeLabel(node)}
              {node.needs_review && <span className="structure-review" title="Проверить распознавание" aria-label="Проверить распознавание"> ⚑</span>}
              {node.kind === "value" && !!node.row_path?.length && (
                <small> · {node.row_path.join(" → ")}</small>
              )}
            </span>
          </button>
        </div>
        {node.children.length > 0 && isOpen && <ul>{node.children.map((child) => branch(child, depth + 1))}</ul>}
      </li>
    );
  }
  useEffect(() => {
    if (!selected || !page) return;
    setExpansion((previous) => {
      const next = { ...previous };
      if (nodes.get(selected)?.children.length) next[selected] = true;
      const seen = new Set<string>();
      for (let parent = nodes.get(selected)?.parent; parent && !seen.has(parent); parent = nodes.get(parent)?.parent) {
        seen.add(parent);
        next[parent] = true;
      }
      return next;
    });
  }, [nodes, page, selected]);
  useEffect(() => {
    const target = scroll.current?.querySelector<HTMLElement>(".structure-tree-row.selected");
    if (!target || !scroll.current) return;
    const bounds = scroll.current.getBoundingClientRect();
    const box = target.getBoundingClientRect();
    if (box.top < bounds.top) scroll.current.scrollTop += box.top - bounds.top;
    if (box.bottom > bounds.bottom) scroll.current.scrollTop += box.bottom - bounds.bottom;
  }, [selected, expansion]);
  const tree = page?.roots.map((id) => branch(id)).filter(Boolean);
  return (
    <aside id="document-structure" className="structure-sidebar" aria-label="Структура документа">
      <header className="structure-sidebar-heading">
        <h2>Иерархия полей</h2>
        <button type="button" className="structure-close" onClick={onClose} aria-label="Скрыть структуру документа"><X size={16} /></button>
      </header>
      {!loading && !error && !!page?.roots.length && (
        <div className="structure-search">
          <input
            type="search"
            placeholder="Поле или значение…"
            aria-label="Поиск по полям"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      )}
      <div ref={scroll} className="structure-tree-scroll" role="region" tabIndex={0} aria-label="Список полей">
        {loading ? <p role="status">Распознаю поля и таблицы…</p> : error ? (
          <div role="alert"><p>{error}</p><button type="button" className="structure-action" onClick={retry}>Повторить</button></div>
        ) : page?.roots.length ? (
          tree?.length ? <ul className="structure-tree-list">{tree}</ul> : <p role="status">Поля не найдены.</p>
        ) : <p>На этой странице поля не найдены.</p>}
      </div>
    </aside>
  );
}

export function StructureEditor({
  node, save, locked, onClose, tools, slicing,
}: {
  node: StructureNode;
  save: (node: StructureNode, value: string) => Promise<void>;
  locked: boolean;
  onClose: () => void;
  tools?: React.ReactNode;
  slicing: boolean;
}) {
  const [value, setValue] = useState(fieldText(node));
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const editable = !!node.field_bbox && !locked && !slicing;
  const changed = fieldText(node) !== value;
  const context = [...(node.column_path ?? []), ...(node.row_path ?? [])];
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!editable || pending || !changed) return;
    setPending(true);
    setMessage("");
    try {
      await save(node, value);
      setValue(value.trim());
      setFailed(false);
      setMessage("Правка сохранена");
    } catch (err) {
      setFailed(true);
      setMessage(err instanceof Error ? err.message : "Не удалось сохранить поле");
    } finally {
      setPending(false);
    }
  }
  return (
    <form className="structure-editor" onSubmit={(event) => void submit(event)} aria-label="Текст поля">
      <header className="structure-heading">
        <div>
          <small>{node.field_bbox ? "Выбранное поле" : "Выбранная группа"}</small>
          <h2 title={context.length ? context.join(" → ") : node.label}>{context.length ? context.join(" → ") : node.label}</h2>
        </div>
        <button type="button" className="structure-close" onClick={onClose} aria-label="Снять выбор поля" disabled={slicing}><X size={16} /></button>
      </header>
      {node.field_bbox && !slicing && (
        <>
          <label>
            {node.kind === "key_value" ? `Значение · ${node.label}` : "Текст поля"}
            <textarea aria-label={node.kind === "key_value" ? "Значение поля" : "Текст поля"} rows={2} maxLength={20000}
              readOnly={!editable || pending} value={editable ? value : fieldText(node)}
              onChange={(event) => { setMessage(""); setValue(event.target.value); }}
            />
          </label>
          <footer>
            <span role={failed ? "alert" : "status"}>{message || (locked ? "Включите «Изменение», чтобы исправить текст." : "Правка сохраняется в разметке документа.")}</span>
            {editable && <button type="submit" className="structure-action" disabled={!changed || pending || !value.trim()}>{pending ? "Сохраняю…" : "Сохранить"}</button>}
          </footer>
        </>
      )}
      {tools && <div className="structure-editor-tools">{tools}</div>}
    </form>
  );
}
