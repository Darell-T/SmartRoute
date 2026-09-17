/**
 * Pure adapter from agent route cards to the recommendation-card view model.
 *
 * The server-owned canonical itinerary is authoritative whenever present.
 * Formatting happens here, but route facts, stop order, and durations are
 * never recomputed by the card component.
 */

import type {
  CanonicalItinerary,
  RecommendationReason,
  RouteCard,
} from "@/lib/agent-chat/stream";
import { formatNycRouteClock } from "@/lib/nyc-route-clock";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import {
  buildEventsFromCanonicalItinerary,
  canonicalPlaceLabel,
  durationMinutesFromSeconds,
  formatDurationMinutes,
  type ItineraryEvent,
} from "./itinerary-event-adapter";

export {
  condensePreviewEvents,
  formatDurationMinutes,
  intermediateStopNames,
} from "./itinerary-event-adapter";
export type { ItineraryEvent, ItineraryEventKind } from "./itinerary-event-adapter";

export interface ItineraryViewModel {
  id: string;
  recommended: boolean;
  placeNames: string[];
  arrivalLabel: string | null;
  firstLegArrivalLabel: string | null;
  durationLabel: string;
  totalMinutes: number;
  transferCount: number;
  metaParts: string[];
  events: ItineraryEvent[];
  rationale: string[];
  primaryActionLabel: string;
  secondaryActionLabel: string;
  invalid: boolean;
  invalidReason?: string;
  sourceCardIds: string[];
  primaryCardId: string;
}

/** Retained for older callers. Semantic sections are not hard-truncated. */
export const PREVIEW_EVENT_MAX = 5;

export function formatClockTime(iso: string | undefined | null): string | null {
  return formatNycRouteClock(iso);
}

export function transferLabel(count: number): string {
  if (count <= 0) return "0 transfers";
  return count === 1 ? "1 transfer" : `${count} transfers`;
}

function firstLegArrivalLabel(card: RouteCard): string | null {
  const context = card.summary.first_leg_arrival;
  const minutes = context?.catchable_arrival_minutes;
  const routeId = context?.route_id?.trim();
  if (
    !routeId ||
    minutes == null ||
    !Number.isFinite(minutes) ||
    !["live", "scheduled"].includes(context?.source_status ?? "")
  ) {
    return null;
  }
  return `Next realistic ${routeId}: ${Math.max(0, Math.round(minutes))} min`;
}

export function parseRationale(reason: string | undefined | null): string[] {
  if (!reason?.trim()) return [];
  return reason
    .trim()
    .split(/\s*[·•|]\s*/)
    .map((part) => part.trim().replace(/[.]+$/, ""))
    .filter(Boolean);
}

function structuredFastestCopy(
  reason: Extract<RecommendationReason, { code: "fastest" }>,
): string {
  const seconds =
    reason.difference_seconds != null && Number.isFinite(reason.difference_seconds)
      ? Math.max(0, reason.difference_seconds)
      : 0;
  if (seconds >= 60) return `About ${Math.round(seconds / 60)} min faster than the next option`;
  return "Fastest available route";
}

function structuredFewerTransfersCopy(
  reason: Extract<RecommendationReason, { code: "fewer_transfers" }>,
): string | null {
  const difference = Math.max(0, reason.transfer_difference);
  if (!difference) return null;
  return `Uses ${difference} fewer ${difference === 1 ? "transfer" : "transfers"}`;
}

const STRUCTURED_REASON_COPY = new Map<string, string>(
  Object.entries({
    less_walking: "Less walking than the other options",
    avoids_active_disruption: "Avoids active service alerts on another option",
    lower_event_crowd_exposure: "Lower exposure to nearby event crowds",
    accessibility: "Meets the accessibility requirement",
    reasonable_local_option: "Nearby option with a reasonable overall trip",
  }),
);

export function formatStructuredRecommendationReason(
  reason: RecommendationReason | string | unknown,
): string | null {
  if (typeof reason === "string") return reason.trim() || null;
  if (!reason || typeof reason !== "object" || !("code" in reason)) return null;
  const structured = reason as RecommendationReason;
  if (structured.code === "fastest") return structuredFastestCopy(structured);
  if (structured.code === "fewer_transfers") return structuredFewerTransfersCopy(structured);
  return STRUCTURED_REASON_COPY.get(structured.code) ?? null;
}

function isValidCard(
  card: RouteCard,
): card is RouteCard & { itinerary: CanonicalItinerary } {
  return Boolean(
    card &&
      card.card_id &&
      card.destination?.label?.trim() &&
      card.itinerary?.itinerary_id &&
      Number.isFinite(card.itinerary.total_duration_seconds) &&
      Number.isFinite(card.itinerary.transfer_count),
  );
}

function cardTotalMinutes(card: RouteCard): number {
  return durationMinutesFromSeconds(card.itinerary?.total_duration_seconds) ?? 0;
}

function cardTransferCount(card: RouteCard): number {
  const canonical = card.itinerary?.transfer_count;
  return canonical != null && Number.isFinite(canonical)
    ? Math.max(0, Math.round(canonical))
    : 0;
}

function cardArrivalLabel(card: RouteCard): string | null {
  return formatClockTime(card.itinerary?.arrival_at);
}

function buildMetaParts(transferCount: number, dwellMinutes: number): string[] {
  const parts: string[] = [transferLabel(transferCount)];
  if (dwellMinutes > 0) parts.push(`${formatDurationMinutes(dwellMinutes)} stop`);
  return parts;
}

function unavailableItineraryViewModel(
  card: RouteCard | undefined,
  primaryActionLabel: string,
  secondaryActionLabel: string,
): ItineraryViewModel {
  return {
    id: card?.card_id ?? "invalid",
    recommended: card?.role === "recommended",
    placeNames: [],
    arrivalLabel: null,
    firstLegArrivalLabel: null,
    durationLabel: "—",
    totalMinutes: 0,
    transferCount: 0,
    metaParts: [],
    events: [],
    rationale: [],
    primaryActionLabel,
    secondaryActionLabel,
    invalid: true,
    invalidReason: "This itinerary is unavailable.",
    sourceCardIds: card?.card_id ? [card.card_id] : [],
    primaryCardId: card?.card_id ?? "invalid",
  };
}

function cardPlaceNames(card: RouteCard): string[] {
  const originLabel = card.origin?.label?.trim() || "Your location";
  const destinationLabel = card.destination.label.trim();
  const waypointNames = Array.isArray(card.itinerary?.waypoints)
    ? card.itinerary.waypoints
        .map((waypoint) => canonicalPlaceLabel(waypoint, ""))
        .filter(Boolean)
    : [];
  return [originLabel, ...waypointNames, destinationLabel].filter(
    (name, index, values) => index === 0 || values[index - 1] !== name,
  );
}

function cardRationale(card: RouteCard): string[] {
  const structuredReasons = card.itinerary?.structured_recommendation_reasons;
  if (Array.isArray(structuredReasons) && structuredReasons.length > 0) {
    return structuredReasons
      .map(formatStructuredRecommendationReason)
      .filter((reason): reason is string => Boolean(reason));
  }
  return parseRationale(card.summary.reason);
}

export function buildItineraryViewModel(
  card: RouteCard,
  options?: {
    primaryActionLabel?: string;
    secondaryActionLabel?: string;
  },
): ItineraryViewModel {
  const primaryActionLabel = options?.primaryActionLabel ?? "Open on map";
  const secondaryActionLabel = options?.secondaryActionLabel ?? "View steps";
  if (!isValidCard(card)) {
    return unavailableItineraryViewModel(card, primaryActionLabel, secondaryActionLabel);
  }

  const originLabel = card.origin?.label?.trim() || "Your location";
  const destinationLabel = card.destination.label.trim();
  const transferCount = cardTransferCount(card);
  return {
    id: card.card_id,
    recommended: card.role === "recommended",
    placeNames: cardPlaceNames(card),
    arrivalLabel: cardArrivalLabel(card),
    firstLegArrivalLabel: firstLegArrivalLabel(card),
    durationLabel: formatDurationMinutes(cardTotalMinutes(card)),
    totalMinutes: cardTotalMinutes(card),
    transferCount,
    metaParts: buildMetaParts(
      transferCount,
      durationMinutesFromSeconds(card.itinerary?.total_dwell_seconds) ?? 0,
    ),
    events: buildEventsFromCanonicalItinerary(
      card.itinerary,
      originLabel,
      destinationLabel,
      card.card_id,
    ),
    rationale: cardRationale(card),
    primaryActionLabel,
    secondaryActionLabel,
    invalid: false,
    sourceCardIds: [card.card_id],
    primaryCardId: card.card_id,
  };
}

/**
 * Selects the server-owned canonical card when callers pass a card group.
 * Legacy cards without a canonical itinerary deliberately render unavailable;
 * this adapter never merges their duration, transfer, or selection facts.
 */
export function buildMergedItineraryViewModel(
  recommendedCards: RouteCard[],
  options?: {
    primaryActionLabel?: string;
    secondaryActionLabel?: string;
  },
): ItineraryViewModel | null {
  if (recommendedCards.length === 0) return null;
  const canonical = recommendedCards.find(isValidCard) ?? recommendedCards[0];
  return buildItineraryViewModel(canonical, options);
}

export function shouldCollapseEvents(eventCount: number): boolean {
  return eventCount > PREVIEW_EVENT_MAX;
}

export function isSupportedSubwayRoute(routeId: string): boolean {
  return SUBWAY_BULLET_ROUTES.has(routeId.trim().toUpperCase());
}

export function warnUnsupportedRouteId(routeId: string): void {
  if (process.env.NODE_ENV === "production" || isSupportedSubwayRoute(routeId)) return;
  // eslint-disable-next-line no-console
  console.warn(`[itinerary-card] unsupported subway route id "${routeId}"`);
}
