import { createChatState, type ChatState } from "./agent-chat-state";

const LEGACY_SESSION_STORAGE_KEY = "sr-agent-session";
const SESSION_RECORD_VERSION = 2;
const SESSION_STORAGE_KEY = "sr-agent-session:v2";

type StorageLike = Pick<Storage, "getItem" | "setItem"> &
  Partial<Pick<Storage, "removeItem">>;

interface SessionRecord {
  version: number;
  namespace: string;
  sessionId: string;
}

function currentSessionNamespace(): string {
  const origin = typeof window === "undefined" ? "server" : window.location.origin;
  const environment =
    process.env.NEXT_PUBLIC_AGENT_SESSION_ENV ||
    process.env.NEXT_PUBLIC_API_URL ||
    "default";
  return `${origin}|${environment}`;
}

export function sessionStorageKey(namespace: string): string {
  return `${SESSION_STORAGE_KEY}:${encodeURIComponent(namespace)}`;
}

export function safeSessionStorage(): StorageLike | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const storage = window.sessionStorage;
    storage.getItem(sessionStorageKey(currentSessionNamespace()));
    return storage;
  } catch {
    return undefined;
  }
}

function isSessionRecord(record: unknown, namespace: string): record is SessionRecord {
  if (!record || typeof record !== "object") return false;
  const version = Object.getOwnPropertyDescriptor(record, "version")?.value;
  const persistedNamespace = Object.getOwnPropertyDescriptor(record, "namespace")?.value;
  const sessionId = Object.getOwnPropertyDescriptor(record, "sessionId")?.value;
  return version === SESSION_RECORD_VERSION &&
    persistedNamespace === namespace &&
    typeof sessionId === "string";
}

export function readPersistedSessionId(
  storage: StorageLike | undefined,
  namespace = currentSessionNamespace(),
): string | null {
  if (!storage) return null;
  const key = sessionStorageKey(namespace);
  try {
    storage.removeItem?.(LEGACY_SESSION_STORAGE_KEY);
    const raw = storage.getItem(key);
    if (!raw) return null;
    const record: unknown = JSON.parse(raw);
    if (!isSessionRecord(record, namespace)) {
      storage.removeItem?.(key);
      return null;
    }
    return record.sessionId;
  } catch {
    try {
      storage.removeItem?.(key);
    } catch {
      // Storage is unavailable; continue without a persisted session.
    }
    return null;
  }
}

export function persistSessionId(
  storage: StorageLike | undefined,
  sessionId: string | null,
  namespace = currentSessionNamespace(),
): void {
  if (!storage) return;
  const key = sessionStorageKey(namespace);
  try {
    if (!sessionId) {
      storage.removeItem?.(key);
      storage.removeItem?.(LEGACY_SESSION_STORAGE_KEY);
      return;
    }
    storage.removeItem?.(LEGACY_SESSION_STORAGE_KEY);
    storage.setItem(key, JSON.stringify({
      version: SESSION_RECORD_VERSION,
      namespace,
      sessionId,
    } satisfies SessionRecord));
  } catch {
    // Persistence is optional when browser storage is blocked or full.
  }
}

export function clearPersistedSession(): void {
  try {
    const storage = safeSessionStorage();
    storage?.removeItem?.(sessionStorageKey(currentSessionNamespace()));
    storage?.removeItem?.(LEGACY_SESSION_STORAGE_KEY);
  } catch {
    // A fresh in-memory state is still useful when storage is unavailable.
  }
}

export function initChatState(): ChatState {
  return createChatState(readPersistedSessionId(safeSessionStorage()));
}
