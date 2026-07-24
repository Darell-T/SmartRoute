export type ResponsePresentationMode = "auto" | "quick";

export const DEFAULT_RESPONSE_PRESENTATION_MODE: ResponsePresentationMode = "auto";
export const RESPONSE_PRESENTATION_STORAGE_KEY = "sr-response-presentation";

type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

export function normalizeResponsePresentationMode(
  value: unknown,
): ResponsePresentationMode {
  return value === "quick" ? "quick" : DEFAULT_RESPONSE_PRESENTATION_MODE;
}

export function readResponsePresentationMode(
  storage: StorageLike | undefined,
): ResponsePresentationMode {
  if (!storage) return DEFAULT_RESPONSE_PRESENTATION_MODE;
  try {
    return normalizeResponsePresentationMode(
      storage.getItem(RESPONSE_PRESENTATION_STORAGE_KEY),
    );
  } catch {
    return DEFAULT_RESPONSE_PRESENTATION_MODE;
  }
}

export function persistResponsePresentationMode(
  storage: StorageLike | undefined,
  mode: ResponsePresentationMode,
): void {
  if (!storage) return;
  try {
    storage.setItem(RESPONSE_PRESENTATION_STORAGE_KEY, mode);
  } catch {
    // A blocked or full session store should never stop trip planning.
  }
}

export function browserSessionStorage(): Storage | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    return window.sessionStorage;
  } catch {
    return undefined;
  }
}
