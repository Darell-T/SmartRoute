import { parseAgentEvent } from "./agent-chat-event-validator";
import type { AgentSource, ArrivalCardEvent, RouteCard } from "./agent-chat-stream";
import {
  arrivalsFromEvent,
  createChatState,
  type AssistantTurn,
  type ChatState,
  type ChatTurn,
} from "./agent-chat-state";

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

export interface SessionSnapshotHistoryEntry {
  role: "user" | "assistant";
  text: string;
  turn_id?: string;
}

/** Read-only transcript projection returned by the backend snapshot route. */
export interface SessionSnapshot {
  session_id: string;
  history: SessionSnapshotHistoryEntry[];
  route_cards: RouteCard[];
  arrival_cards: ArrivalCardEvent[];
  sources?: SessionSnapshotSources[];
}

export interface SessionSnapshotSources {
  turn_id: string;
  sources: AgentSource[];
}

export type SessionSnapshotResult =
  | { status: "ok"; turns: ChatTurn[] }
  | { status: "expired" }
  | { status: "unavailable" };

export type SessionSnapshotTransport = (sessionId: string) => Promise<Response>;

const SNAPSHOT_ENDPOINT = "/api/agent/chat/session";
const RESET_ENDPOINT = "/api/agent/chat/session/reset";

async function defaultSnapshotTransport(sessionId: string): Promise<Response> {
  return fetch(SNAPSHOT_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

function parseSnapshot(data: unknown): SessionSnapshot | null {
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const record = data as Record<string, unknown>;
  if (typeof record.session_id !== "string" || record.session_id.length === 0) return null;
  const history: SessionSnapshotHistoryEntry[] = [];
  if (Array.isArray(record.history)) {
    for (const entry of record.history) {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
      const item = entry as Record<string, unknown>;
      if (
        (item.role !== "user" && item.role !== "assistant") ||
        typeof item.text !== "string"
      ) {
        return null;
      }
      history.push({
        role: item.role,
        text: item.text,
        ...(typeof item.turn_id === "string" ? { turn_id: item.turn_id } : {}),
      });
    }
  }
  const routeCards: RouteCard[] = [];
  if (Array.isArray(record.route_cards)) {
    for (const card of record.route_cards) {
      // Cards are revalidated at this boundary exactly like live SSE events;
      // an invalid card is dropped rather than poisoning the whole transcript.
      const parsed = parseAgentEvent("route_card", card);
      if (!parsed || parsed.type !== "route_card") continue;
      const { type: _type, ...rest } = parsed;
      routeCards.push(rest);
    }
  }
  const arrivalCards: ArrivalCardEvent[] = [];
  if (Array.isArray(record.arrival_cards)) {
    for (const card of record.arrival_cards) {
      const parsed = parseAgentEvent("arrival_card", card);
      if (parsed?.type === "arrival_card") arrivalCards.push(parsed);
    }
  }
  const snapshotSources: SessionSnapshotSources[] = [];
  if (Array.isArray(record.sources)) {
    for (const item of record.sources) {
      if (item === null || item === undefined) continue;
      const turnId = Object.getOwnPropertyDescriptor(item, "turn_id")?.value;
      const sourceItems = Object.getOwnPropertyDescriptor(item, "sources")?.value;
      const parsedTurn = parseAgentEvent("meta", {
        session_id: "snapshot",
        turn_id: turnId,
      });
      const parsedSources = parseAgentEvent("sources", { sources: sourceItems });
      if (parsedTurn?.type === "meta" && parsedSources?.type === "sources") {
        snapshotSources.push({ turn_id: parsedTurn.turn_id, sources: parsedSources.sources });
      }
    }
  }
  return {
    session_id: record.session_id,
    history,
    route_cards: routeCards,
    arrival_cards: arrivalCards,
    sources: snapshotSources,
  };
}

/**
 * Rebuilds the visible transcript from a backend snapshot. Canonical cards
 * attach to the assistant turn that produced them by turn id.
 */
export function buildTurnsFromSnapshot(snapshot: SessionSnapshot): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const entry of snapshot.history) {
    if (entry.role === "user") {
      turns.push({ role: "user", text: entry.text });
    } else {
      turns.push({
        role: "assistant",
        turnId: entry.turn_id ?? "",
        text: entry.text,
        reasoning: "",
        toolChips: [],
        routeCards: [],
        isStreaming: false,
      });
    }
  }
  const assistantByTurnId = new Map(
    turns
      .filter((turn): turn is AssistantTurn => turn.role === "assistant")
      .map((turn) => [turn.turnId, turn]),
  );
  for (const card of snapshot.route_cards) {
    const turn = assistantByTurnId.get(card.turn_id);
    if (turn) turn.routeCards.push(card);
  }
  for (const card of snapshot.arrival_cards) {
    const turn = assistantByTurnId.get(card.turn_id);
    if (turn) turn.arrivals = arrivalsFromEvent(card);
  }
  for (const item of snapshot.sources ?? []) {
    const turn = assistantByTurnId.get(item.turn_id);
    if (turn) turn.sources = item.sources;
  }
  return turns;
}

/** Best-effort server wipe. Local New Trip never waits on the network. */
export async function resetSession(sessionId: string): Promise<void> {
  try {
    await fetch(RESET_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
      keepalive: true,
    });
  } catch {
    // The browser state is already reset; the old opaque id is no longer used.
  }
}

/**
 * Fetches and validates the backend transcript snapshot for a persisted
 * session id. A stable 404 means the session expired; other failures keep the
 * opaque id so a transient restore outage does not discard server context.
 */
export async function fetchSessionSnapshot(
  sessionId: string,
  transport: SessionSnapshotTransport = defaultSnapshotTransport,
): Promise<SessionSnapshotResult> {
  let response: Response;
  try {
    response = await transport(sessionId);
  } catch {
    return { status: "unavailable" };
  }
  if (response.status === 404) return { status: "expired" };
  if (!response.ok) return { status: "unavailable" };
  try {
    const snapshot = parseSnapshot(await response.json());
    if (!snapshot) return { status: "unavailable" };
    return { status: "ok", turns: buildTurnsFromSnapshot(snapshot) };
  } catch {
    return { status: "unavailable" };
  }
}
