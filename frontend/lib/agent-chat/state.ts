import { isRouteWorkflowTool } from "./route-tools";
import type {
  AgentEvent,
  AgentSource,
  ArrivalCardEvent,
  ArrivalSourceStatus,
  ProgressEvent,
  RouteCard,
  TransitStatusActionEvent,
} from "./stream";

export interface ToolChip {
  id: string;
  tool: string;
  label: string;
  status: "running" | "ok" | "failed";
  durationMs?: number;
  summary?: string;
}

export interface UserTurn {
  role: "user";
  text: string;
}

export interface AssistantTurn {
  role: "assistant";
  turnId: string;
  text: string;
  reasoning: string;
  toolChips: ToolChip[];
  routeCards: RouteCard[];
  sources?: AgentSource[];
  progress?: Omit<ProgressEvent, "type">;
  isStreaming: boolean;
  stopReason?:
    | "end_turn"
    | "clarification_required"
    | "max_rounds"
    | "deadline"
    | "error"
    | "cancelled"
    | "dropped";
  error?: {
    code: string;
    message: string;
    retryable: boolean;
    correlationId?: string;
  };
  notice?: string;
  local?: boolean;
  arrivals?: ArrivalsTurnPayload;
  /** Server-owned action offered by a transit-status presentation. */
  transitStatusAction?: TransitStatusActionEvent["action"];
}

export type ChatTurn = UserTurn | AssistantTurn;

export interface ArrivalsTurnDirectionGroup {
  direction: string;
  label: string;
  minutes: number[];
}

export interface ArrivalsTurnPayload {
  routeId: string;
  stationName: string;
  stationGuidance?: string;
  stationCoordinates?: { lat: number; lng: number };
  groups: ArrivalsTurnDirectionGroup[];
  sourceStatus?: ArrivalSourceStatus;
  updatedAt?: string;
  catchability?: ArrivalCardEvent["catchability"];
}

export interface ChatState {
  messages: ChatTurn[];
  sessionId: string | null;
  isStreaming: boolean;
  error: string | null;
}

export type ChatReducerAction =
  | AgentEvent
  | { type: "chat_reset" }
  | { type: "session_discarded" }
  | { type: "session_restarted"; message: string }
  | { type: "session_restored"; sessionId: string; turns: ChatTurn[] }
  | { type: "turn_started"; text: string }
  | { type: "turn_retry_started" }
  | { type: "turn_error_dismissed" }
  | {
      type: "stream_error";
      message: string;
      code?: string;
      retryable?: boolean;
      correlationId?: string;
    }
  | { type: "stream_cancelled" }
  | { type: "local_turn_appended"; turnId: string; text: string; arrivals: ArrivalsTurnPayload };

const SESSION_TURN_TYPES = [
  "chat_reset",
  "session_discarded",
  "session_restarted",
  "session_restored",
  "turn_started",
  "turn_retry_started",
  "turn_error_dismissed",
  "meta",
  "local_turn_appended",
] as const;
const STREAMED_CONTENT_TYPES = ["token", "reasoning", "sources", "progress"] as const;
const TOOL_LIFECYCLE_TYPES = ["tool_start", "tool_end"] as const;
const CARD_TYPES = ["route_card", "arrival_card", "transit_status_action"] as const;
const TERMINAL_TYPES = ["error", "done", "stream_cancelled", "stream_error"] as const;

const HIDDEN_ARRIVAL_SOURCES: ReadonlySet<ArrivalSourceStatus> = new Set([
  "stop_not_resolved",
  "provider_unavailable",
  "no_predictions",
]);
const HIDDEN_ARRIVAL_RESOLUTIONS: ReadonlySet<ArrivalCardEvent["resolution_status"]> = new Set([
  "ambiguous",
  "location_required",
  "provider_unavailable",
  "no_predictions",
]);

type SessionTurnAction = Extract<ChatReducerAction, { type: (typeof SESSION_TURN_TYPES)[number] }>;
type StreamedContentAction = Extract<ChatReducerAction, { type: (typeof STREAMED_CONTENT_TYPES)[number] }>;
type ToolLifecycleAction = Extract<ChatReducerAction, { type: (typeof TOOL_LIFECYCLE_TYPES)[number] }>;
type CardAction = Extract<ChatReducerAction, { type: (typeof CARD_TYPES)[number] }>;
type TerminalAction = Extract<ChatReducerAction, { type: (typeof TERMINAL_TYPES)[number] }>;

function actionHasType<T extends string>(
  action: ChatReducerAction,
  types: readonly T[],
): action is Extract<ChatReducerAction, { type: T }> {
  return types.some((type) => action.type === type);
}

export function createChatState(sessionId: string | null): ChatState {
  return { messages: [], sessionId, isStreaming: false, error: null };
}

function lastAssistantTurn(messages: ChatTurn[]): AssistantTurn | null {
  const last = messages[messages.length - 1];
  return last && last.role === "assistant" ? last : null;
}

function appendUniqueReasoning(reasoning: string, text: string): string {
  const nextLine = text.trim();
  if (!nextLine) return reasoning;
  const alreadyPresent = reasoning
    .split("\n")
    .some((line) => line.trim() === nextLine);
  if (alreadyPresent) return reasoning;
  return reasoning ? `${reasoning}\n${text}` : text;
}

function updateLastAssistantTurn(
  state: ChatState,
  update: (turn: AssistantTurn) => AssistantTurn,
): ChatState {
  const turn = lastAssistantTurn(state.messages);
  if (!turn) return state;
  const messages = state.messages.slice();
  messages[messages.length - 1] = update(turn);
  return { ...state, messages };
}

function cardFromEvent(event: Extract<AgentEvent, { type: "route_card" }>): RouteCard {
  const { type: _type, ...card } = event;
  return card;
}

export function arrivalsFromEvent(event: ArrivalCardEvent): ArrivalsTurnPayload {
  const distance = event.stop.distance_meters;
  const walking = event.catchability?.walking_minutes;
  const guidance = [
    walking != null ? `${walking} min walk` : null,
    distance != null ? `${Math.max(0.1, distance / 1609.344).toFixed(1)} mi away` : null,
  ].filter((value): value is string => Boolean(value));
  const latitude = event.stop.latitude;
  const longitude = event.stop.longitude;
  const payload: ArrivalsTurnPayload = {
    routeId: event.route_id,
    stationName: event.stop.name || "Transit stop",
    groups: event.directions.flatMap((direction) => {
      const minutes = direction.arrivals
        .map((arrival) => arrival.minutes)
        .filter((value) => value > 0);
      return minutes.length > 0
        ? [{ direction: direction.id, label: direction.label, minutes }]
        : [];
    }),
    sourceStatus: event.source_status,
    updatedAt: event.updated_at,
  };
  if (guidance.length > 0) payload.stationGuidance = guidance.join(" · ");
  if (latitude != null && longitude != null) {
    payload.stationCoordinates = { lat: latitude, lng: longitude };
  }
  if (event.catchability) payload.catchability = event.catchability;
  return payload;
}

function isEmptyFailedTurn(turn: AssistantTurn | null): boolean {
  return Boolean(
    turn?.error && !turn.text && turn.routeCards.length === 0 && !turn.arrivals,
  );
}

function messagesAfterClearingEmptyFailure(state: ChatState): ChatTurn[] {
  return isEmptyFailedTurn(lastAssistantTurn(state.messages))
    ? state.messages.slice(0, -1)
    : state.messages;
}

function dismissTurnError(state: ChatState): ChatState {
  if (isEmptyFailedTurn(lastAssistantTurn(state.messages))) {
    return { ...state, messages: state.messages.slice(0, -1), error: null };
  }
  return {
    ...updateLastAssistantTurn(state, (current) => ({ ...current, error: undefined })),
    error: null,
  };
}

function restoreSessionIfIdle(
  state: ChatState,
  action: Extract<ChatReducerAction, { type: "session_restored" }>,
): ChatState {
  if (state.messages.length > 0) return state;
  return {
    messages: action.turns,
    sessionId: action.sessionId,
    isStreaming: false,
    error: null,
  };
}

function applySessionAndTurn(state: ChatState, action: SessionTurnAction): ChatState {
  switch (action.type) {
    case "chat_reset":
      return createChatState(null);
    case "session_discarded":
      return { ...state, sessionId: null };
    case "session_restarted":
      return updateLastAssistantTurn(state, (turn) => ({
        ...turn,
        notice: action.message,
      }));
    case "session_restored":
      return restoreSessionIfIdle(state, action);
    case "turn_started": {
      const userTurn: UserTurn = { role: "user", text: action.text };
      const assistantTurn: AssistantTurn = {
        role: "assistant",
        turnId: "",
        text: "",
        reasoning: "",
        toolChips: [],
        routeCards: [],
        isStreaming: true,
      };
      return {
        ...state,
        messages: [...messagesAfterClearingEmptyFailure(state), userTurn, assistantTurn],
        isStreaming: true,
        error: null,
      };
    }
    case "turn_retry_started":
      return {
        ...updateLastAssistantTurn(state, (turn) => ({
          ...turn,
          turnId: "",
          text: "",
          reasoning: "",
          toolChips: [],
          routeCards: [],
          sources: undefined,
          arrivals: undefined,
          transitStatusAction: undefined,
          progress: undefined,
          isStreaming: true,
          stopReason: undefined,
          error: undefined,
        })),
        isStreaming: true,
        error: null,
      };
    case "turn_error_dismissed":
      return dismissTurnError(state);
    case "meta":
      return {
        ...updateLastAssistantTurn(state, (turn) => ({ ...turn, turnId: action.turn_id })),
        sessionId: action.session_id,
      };
    case "local_turn_appended": {
      const turn: AssistantTurn = {
        role: "assistant",
        turnId: action.turnId,
        text: action.text,
        reasoning: "",
        toolChips: [],
        routeCards: [],
        isStreaming: false,
        stopReason: "end_turn",
        local: true,
        arrivals: action.arrivals,
      };
      return { ...state, messages: [...state.messages, turn] };
    }
    default: {
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}

function mergeTurnSources(turn: AssistantTurn, sources: AgentSource[]): AssistantTurn {
  if (!turn.isStreaming) return turn;
  const byUrl = new Map(turn.sources?.map((source) => [source.url, source]));
  for (const source of sources) byUrl.set(source.url, source);
  return { ...turn, sources: [...byUrl.values()] };
}

function applyProgressUpdate(turn: AssistantTurn, action: ProgressEvent): AssistantTurn {
  if (!turn.isStreaming) return turn;
  if (action.status === "active") {
    return { ...turn, progress: { stage: action.stage, status: action.status } };
  }
  return turn.progress?.stage === action.stage ? { ...turn, progress: undefined } : turn;
}

function applyStreamedContent(state: ChatState, action: StreamedContentAction): ChatState {
  switch (action.type) {
    case "token":
      return updateLastAssistantTurn(state, (turn) => ({ ...turn, text: turn.text + action.text }));
    case "reasoning":
      return updateLastAssistantTurn(state, (turn) => ({
        ...turn,
        reasoning: appendUniqueReasoning(turn.reasoning, action.text),
      }));
    case "sources":
      return updateLastAssistantTurn(state, (turn) => mergeTurnSources(turn, action.sources));
    case "progress":
      return updateLastAssistantTurn(state, (turn) => applyProgressUpdate(turn, action));
    default: {
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}

function upsertRunningToolChip(turn: AssistantTurn, action: Extract<AgentEvent, { type: "tool_start" }>): AssistantTurn {
  const nextChip: ToolChip = {
    id: action.tool_call_id,
    tool: action.tool,
    label: action.label,
    status: "running",
  };
  const existingIndex = turn.toolChips.findIndex((chip) => chip.id === action.tool_call_id);
  if (existingIndex >= 0) {
    const toolChips = turn.toolChips.slice();
    toolChips[existingIndex] = nextChip;
    return { ...turn, toolChips };
  }
  return {
    ...turn,
    toolChips: [
      ...turn.toolChips.filter((chip) => !(chip.tool === action.tool && chip.status === "failed")),
      nextChip,
    ],
  };
}

function applyToolEnd(turn: AssistantTurn, action: Extract<AgentEvent, { type: "tool_end" }>): AssistantTurn {
  const toolChips = turn.toolChips.map((chip): ToolChip => {
    if (chip.id !== action.tool_call_id) return chip;
    const status: ToolChip["status"] = action.ok ? "ok" : "failed";
    return {
      ...chip,
      status,
      durationMs: action.ok ? action.duration_ms : undefined,
      summary: action.summary,
      // The start label is the only rider-facing activity copy. A tool
      // receipt can contain counts, provider wording, or execution
      // details, so it must never replace the contextual label.
      label: chip.label,
    };
  });
  if (isRouteWorkflowTool(action.tool) && !action.ok) {
    return { ...turn, toolChips, progress: undefined };
  }
  return { ...turn, toolChips };
}

function applyToolLifecycle(state: ChatState, action: ToolLifecycleAction): ChatState {
  switch (action.type) {
    case "tool_start":
      return updateLastAssistantTurn(state, (turn) => upsertRunningToolChip(turn, action));
    case "tool_end":
      return updateLastAssistantTurn(state, (turn) => applyToolEnd(turn, action));
    default: {
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}

function arrivalCardIsHidden(event: ArrivalCardEvent): boolean {
  return (
    HIDDEN_ARRIVAL_SOURCES.has(event.source_status) ||
    HIDDEN_ARRIVAL_RESOLUTIONS.has(event.resolution_status) ||
    !event.directions.some((group) => group.arrivals.length > 0)
  );
}

function appendUniqueRouteCard(turn: AssistantTurn, card: RouteCard): AssistantTurn {
  if (turn.routeCards.some((existing) => existing.card_id === card.card_id)) return turn;
  return { ...turn, routeCards: [...turn.routeCards, card], progress: undefined };
}

function isActionForCurrentTurn(turn: AssistantTurn | null, turnId: string): boolean {
  return Boolean(turn && (!turn.turnId || turn.turnId === turnId));
}

function applyCardAction(state: ChatState, action: CardAction): ChatState {
  const turn = lastAssistantTurn(state.messages);
  if (!isActionForCurrentTurn(turn, action.turn_id)) return state;
  switch (action.type) {
    case "route_card":
      return updateLastAssistantTurn(state, (current) => appendUniqueRouteCard(current, cardFromEvent(action)));
    case "arrival_card":
      if (arrivalCardIsHidden(action)) return state;
      return updateLastAssistantTurn(state, (current) => ({ ...current, arrivals: arrivalsFromEvent(action) }));
    case "transit_status_action":
      return updateLastAssistantTurn(state, (current) => ({
        ...current,
        transitStatusAction: action.action,
      }));
    default: {
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}

function shouldIgnoreDone(turn: AssistantTurn | null, turnId: string): boolean {
  if (!turn) return false;
  if (!turn.isStreaming) return true;
  return Boolean(turn.turnId && turn.turnId !== turnId);
}

function applyTerminal(state: ChatState, action: TerminalAction): ChatState {
  switch (action.type) {
    case "error": {
      const turn = lastAssistantTurn(state.messages);
      if (turn?.arrivals) return state;
      return {
        ...updateLastAssistantTurn(state, (current) => ({
          ...current,
          error: { code: action.code, message: action.message, retryable: action.retryable },
          progress: undefined,
        })),
        error: action.message,
      };
    }
    case "done": {
      const turn = lastAssistantTurn(state.messages);
      if (shouldIgnoreDone(turn, action.turn_id)) return state;
      const clarification = action.stop_reason === "clarification_required";
      const next: ChatState = {
        ...updateLastAssistantTurn(state, (current) => {
          const updated: AssistantTurn = {
            ...current,
            isStreaming: false,
            stopReason: action.stop_reason,
            progress: undefined,
          };
          if (clarification) updated.error = undefined;
          return updated;
        }),
        sessionId: action.session_id,
        isStreaming: false,
      };
      if (clarification) next.error = null;
      return next;
    }
    case "stream_cancelled":
      return {
        ...updateLastAssistantTurn(state, (turn) => ({
          ...turn,
          isStreaming: false,
          stopReason: "cancelled",
          progress: undefined,
        })),
        isStreaming: false,
      };
    case "stream_error":
      return {
        ...updateLastAssistantTurn(state, (turn) => ({
          ...turn,
          isStreaming: false,
          stopReason: "dropped",
          progress: undefined,
          error: turn.error ?? {
            code: action.code ?? "upstream_error",
            message: action.message,
            retryable: action.retryable ?? true,
            correlationId: action.correlationId,
          },
        })),
        isStreaming: false,
        error: action.message,
      };
    default: {
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}

export function applyAgentEvent(state: ChatState, action: ChatReducerAction): ChatState {
  if (actionHasType(action, SESSION_TURN_TYPES)) return applySessionAndTurn(state, action);
  if (actionHasType(action, STREAMED_CONTENT_TYPES)) return applyStreamedContent(state, action);
  if (actionHasType(action, TOOL_LIFECYCLE_TYPES)) return applyToolLifecycle(state, action);
  if (actionHasType(action, CARD_TYPES)) return applyCardAction(state, action);
  if (actionHasType(action, TERMINAL_TYPES)) return applyTerminal(state, action);
  const _exhaustive: never = action;
  return _exhaustive;
}
