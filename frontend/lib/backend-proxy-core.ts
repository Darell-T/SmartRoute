export type JsonBodyReadResult =
  | { ok: true; empty: false; value: unknown }
  | { ok: true; empty: true; value: undefined }
  | { ok: false; empty: false; value: undefined };

export type BackendTextResult =
  | { ok: true; status: number; raw: string }
  | { ok: false; aborted: boolean };

export function appendRequestSearch(path: string, request: Request): string {
  const search = new URL(request.url).search;
  return search ? `${path}${search}` : path;
}

/** Parse a request JSON body, distinguishing empty input from malformed JSON. */
export async function readJsonBody(request: Request): Promise<JsonBodyReadResult> {
  const raw = await request.text();
  if (!raw.trim()) {
    return { ok: true, empty: true, value: undefined };
  }
  try {
    return { ok: true, empty: false, value: JSON.parse(raw) };
  } catch {
    return { ok: false, empty: false, value: undefined };
  }
}

export async function fetchBackendText(
  url: string,
  init: Omit<RequestInit, "signal">,
  timeoutMs: number,
): Promise<BackendTextResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
    });
    const raw = await response.text();
    clearTimeout(timer);
    return { ok: true, status: response.status, raw };
  } catch (err) {
    clearTimeout(timer);
    return {
      ok: false,
      aborted: err instanceof Error && err.name === "AbortError",
    };
  }
}
