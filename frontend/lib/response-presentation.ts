export type ResponsePresentationMode = "auto" | "quick";

export const DEFAULT_RESPONSE_PRESENTATION_MODE: ResponsePresentationMode = "auto";
export const RESPONSE_PRESENTATION_STORAGE_KEY = "sr-response-presentation";

type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

type ResponsePresentationListener = () => void;

export type ResponsePresentationModeStore = {
  subscribe(listener: ResponsePresentationListener): () => void;
  getServerSnapshot(): ResponsePresentationMode;
  getClientSnapshot(): ResponsePresentationMode;
  setMode(mode: ResponsePresentationMode): void;
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

/**
 * Keeps the server and hydration snapshots deterministic while allowing the
 * browser to promote a session preference immediately after hydration.
 */
export function createResponsePresentationModeStore(
  getStorage: () => StorageLike | undefined,
): ResponsePresentationModeStore {
  let inMemoryMode: ResponsePresentationMode | undefined;
  const listeners = new Set<ResponsePresentationListener>();

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getServerSnapshot() {
      return DEFAULT_RESPONSE_PRESENTATION_MODE;
    },
    getClientSnapshot() {
      return inMemoryMode ?? readResponsePresentationMode(getStorage());
    },
    setMode(mode) {
      inMemoryMode = mode;
      persistResponsePresentationMode(getStorage(), mode);
      listeners.forEach((listener) => listener());
    },
  };
}

export const responsePresentationModeStore = createResponsePresentationModeStore(
  browserSessionStorage,
);
