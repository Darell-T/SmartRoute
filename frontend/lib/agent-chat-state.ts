import { isRouteWorkflowTool } from "./agent-route-tools";
import type {
  AgentEvent,
  AgentSource,
  ArrivalCardEvent,
  ArrivalSourceStatus,
  ProgressEvent,
  RouteCard,
  TransitStatusActionEvent,
} from "./agent-chat-stream";

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
    typeof walking === "number" ? `${walking} min walk` : null,
    typeof distance === "number" ? `${Math.max(0.1, distance / 1609.344).toFixed(1)} mi away` : null,
  ].filter((value): value is string => Boolean(value));
  const latitude = event.stop.latitude;
  const longitude = event.stop.longitude;
  return {
    routeId: event.route_id,
    stationName: event.stop.name || "Transit stop",
    ...(guidance.length > 0 ? { stationGuidance: guidance.join(" · ") } : {}),
    ...(typeof latitude === "number" && typeof longitude === "number"
      ? { stationCoordinates: { lat: latitude, lng: longitude } }
      : {}),
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
    ...(event.catchability ? { catchability: event.catchability } : {}),
  };
}

export function applyAgentEvent(state: ChatState, action: ChatReducerAction): ChatState {
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
    case "session_restored": {
      // A live turn may have started before the snapshot resolved; the
      // restore must never overwrite what the rider is already watching.
      if (state.messages.length > 0) return state;
      return {
        messages: action.turns,
        sessionId: action.sessionId,
        isStreaming: false,
        error: null,
      };
    }
    case "turn_started": {
      const priorMessages = (() => {
        const prior = lastAssistantTurn(state.messages);
        if (
          !prior?.error ||
          prior.text ||
          prior.routeCards.length > 0 ||
          prior.arrivals
        ) {
          return state.messages;
        }
        return state.messages.slice(0, -1);
      })();
      const userTurn: UserTurn = { role: "user", text: action.text };
      const assistantTurn: AssistantTurn = {
        role: "assistant", turnId: "", text: "", reasoning: "", toolChips: [], routeCards: [], isStreaming: true,
      };
      return {
        ...state,
        messages: [...priorMessages, userTurn, assistantTurn],
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
    case "turn_error_dismissed": {
      const turn = lastAssistantTurn(state.messages);
      const removeEmptyFailedTurn = Boolean(
        turn?.error &&
          !turn.text &&
          turn.routeCards.length === 0 &&
          !turn.arrivals,
      );
      return {
        ...state,
        messages: removeEmptyFailedTurn
          ? state.messages.slice(0, -1)
          : updateLastAssistantTurn(state, (current) => ({
              ...current,
              error: undefined,
            })).messages,
        error: null,
      };
    }
    case "meta":
      return {
        ...updateLastAssistantTurn(state, (turn) => ({ ...turn, turnId: action.turn_id })),
        sessionId: action.session_id,
      };
    case "token":
      return updateLastAssistantTurn(state, (turn) => ({ ...turn, text: turn.text + action.text }));
    case "reasoning":
      return updateLastAssistantTurn(state, (turn) => ({
        ...turn,
        reasoning: appendUniqueReasoning(turn.reasoning, action.text),
      }));
    case "sources":
      return updateLastAssistantTurn(state, (turn) => {
        if (!turn.isStreaming) return turn;
        const byUrl = new Map(turn.sources?.map((source) => [source.url, source]));
        for (const source of action.sources) byUrl.set(source.url, source);
        return { ...turn, sources: [...byUrl.values()] };
      });
    case "progress":
      return updateLastAssistantTurn(state, (turn) => {
        if (!turn.isStreaming) return turn;
        if (action.status === "active") {
          return { ...turn, progress: { stage: action.stage, status: action.status } };
        }
        return turn.progress?.stage === action.stage
          ? { ...turn, progress: undefined }
          : turn;
      });
    case "tool_start":
      return updateLastAssistantTurn(state, (turn) => {
        const nextChip: ToolChip = {
          id: action.tool_call_id, tool: action.tool, label: action.label, status: "running",
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
      });
    case "tool_end":
      return updateLastAssistantTurn(state, (turn) => ({
        ...turn,
        toolChips: turn.toolChips.map((chip) => chip.id === action.tool_call_id
          ? {
            ...chip,
            status: action.ok ? "ok" : "failed",
            durationMs: action.ok ? action.duration_ms : undefined,
            summary: action.summary,
            // The start label is the only rider-facing activity copy. A tool
            // receipt can contain counts, provider wording, or execution
            // details, so it must never replace the contextual label.
            label: chip.label,
          }
          : chip),
        ...(isRouteWorkflowTool(action.tool) && !action.ok ? { progress: undefined } : {}),
      }));
    case "route_card": {
      const card = cardFromEvent(action);
      return updateLastAssistantTurn(state, (turn) => turn.routeCards.some((existing) => existing.card_id === card.card_id)
        ? turn
        : { ...turn, routeCards: [...turn.routeCards, card], progress: undefined });
    }
    case "arrival_card":
      if (
        action.source_status === "stop_not_resolved" ||
        action.source_status === "provider_unavailable" ||
        action.source_status === "no_predictions" ||
        action.resolution_status === "ambiguous" ||
        action.resolution_status === "location_required" ||
        action.resolution_status === "provider_unavailable" ||
        action.resolution_status === "no_predictions" ||
        !action.directions.some((group) => group.arrivals.length > 0)
      ) {
        return state;
      }
      return updateLastAssistantTurn(state, (turn) => ({ ...turn, arrivals: arrivalsFromEvent(action) }));
    case "transit_status_action": {
      const turn = lastAssistantTurn(state.messages);
      if (!turn || (turn.turnId && turn.turnId !== action.turn_id)) return state;
      return updateLastAssistantTurn(state, (current) => ({
        ...current,
        transitStatusAction: action.action,
      }));
    }
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
      if (turn && (!turn.isStreaming || (turn.turnId && turn.turnId !== action.turn_id))) return state;
      const clarification = action.stop_reason === "clarification_required";
      return {
        ...updateLastAssistantTurn(state, (current) => ({
          ...current,
          isStreaming: false,
          stopReason: action.stop_reason,
          progress: undefined,
          ...(clarification ? { error: undefined } : {}),
        })),
        sessionId: action.session_id,
        isStreaming: false,
        ...(clarification ? { error: null } : {}),
      };
    }
    case "stream_cancelled":
      return {
        ...updateLastAssistantTurn(state, (turn) => ({ ...turn, isStreaming: false, stopReason: "cancelled", progress: undefined })),
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
    default:
      return state;
  }
}
