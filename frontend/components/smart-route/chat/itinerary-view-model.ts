/**
 * Pure adapter from agent route cards to the recommendation-card view model.
 *
 * The server-owned canonical itinerary is authoritative whenever present.
 * Formatting happens here, but route facts, stop order, and durations are
 * never recomputed by the card component.
 */

import type {
  AgentRouteStep,
  CanonicalItinerary,
  CanonicalItineraryLeg,
  RecommendationReason,
  RouteCard,
} from "@/lib/agent-chat-stream";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";

export type ItineraryEventKind =
  | "subway"
  | "bus"
  | "walk"
  | "pickup"
  | "waypoint"
  | "transfer"
  | "destination";

export interface ItineraryEvent {
  id: string;
  kind: ItineraryEventKind;
  routeIds: string[];
  title: string;
  subtitle?: string;
  durationLabel?: string;
  durationMinutes?: number;
  fromLabel?: string;
  toLabel?: string;
  stopCount?: number;
  /** Provider-owned ordered stop names, including endpoints when supplied. */
  stops?: string[];
  sourceLabel?: string;
}

export interface ItineraryViewModel {
  id: string;
  recommended: boolean;
  placeNames: string[];
  arrivalLabel: string | null;
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

/** Retained for older callers; the redesigned card no longer truncates legs. */
export const PREVIEW_EVENT_MAX = 5;

export function formatDurationMinutes(totalMinutes: number): string {
  if (!Number.isFinite(totalMinutes) || totalMinutes < 0) return "—";
  const minutes = Math.round(totalMinutes);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours} hr ${rest} min` : `${hours} hr`;
}

export function formatClockTime(iso: string | undefined | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function transferLabel(count: number): string {
  if (count <= 0) return "0 transfers";
  return count === 1 ? "1 transfer" : `${count} transfers`;
}

export function parseRationale(reason: string | undefined | null): string[] {
  if (!reason?.trim()) return [];
  return reason
    .trim()
    .split(/\s*[·•|]\s*/)
    .map((part) => part.trim().replace(/[.]+$/, ""))
    .filter(Boolean);
}

export function formatStructuredRecommendationReason(
  reason: RecommendationReason | string | unknown,
): string | null {
  if (typeof reason === "string") return reason.trim() || null;
  if (!reason || typeof reason !== "object" || !("code" in reason)) return null;
  const structured = reason as RecommendationReason;
  if (structured.code === "fastest") {
    const seconds =
      typeof structured.difference_seconds === "number" &&
      Number.isFinite(structured.difference_seconds)
        ? Math.max(0, structured.difference_seconds)
        : 0;
    return seconds >= 60
      ? `About ${Math.round(seconds / 60)} min faster than the next option`
      : "Fastest available route";
  }
  if (structured.code === "fewer_transfers") {
    const difference = Math.max(0, structured.transfer_difference);
    return difference
      ? `Uses ${difference} fewer ${difference === 1 ? "transfer" : "transfers"}`
      : null;
  }
  if (structured.code === "avoids_active_disruption") {
    return "Avoids active service alerts on another option";
  }
  return null;
}

function durationMinutesFromSeconds(seconds: unknown): number | null {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
    return null;
  }
  return Math.round(seconds / 60);
}

function durationLabelFromMinutes(minutes: number | null): string | undefined {
  return minutes == null ? undefined : formatDurationMinutes(minutes);
}

function canonicalStopLabel(stop: unknown): string | null {
  if (typeof stop === "string" && stop.trim()) return stop.trim();
  if (stop && typeof stop === "object" && !Array.isArray(stop)) {
    const record = stop as Record<string, unknown>;
    for (const key of ["name", "label", "display_name", "station_name"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return null;
}

function canonicalPlaceLabel(place: unknown, fallback: string): string {
  if (typeof place === "string" && place.trim()) return place.trim();
  if (place && typeof place === "object" && !Array.isArray(place)) {
    const record = place as Record<string, unknown>;
    for (const key of ["display_name", "label", "name", "address"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return fallback;
}

function stepRouteId(step: AgentRouteStep): string | null {
  const value = step.train_line || step.route_id;
  return typeof value === "string" && value.trim()
    ? value.trim().toUpperCase()
    : null;
}

function legacyStepDurationMinutes(step: AgentRouteStep): number | null {
  const departure = step.departure_time_iso
    ? Date.parse(step.departure_time_iso)
    : Number.NaN;
  const arrival = step.arrival_time_iso
    ? Date.parse(step.arrival_time_iso)
    : Number.NaN;
  if (Number.isFinite(departure) && Number.isFinite(arrival) && arrival >= departure) {
    return Math.round((arrival - departure) / 60_000);
  }
  if (
    typeof step.minutes_until_arrival === "number" &&
    Number.isFinite(step.minutes_until_arrival)
  ) {
    return Math.max(0, Math.round(step.minutes_until_arrival));
  }
  return null;
}

function canonicalLegDurationMinutes(leg: CanonicalItineraryLeg): number | null {
  return leg.mode.trim().toUpperCase() === "WALK"
    ? durationMinutesFromSeconds(leg.walk_seconds)
    : durationMinutesFromSeconds(leg.ride_seconds);
}

function canonicalLegStops(leg: CanonicalItineraryLeg): string[] {
  if (!Array.isArray(leg.stops)) return [];
  return leg.stops
    .map((stop) => canonicalStopLabel(stop))
    .filter((stop): stop is string => Boolean(stop));
}

function appendCanonicalLegs(
  events: ItineraryEvent[],
  legs: CanonicalItineraryLeg[],
  segmentDestination: string,
  idPrefix: string,
): void {
  legs.forEach((leg, index) => {
    const mode = leg.mode.trim().toUpperCase();
    const fromLabel = canonicalStopLabel(leg.board) ?? undefined;
    const toLabel = canonicalStopLabel(leg.alight) ?? undefined;
    const durationMinutes = canonicalLegDurationMinutes(leg);
    const base = {
      id: `${idPrefix}-${index}`,
      title: toLabel || fromLabel || segmentDestination,
      durationMinutes: durationMinutes ?? undefined,
      durationLabel: durationLabelFromMinutes(durationMinutes),
      fromLabel,
      toLabel,
    };

    if (mode === "WALK") {
      events.push({ ...base, kind: "walk", routeIds: [] });
      return;
    }
    if (mode !== "SUBWAY" && mode !== "BUS") return;

    const serviceId =
      typeof leg.service_id === "string" ? leg.service_id.trim().toUpperCase() : "";
    events.push({
      ...base,
      kind: mode === "BUS" ? "bus" : "subway",
      routeIds: serviceId ? [serviceId] : [],
      stopCount:
        typeof leg.stop_count === "number" && Number.isFinite(leg.stop_count)
          ? Math.max(0, Math.round(leg.stop_count))
          : undefined,
      stops: canonicalLegStops(leg),
    });
  });
}

function buildEventsFromCanonicalItinerary(
  itinerary: CanonicalItinerary,
  destinationLabel: string,
  idPrefix: string,
): ItineraryEvent[] {
  const events: ItineraryEvent[] = [];
  const segments = Array.isArray(itinerary.segments) ? itinerary.segments : [];

  if (segments.length > 0) {
    const dwellBySegment = new Map(
      (Array.isArray(itinerary.dwell_events) ? itinerary.dwell_events : [])
        .filter((event) => event?.event_type === "dwell")
        .map((event) => [event.after_segment_index, event]),
    );

    [...segments]
      .sort((a, b) => a.segment_index - b.segment_index)
      .forEach((segment, position) => {
        const segmentDestination = canonicalPlaceLabel(
          segment.destination,
          position === segments.length - 1 ? destinationLabel : "Waypoint",
        );
        appendCanonicalLegs(
          events,
          Array.isArray(segment.legs) ? segment.legs : [],
          segmentDestination,
          `${idPrefix}-segment-${segment.segment_index}`,
        );
        const dwell = dwellBySegment.get(segment.segment_index);
        if (!dwell) return;
        const minutes = durationMinutesFromSeconds(dwell.duration_seconds);
        events.push({
          id: `${idPrefix}-dwell-${segment.segment_index}`,
          kind: "waypoint",
          routeIds: [],
          title: canonicalPlaceLabel(dwell.waypoint, segmentDestination),
          subtitle: minutes == null ? "Planned stop" : `${formatDurationMinutes(minutes)} stop`,
          durationMinutes: minutes ?? undefined,
          durationLabel: durationLabelFromMinutes(minutes),
          sourceLabel: dwell.source === "user" ? "Requested stop" : "Planned stop",
        });
      });
    return events;
  }

  appendCanonicalLegs(
    events,
    Array.isArray(itinerary.legs) ? itinerary.legs : [],
    destinationLabel,
    `${idPrefix}-direct`,
  );
  return events;
}

function buildEventsFromSteps(
  steps: AgentRouteStep[],
  destinationLabel: string,
  idPrefix: string,
): ItineraryEvent[] {
  return steps.flatMap((step, index): ItineraryEvent[] => {
    const fromLabel = step.departure_stop?.trim() || undefined;
    const toLabel =
      step.arrival_stop?.trim() ||
      (index === steps.length - 1 ? destinationLabel : undefined);
    const durationMinutes = legacyStepDurationMinutes(step);
    const base = {
      id: `${idPrefix}-legacy-${index}`,
      title: toLabel || fromLabel || destinationLabel,
      durationMinutes: durationMinutes ?? undefined,
      durationLabel: durationLabelFromMinutes(durationMinutes),
      fromLabel,
      toLabel,
    };

    if (step.type === "WALK") return [{ ...base, kind: "walk", routeIds: [] }];
    if (step.type !== "SUBWAY" && step.type !== "BUS") return [];
    const routeId = stepRouteId(step);
    return [{
      ...base,
      kind: step.type === "BUS" ? "bus" : "subway",
      routeIds: routeId ? [routeId] : [],
      stopCount:
        typeof step.stop_count === "number" && Number.isFinite(step.stop_count)
          ? Math.max(0, Math.round(step.stop_count))
          : undefined,
      stops: Array.isArray(step.intermediate_stops)
        ? step.intermediate_stops.filter((stop) => typeof stop === "string" && stop.trim())
        : [],
    }];
  });
}

/** Legacy helper retained for downstream imports; no longer drops journey legs. */
export function condensePreviewEvents(
  events: ItineraryEvent[],
  _destinationLabel: string,
): ItineraryEvent[] {
  return events.map((event) => ({ ...event }));
}

function isValidCard(card: RouteCard): boolean {
  return Boolean(
    card &&
      card.card_id &&
      card.destination?.label?.trim() &&
      Number.isFinite(card.summary?.eta_minutes),
  );
}

function cardTotalMinutes(card: RouteCard): number {
  const canonical = durationMinutesFromSeconds(card.itinerary?.total_duration_seconds);
  return canonical ?? Math.max(0, Math.round(card.summary.eta_minutes));
}

function cardTransferCount(card: RouteCard): number {
  const canonical = card.itinerary?.transfer_count;
  return typeof canonical === "number" && Number.isFinite(canonical)
    ? Math.max(0, Math.round(canonical))
    : Math.max(0, Math.round(card.summary.transfers));
}

function cardArrivalLabel(card: RouteCard): string | null {
  const canonical = formatClockTime(card.itinerary?.arrival_at);
  if (canonical) return canonical;
  for (let index = card.route.length - 1; index >= 0; index -= 1) {
    const label = formatClockTime(card.route[index]?.arrival_time_iso);
    if (label) return label;
  }
  return null;
}

function buildMetaParts(transferCount: number, dwellMinutes: number): string[] {
  const parts: string[] = [];
  if (transferCount > 0) parts.push(transferLabel(transferCount));
  if (dwellMinutes > 0) parts.push(`${formatDurationMinutes(dwellMinutes)} stop`);
  return parts;
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
    return {
      id: card?.card_id ?? "invalid",
      recommended: card?.role === "recommended",
      placeNames: [],
      arrivalLabel: null,
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

  const originLabel = card.origin?.label?.trim() || "Your location";
  const destinationLabel = card.destination.label.trim();
  const waypointNames = Array.isArray(card.itinerary?.waypoints)
    ? card.itinerary.waypoints
        .map((waypoint) => canonicalPlaceLabel(waypoint, ""))
        .filter(Boolean)
    : [];
  const placeNames = [originLabel, ...waypointNames, destinationLabel].filter(
    (name, index, values) => index === 0 || values[index - 1] !== name,
  );
  const transferCount = cardTransferCount(card);
  const hasCanonicalEvents =
    Boolean(card.itinerary) &&
    ((Array.isArray(card.itinerary?.segments) && card.itinerary.segments.length > 0) ||
      (Array.isArray(card.itinerary?.legs) && card.itinerary.legs.length > 0));
  const events = hasCanonicalEvents
    ? buildEventsFromCanonicalItinerary(card.itinerary!, destinationLabel, card.card_id)
    : buildEventsFromSteps(card.route ?? [], destinationLabel, card.card_id);
  const structuredReasons = card.itinerary?.structured_recommendation_reasons;
  const rationale =
    Array.isArray(structuredReasons) && structuredReasons.length > 0
      ? structuredReasons
          .map(formatStructuredRecommendationReason)
          .filter((reason): reason is string => Boolean(reason))
      : parseRationale(card.summary.reason);

  return {
    id: card.card_id,
    recommended: card.role === "recommended",
    placeNames,
    arrivalLabel: cardArrivalLabel(card),
    durationLabel: formatDurationMinutes(cardTotalMinutes(card)),
    totalMinutes: cardTotalMinutes(card),
    transferCount,
    metaParts: buildMetaParts(
      transferCount,
      durationMinutesFromSeconds(card.itinerary?.total_dwell_seconds) ?? 0,
    ),
    events,
    rationale,
    primaryActionLabel,
    secondaryActionLabel,
    invalid: false,
    sourceCardIds: [card.card_id],
    primaryCardId: card.card_id,
  };
}

/**
 * Compatibility path for saved sessions that predate canonical itineraries.
 * New plans arrive as one server-owned canonical card.
 */
export function buildMergedItineraryViewModel(
  recommendedCards: RouteCard[],
  options?: {
    primaryActionLabel?: string;
    secondaryActionLabel?: string;
  },
): ItineraryViewModel | null {
  if (recommendedCards.length === 0) return null;
  if (recommendedCards.length === 1 || recommendedCards.some((card) => card.itinerary)) {
    const canonical =
      recommendedCards.find((card) => card.itinerary) ?? recommendedCards[0];
    return buildItineraryViewModel(canonical, options);
  }

  const valid = recommendedCards.filter(isValidCard);
  if (valid.length === 0) return buildItineraryViewModel(recommendedCards[0], options);
  const first = valid[0];
  const last = valid[valid.length - 1];
  const placeNames = [
    first.origin?.label?.trim() || "Your location",
    ...valid.map((card) => card.destination.label.trim()),
  ].filter((name, index, values) => index === 0 || values[index - 1] !== name);
  const events = valid.flatMap((card) =>
    buildEventsFromSteps(card.route ?? [], card.destination.label.trim(), card.card_id),
  );
  const totalMinutes = valid.reduce((total, card) => total + cardTotalMinutes(card), 0);
  const transferCount = valid.reduce(
    (total, card) => total + cardTransferCount(card),
    0,
  );
  const rationale = valid.flatMap((card) => parseRationale(card.summary.reason));

  return {
    id: valid.map((card) => card.card_id).join("-"),
    recommended: true,
    placeNames,
    arrivalLabel: cardArrivalLabel(last),
    durationLabel: formatDurationMinutes(totalMinutes),
    totalMinutes,
    transferCount,
    metaParts: buildMetaParts(transferCount, 0),
    events,
    rationale: [...new Set(rationale)],
    primaryActionLabel: options?.primaryActionLabel ?? "Open on map",
    secondaryActionLabel: options?.secondaryActionLabel ?? "View steps",
    invalid: false,
    sourceCardIds: valid.map((card) => card.card_id),
    primaryCardId: last.card_id,
  };
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
