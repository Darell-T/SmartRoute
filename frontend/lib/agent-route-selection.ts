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

function canonicalDurationMinutes(card: RouteCard): number | null {
  const seconds = card.itinerary?.total_duration_seconds;
  return typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 0
    ? Math.round(seconds / 60)
    : null;
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
  if (
    !selectedCard ||
    !selectedCard.itinerary ||
    canonicalDurationMinutes(selectedCard) === null ||
    !Number.isFinite(selectedCard.itinerary.transfer_count)
  ) return null;
  const selectedRoute = agentRouteFromCard(selectedCard);
  if (!selectedRoute) return null;

  const candidates = cards.flatMap((card, index): RouteCandidate[] => {
    const route = agentRouteFromCard(card);
    if (!route) return [];
    const totalMinutes = canonicalDurationMinutes(card);
    if (!card.itinerary || totalMinutes === null || !Number.isFinite(card.itinerary.transfer_count)) return [];
    return [
      {
        id: card.card_id,
        index,
        steps: route.steps,
        itinerary: card.itinerary,
        itinerary_id: card.itinerary.itinerary_id,
        origin: card.origin,
        destination: card.destination,
        is_recommended: card.role === "recommended",
        total_minutes: totalMinutes,
        ...(card.itinerary.arrival_at ? { arrival_at: card.itinerary.arrival_at } : {}),
        score_breakdown: {
          duration_minutes: totalMinutes,
          transfers: card.itinerary.transfer_count,
          active_alerts: card.alerts.length,
          transit_lines: card.summary.lines,
        },
        enriched: true,
        can_enrich_on_select: false,
        recommendation_reason: card.role === "recommended" ? card.summary.reason : undefined,
        rejection_reason: card.role === "recommended" ? undefined : card.summary.reason,
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
