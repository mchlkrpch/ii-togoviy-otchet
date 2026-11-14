import { useLayoutEffect, useRef } from "react";

// The annotation owns a line inside its cell. It never floats over another row.
export default function TableVariableLabel({ label, zoom }: { label: string; zoom: number }) {
  const host = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const element = host.current;
    const text = element?.firstElementChild as HTMLElement | null;
    if (!element || !text) return;
    const scale = zoom / 100;
    function fit() {
      if (!element || !text) return;
      const maximum = 11 / scale;
      text.style.fontSize = `${maximum}px`;
      const available = element.clientWidth - 8 / scale;
      if (available > 0 && text.scrollWidth > available)
        text.style.fontSize = `${Math.max(6 / scale, maximum * available / text.scrollWidth)}px`;
    }
    fit();
    const observer = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(fit);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [label, zoom]);
  return (
    <span ref={host} className="variable-cell-label table-variable-label" title={label}
      style={{ padding: `${1 / (zoom / 100)}px ${4 / (zoom / 100)}px`, fontSize: `${11 / (zoom / 100)}px` }}>
      <span>{label}</span>
    </span>
  );
}
