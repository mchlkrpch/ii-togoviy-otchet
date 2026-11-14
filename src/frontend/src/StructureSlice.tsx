import type { MouseEvent } from "react";
import { Scissors } from "lucide-react";
import type { Box } from "./DocumentStructure";
import "./structure-slice.css";

export type SliceCuts = { xCuts: number[]; yCuts: number[] };

export function slicePieces(box: Box, { xCuts, yCuts }: SliceCuts): Box[] {
  const xs = [box[0], ...xCuts, box[2]];
  const ys = [box[1], ...yCuts, box[3]];
  return ys.slice(0, -1).flatMap((y, row) =>
    xs.slice(0, -1).map((x, column): Box => [x, y, xs[column + 1], ys[row + 1]]),
  );
}

type Cut = { axis: keyof SliceCuts; at: number; tolerance: number };

export function sliceCutAt(event: MouseEvent<SVGSVGElement>, box: Box, size: [number, number]): Cut | undefined {
  const bounds = event.currentTarget.getBoundingClientRect();
  const scaleX = bounds.width / size[0];
  const scaleY = bounds.height / size[1];
  if (!scaleX || !scaleY) return;
  const x = (event.clientX - bounds.left) / scaleX;
  const y = (event.clientY - bounds.top) / scaleY;
  const [left, top, right, bottom] = box;
  const edges = [
    { axis: "xCuts" as const, distance: Math.min(Math.abs(y - top), Math.abs(y - bottom)) * scaleY, at: x, min: left, max: right, scale: scaleX },
    { axis: "yCuts" as const, distance: Math.min(Math.abs(x - left), Math.abs(x - right)) * scaleX, at: y, min: top, max: bottom, scale: scaleY },
  ].sort((a, b) => a.distance - b.distance);
  const edge = edges.find(({ at, min, max, distance }) => distance <= 10 && at > min && at < max);
  if (edge) return { axis: edge.axis, at: edge.at, tolerance: 5 / edge.scale };
}

export function SliceOverlay({
  box, size, cuts, pickingParent, saving, onCut, onChoose,
}: {
  box: Box;
  size: [number, number];
  cuts: SliceCuts;
  pickingParent: boolean;
  saving: boolean;
  onCut: (cut: Cut) => void;
  onChoose: (index: number) => void;
}) {
  const [left, top, right, bottom] = box;
  const pieces = pickingParent ? slicePieces(box, cuts) : [];
  return (
    <svg
      className={`structure-slice-overlay ${pickingParent ? "picking-parent" : "placing-cuts"}`}
      viewBox={`0 0 ${size.join(" ")}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      onClick={(event) => {
        event.stopPropagation();
        if (pickingParent || saving) return;
        const cut = sliceCutAt(event, box, size);
        if (cut) onCut(cut);
      }}
      onDoubleClick={(event) => event.stopPropagation()}
    >
      <rect className="slice-target" x={left} y={top} width={right - left} height={bottom - top} vectorEffect="non-scaling-stroke" />
      {pieces.map(([x, y, x2, y2], index) => (
        <g key={index}>
          <rect className="slice-piece" x={x} y={y} width={x2 - x} height={y2 - y}
            vectorEffect="non-scaling-stroke" data-slice-piece={index}
            onClick={(event) => {
              event.stopPropagation();
              if (!saving) onChoose(index);
            }}
          />
          <text className="slice-piece-number" x={(x + x2) / 2} y={(y + y2) / 2}
            fontSize={Math.min(16, (x2 - x) / 2, (y2 - y) * 0.6)}>{index + 1}</text>
        </g>
      ))}
      {cuts.xCuts.map((x) => <line key={x} className="slice-guide" x1={x} x2={x} y1={top} y2={bottom} vectorEffect="non-scaling-stroke" />)}
      {cuts.yCuts.map((y) => <line key={y} className="slice-guide" y1={y} y2={y} x1={left} x2={right} vectorEffect="non-scaling-stroke" />)}
    </svg>
  );
}

export function SliceControls({
  active, pickingParent, saving, error, count, disabled, onStart, onNext, onCancel, onChoose,
}: {
  active: boolean;
  pickingParent: boolean;
  saving: boolean;
  error: string;
  count: number;
  disabled: boolean;
  onStart: () => void;
  onNext: () => void;
  onCancel: () => void;
  onChoose: (index: number) => void;
}) {
  return (
    <div className="structure-slice-controls" role="group" aria-label="Разбивка поля">
      <button type="button" className="structure-action slice-start" aria-pressed={active} disabled={disabled || saving}
        title={disabled ? "Включите режим «Изменение»" : "Разделить прямоугольник"}
        onClick={active ? onCancel : onStart}
      >
        <Scissors size={14} aria-hidden="true" /> Slice
      </button>
      {active && (
        <>
          <span role={error ? "alert" : "status"}>
            {error || (saving ? "Сохраняю разбиение…" : pickingParent
              ? "Выберите главный прямоугольник. Остальные станут его дочерними полями."
              : "Нажимайте на границы поля, чтобы добавить или убрать линии. Нажмите Enter, чтобы продолжить.")}
          </span>
          {pickingParent ? (
            <select className="structure-action" aria-label="Главный прямоугольник" value="" disabled={saving}
              onChange={(event) => {
                if (event.target.value !== "") onChoose(Number(event.target.value));
              }}
            >
              <option value="" disabled>Родительское поле</option>
              {Array.from({ length: count }, (_, index) => (
                <option key={index} value={index}>Часть {index + 1}</option>
              ))}
            </select>
          ) : (
            <button type="button" className="structure-action" onClick={onNext} disabled={count < 2}>
              Далее
            </button>
          )}
          <button type="button" className="structure-action" onClick={onCancel} disabled={saving}>Отмена</button>
        </>
      )}
    </div>
  );
}
