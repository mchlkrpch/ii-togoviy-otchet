export class RequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `Ошибка ${response.status}`;
    try {
      const body = await response.json();
      message =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail) ?? message;
    } catch {
      /* Keep the HTTP error if the server returned an empty response. */
    }
    throw new RequestError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export const statusLabels = {
  verified: "Подтверждено",
  mismatch: "Вопрос",
  unverified: "Нет подтверждения",
  review: "Требует проверки",
  excluded: "Исключено",
  error: "Ошибка",
};

export function amount(value: string | null | undefined, unit = "RUB"): string {
  if (value == null) return "—";
  return (
    new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(value)) + (unit === "RUB" ? "" : ` ${unit}`)
  );
}

export function dateTime(value: string): string {
  return new Date(value).toLocaleString("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  });
}
