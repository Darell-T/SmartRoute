/**
 * SmartRoute agent chat — card→map selection
 *
 * Pure translation of a tapped `RouteCard` (from `lib/agent-chat-stream.ts`)
 * into the shape `page.tsx` needs to drive the Live Map tab: the plain
 * `RouteStep[]` the map renderer already knows how to draw, plus the
 * destination coordinates for the camera flight. No React, no fetch --
 * deliberately kept parallel to the destCoords derivation `page.tsx` already
 * does for the manual (rail) route so agent and manual routes land on the
 * map identically.
 */

import type { RouteCard } from "@/lib/agent-chat-stream";
import type { DestinationSelection, RouteCandidate, RouteStep } from "@/types/api";

export interface AgentRouteSelection {
  cardId: string;
  steps: RouteStep[];
  destCoords: { lat: number; lng: number };
}

/** Prefer canonical itinerary seconds over legacy summary ETA. */
function totalMinutesFromCard(card: RouteCard): number {
  const seconds = card.itinerary?.total_duration_seconds;
  if (typeof seconds === "number" && Number.isFinite(seconds)) {
    return Math.round(seconds / 60);
  }
  return card.summary.eta_minutes;
}

/** Prefer canonical itinerary transfer_count over summary.transfers. */
function transferCountFromCard(card: RouteCard): number {
  const fromItin = card.itinerary?.transfer_count;
  if (typeof fromItin === "number" && Number.isFinite(fromItin)) {
    return Math.max(0, Math.round(fromItin));
  }
  return card.summary.transfers;
}

/** Prefer itinerary.arrival_at when it is a non-empty string. */
function arrivalAtFromCard(card: RouteCard): string | undefined {
  const iso = card.itinerary?.arrival_at;
  if (typeof iso === "string" && iso.trim()) return iso.trim();
  return undefined;
}

export function normalizeRouteCoordinate(
  value: unknown,
): { lat: number; lng: number } | null {
  if (!value || typeof value !== "object") return null;

  const coordinate = value as {
    latitude?: unknown;
    longitude?: unknown;
    lat?: unknown;
    lng?: unknown;
  };
  const lat = coordinate.latitude ?? coordinate.lat;
  const lng = coordinate.longitude ?? coordinate.lng;

  return typeof lat === "number" &&
    Number.isFinite(lat) &&
    typeof lng === "number" &&
    Number.isFinite(lng)
    ? { lat, lng }
    : null;
}

export interface AgentRoutePlan {
  destination: DestinationSelection;
  candidates: RouteCandidate[];
  activeCandidateId: string;
  recommendationText: string;
  /** The rail suppresses duplicated chat reasoning for this route source. */
  entryContext: "chat";
}

/**
 * Builds the map-ready selection for a tapped route card. Returns `null`
 * when the card carries no route geometry (empty or missing `route` array)
 * -- callers should treat that as a no-op tap rather than clearing whatever
 * is currently on the map.
 *
 * `destCoords` prefers the last step's own geometry -- a trailing WALK leg's
 * `end_point`, else a trailing transit leg's `arrival_coords` -- the same
 * precedence `page.tsx` uses for the manual route, so the camera lands on
 * the actual last waypoint rather than a coarser card-level pin. Falls back
 * to the card's `destination` label coordinates when the last step carries
 * neither (e.g. a step shape from an older server build).
 */
export function agentRouteFromCard(card: RouteCard): AgentRouteSelection | null {
  const steps = card.route;
  if (!Array.isArray(steps) || steps.length === 0) return null;

  const lastStep = steps[steps.length - 1];
  const rawDest =
    lastStep?.type === "WALK" ? lastStep.end_point : lastStep?.arrival_coords;
  const destCoords =
    normalizeRouteCoordinate(rawDest) ?? normalizeRouteCoordinate(card.destination);
  if (!destCoords) return null;

  return {
    cardId: card.card_id,
    steps,
    destCoords,
  };
}

/** Convert every geometry-complete card from one assistant turn into the
 *  standard route-planning model consumed by the existing rail and map. */
export function agentRoutePlanFromCards(
  cards: RouteCard[],
  selectedCardId: string,
): AgentRoutePlan | null {
  const selectedCard = cards.find((card) => card.card_id === selectedCardId);
  if (!selectedCard) return null;
  const selectedRoute = agentRouteFromCard(selectedCard);
  if (!selectedRoute) return null;

  const candidates = cards.flatMap((card, index): RouteCandidate[] => {
    const route = agentRouteFromCard(card);
    if (!route) return [];
    const isRecommended = card.role === "recommended";
    const totalMinutes = totalMinutesFromCard(card);
    const transfers = transferCountFromCard(card);
    const arrivalAt = arrivalAtFromCard(card);
    return [
      {
        id: card.card_id,
        index,
        steps: route.steps,
        is_recommended: isRecommended,
        total_minutes: totalMinutes,
        ...(arrivalAt ? { arrival_at: arrivalAt } : {}),
        score_breakdown: {
          duration_minutes: totalMinutes,
          transfers,
          active_alerts: card.alerts.length,
          transit_lines: card.summary.lines,
        },
        enriched: true,
        can_enrich_on_select: false,
        recommendation_reason: isRecommended ? card.summary.reason : undefined,
        rejection_reason: isRecommended ? undefined : card.summary.reason,
      },
    ];
  });
  if (!candidates.some((candidate) => candidate.id === selectedCardId)) return null;

  return {
    destination: {
      label: selectedCard.destination.label,
      coordinates: selectedRoute.destCoords,
    },
    candidates,
    activeCandidateId: selectedCardId,
    recommendationText: selectedCard.summary.reason,
    entryContext: "chat",
  };
}
