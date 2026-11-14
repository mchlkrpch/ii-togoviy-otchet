import { useLayoutEffect, useRef, useState } from "react";
import { loadCamera, saveCamera } from "./viewCamera";
import type { ViewCamera } from "./viewCamera";
import type { RefObject } from "react";

export function useDocumentCamera(
  key: string,
  viewport: RefObject<HTMLDivElement | null>,
  state: Omit<ViewCamera, "x" | "y" | "width">,
  image: boolean,
) {
  const initial = useRef(loadCamera(key));
  const [metrics, setMetrics] = useState({
    width: 1000,
    height: 700,
    x: 1000,
    y: 350,
  });
  const metricRef = useRef(metrics);
  metricRef.current = metrics;
  const current = useRef(state);
  current.current = state;
  const restored = useRef(false);
  function save() {
    const element = viewport.current;
    if (!element || !restored.current) return;
    const m = metricRef.current;
    saveCamera(key, {
      ...current.current,
      x: element.scrollLeft - m.x,
      y: element.scrollTop - m.y,
      width: m.width,
    });
  }
  function restore() {
    if (restored.current) return;
    const element = viewport.current;
    if (!element) return;
    const saved = initial.current,
      m = metricRef.current;
    const ratio = image && saved?.width ? m.width / saved.width : 1;
    element.scrollLeft = m.x + (saved ? saved.x * ratio : -16);
    element.scrollTop = m.y + (saved ? saved.y * ratio : -16);
    restored.current = true;
  }
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const measure = () => {
      const old = metricRef.current;
      const width = element.clientWidth || 1000,
        height = element.clientHeight || 700;
      const next = {
        width,
        height,
        x: width,
        y: Math.max(160, Math.round(height / 2)),
      };
      if (next.width === old.width && next.height === old.height) return;
      const scale = image && old.width ? width / old.width : 1;
      const left = (element.scrollLeft - old.x) * scale,
        top = (element.scrollTop - old.y) * scale;
      metricRef.current = next;
      setMetrics(next);
      window.requestAnimationFrame(() => {
        if (restored.current) {
          element.scrollLeft = next.x + left;
          element.scrollTop = next.y + top;
        }
      });
    };
    measure();
    if (!initial.current) window.requestAnimationFrame(restore);
    const observer =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(measure)
        : undefined;
    observer?.observe(element);
    return () => {
      const m = metricRef.current;
      if (restored.current)
        saveCamera(key, {
          ...current.current,
          x: element.scrollLeft - m.x,
          y: element.scrollTop - m.y,
          width: m.width,
        });
      observer?.disconnect();
    };
  }, [key]);
  return {
    metrics,
    save,
    restore: () => window.requestAnimationFrame(restore),
    metricRef,
  };
}
