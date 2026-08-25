export const PROD_API_FALLBACK = "https://jarvis-mta-assistant.onrender.com";
const LOCAL_API_FALLBACK = "http://localhost:8000";
const BLOCKED_VERCEL_BACKEND_HOSTS = new Set([
  "localhost",
  "127.0.0.1",
  "0.0.0.0",
  "::1",
  "::",
]);

export interface BackendEnvironment {
  [key: string]: string | undefined;
  API_URL?: string;
  NEXT_PUBLIC_API_URL?: string;
  VERCEL?: string;
}

/**
 * Resolve the server-side FastAPI base while keeping local targets out of
 * deployed Vercel requests. Invalid production configuration falls back to
 * the established Render service so a stale build-time override cannot make
 * every server-side proxy request point at the deployer's machine.
 */
export function resolveBackendBaseUrl(environment: BackendEnvironment = process.env): string {
  const configured = environment.API_URL ?? environment.NEXT_PUBLIC_API_URL;
  if (configured && (!environment.VERCEL || isAllowedVercelBackendBase(configured))) {
    return configured.replace(/\/+$/, "");
  }
  return environment.VERCEL ? PROD_API_FALLBACK : LOCAL_API_FALLBACK;
}

function isAllowedVercelBackendBase(value: string): boolean {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    const hostname = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
    return !BLOCKED_VERCEL_BACKEND_HOSTS.has(hostname);
  } catch {
    return false;
  }
}

export type JsonBodyReadResult =
  | { ok: true; empty: false; value: unknown }
  | { ok: true; empty: true; value: undefined }
  | { ok: false; tooLarge: boolean; empty: false; value: undefined };

export type BackendTextResult =
  | { ok: true; status: number; raw: string }
  | { ok: false; aborted: boolean };

export function appendRequestSearch(path: string, request: Request): string {
  const search = new URL(request.url).search;
  return search ? `${path}${search}` : path;
}

/** Parse a request JSON body, distinguishing empty input from malformed JSON. */
export async function readJsonBody(request: Request, maxBytes = 32 * 1024): Promise<JsonBodyReadResult> {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength && /^\d+$/.test(declaredLength) && Number(declaredLength) > maxBytes) {
    return { ok: false, tooLarge: true, empty: false, value: undefined };
  }
  const reader = request.body?.getReader();
  if (!reader) return { ok: true, empty: true, value: undefined };
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        return { ok: false, tooLarge: true, empty: false, value: undefined };
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  let raw: string;
  try {
    raw = new TextDecoder("utf-8", { fatal: true }).decode(concatBytes(chunks, total));
  } catch {
    return { ok: false, tooLarge: false, empty: false, value: undefined };
  }
  if (!raw.trim()) {
    return { ok: true, empty: true, value: undefined };
  }
  try {
    return { ok: true, empty: false, value: JSON.parse(raw) };
  } catch {
    return { ok: false, tooLarge: false, empty: false, value: undefined };
  }
}

function concatBytes(chunks: Uint8Array[], length: number): Uint8Array {
  const merged = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged;
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
