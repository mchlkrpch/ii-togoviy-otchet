/** Keep an anchor in document coordinates, rather than repeatedly reading a
 * rounded browser scroll offset back into the next zoom calculation. */
export class CursorZoom {
  private anchor?: {
    u: number; v: number; x: number; y: number;
    width: number; height: number; pending: boolean;
    left: number; top: number; scrollLeft: number; scrollTop: number;
  };

  begin(viewport: HTMLElement, content: HTMLElement, previous: number,
    next: number, x: number, y: number) {
    const rect = content.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    const old = this.anchor;
    const continuous = old && (old.pending || (
      old.scrollLeft === viewport.scrollLeft && old.scrollTop === viewport.scrollTop &&
      Math.abs(old.left - rect.left) < 0.01 && Math.abs(old.top - rect.top) < 0.01 &&
      Math.abs(old.width - rect.width) < 0.01 && Math.abs(old.height - rect.height) < 0.01
    ));
    // During a wheel burst React may not have committed the preceding scale.
    // Use the pending geometry, including when the cursor moves mid-gesture.
    const width = continuous ? old.width : rect.width;
    const height = continuous ? old.height : rect.height;
    this.anchor = {
      u: continuous ? old.u + (x - old.x) / width : (x - rect.left) / width,
      v: continuous ? old.v + (y - old.y) / height : (y - rect.top) / height,
      x, y, width: width * next / previous, height: height * next / previous,
      left: rect.left, top: rect.top, scrollLeft: viewport.scrollLeft,
      scrollTop: viewport.scrollTop, pending: true,
    };
    return true;
  }

  apply(viewport: HTMLElement, content: HTMLElement) {
    const anchor = this.anchor;
    if (!anchor?.pending) return;
    const rect = content.getBoundingClientRect();
    viewport.scrollLeft += rect.left + anchor.u * rect.width - anchor.x;
    viewport.scrollTop += rect.top + anchor.v * rect.height - anchor.y;
    // scrollLeft/Top can be rounded to device pixels. Preserve the fractional
    // remainder visually as well, so it cannot accumulate on later events.
    const actual = content.getBoundingClientRect();
    const [tx = 0, ty = 0] = content.style.translate.split(" ").map(parseFloat);
    content.style.translate = `${(Number.isFinite(tx) ? tx : 0) + anchor.x - actual.left - anchor.u * actual.width}px ${(Number.isFinite(ty) ? ty : 0) + anchor.y - actual.top - anchor.v * actual.height}px`;
    const settled = content.getBoundingClientRect();
    Object.assign(anchor, {
      width: settled.width, height: settled.height, left: settled.left, top: settled.top,
      scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop, pending: false,
    });
  }
}
