export type NumberSpan = { start: number; end: number; text: string };

export function canonicalNumber(value: string): string | undefined {
  const clean = value
    .replace(/[\s\u00a0\u202f]/g, "")
    .replace("−", "-")
    .replace(",", ".");
  if (!/^-?\d+(?:\.\d+)?$/.test(clean)) return undefined;
  const [integer, fraction = ""] = clean.split(".");
  const sign = integer.startsWith("-") ? "-" : "";
  const digits = integer.replace("-", "").replace(/^0+(?=\d)/, "");
  const decimal = fraction.replace(/0+$/, "");
  return `${sign && !(digits === "0" && !decimal) ? sign : ""}${digits}${decimal ? "." + decimal : ""}`;
}

export function numberSpans(text: string, values?: string[]): NumberSpan[] {
  const wanted = values
    ? new Set(values.map(canonicalNumber).filter(Boolean))
    : undefined;
  const result: NumberSpan[] = [];
  const pattern = wanted
    ? /(?<![\d/.])[-−]?\d(?:[\d \u00a0\u202f]*\d)?[,.]\d+(?![\d./])/g
    : /(?<![\d/.])[-−]?\d(?:[\d \u00a0\u202f]*\d)?[,.]\d{2}(?![\d./])/g;
  for (const match of text.matchAll(pattern)) {
    if (!wanted || wanted.has(canonicalNumber(match[0])))
      result.push({
        start: match.index!,
        end: match.index! + match[0].length,
        text: match[0],
      });
  }
  if (wanted && !result.length) {
    for (const match of text.matchAll(
      /(?<![\d/.,])[-−]?\d+(?:[ \u00a0\u202f]\d{3})*(?![\d/.,])/g,
    ))
      if (wanted.has(canonicalNumber(match[0])))
        result.push({
          start: match.index!,
          end: match.index! + match[0].length,
          text: match[0],
        });
  }
  return result;
}
