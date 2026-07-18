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
import type { RouteStep } from "@/types/api";

export interface AgentRouteSelection {
  cardId: string;
  steps: RouteStep[];
  destCoords: { lat: number; lng: number };
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
  const destCoords = rawDest
    ? { lat: rawDest.latitude, lng: rawDest.longitude }
    : { lat: card.destination.lat, lng: card.destination.lng };

  return {
    cardId: card.card_id,
    steps,
    destCoords,
  };
}
