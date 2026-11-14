export type ViewCamera = {
  zoom: number;
  page: number;
  sheet: string;
  row: number;
  column: number;
  pan: boolean;
  x: number;
  y: number;
  width: number;
};
const cameras = new Map<string, ViewCamera>();
export function loadCamera(key: string): ViewCamera | undefined {
  if (cameras.has(key)) return cameras.get(key);
  try {
    const value = JSON.parse(
      window.sessionStorage.getItem(`camera:${key}`) || "null",
    );
    if (
      value &&
      Number.isFinite(value.zoom) &&
      Number.isFinite(value.x) &&
      Number.isFinite(value.y) &&
      value.zoom >= 25 &&
      value.zoom <= 500
    ) {
      cameras.set(key, value);
      return value;
    }
  } catch {}
}
export function saveCamera(key: string, value: ViewCamera) {
  cameras.set(key, { ...value });
  try {
    window.sessionStorage.setItem(`camera:${key}`, JSON.stringify(value));
  } catch {}
}
