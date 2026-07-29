import type { ResponsePresentationMode } from "./response-presentation";

export interface AgentChatRequestBody {
  session_id?: string;
  message: string;
  origin?: { lat: number; lng: number };
  selected_card_id?: string;
  response_presentation: ResponsePresentationMode;
}

interface BuildAgentChatRequestInput {
  sessionId: string | null;
  message: string;
  origin?: { lat: number; lng: number } | null;
  selectedCardId: string | null;
  responsePresentation?: ResponsePresentationMode;
}

export function buildAgentChatRequest({
  sessionId,
  message,
  origin,
  selectedCardId,
  responsePresentation = "auto",
}: BuildAgentChatRequestInput): AgentChatRequestBody {
  return {
    session_id: validOpaqueId(sessionId, 128),
    message: message.trim(),
    origin: validOrigin(origin),
    selected_card_id: validOpaqueId(selectedCardId, 64),
    response_presentation: responsePresentation,
  };
}

function validOrigin(origin: { lat: number; lng: number } | null | undefined):
  | { lat: number; lng: number }
  | undefined {
  if (!origin || !Number.isFinite(origin.lat) || !Number.isFinite(origin.lng)) {
    return undefined;
  }
  return { lat: origin.lat, lng: origin.lng };
}

function validOpaqueId(value: string | null, maxLength: number): string | undefined {
  return value && value.length <= maxLength ? value : undefined;
}
