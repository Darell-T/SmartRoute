/**
 * Pure adapter: RouteCard (agent SSE shape) → itinerary card view model.
 *
 * Formats display values from supplied itinerary facts. Does not plan routes,
 * invent ranking claims, or recalculate business totals from unrelated fields.
 */

import type { AgentRouteStep, RouteCard } from "@/lib/agent-chat-stream";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";

export type ItineraryEventKind =
  | "subway"
  | "bus"
  | "walk"
  | "pickup"
  | "transfer"
  | "destination";

export interface ItineraryEvent {
  id: string;
  kind: ItineraryEventKind;
  /** Official route IDs for subway/bus bullets. Empty for walk/pickup. */
  routeIds: string[];
  /** Primary line, e.g. "Herald Sq → Jay St" or "Walk to Costco". */
  title: string;
  /** Secondary line when needed (location under pickup, etc.). */
  subtitle?: string;
  /** Formatted duration when supplied by the source steps/summary. */
  durationLabel?: string;
  durationMinutes?: number;
}

export interface ItineraryViewModel {
  id: string;
  recommended: boolean;
  /** Ordered place display names for the journey title. */
  placeNames: string[];
  arrivalLabel: string | null;
  durationLabel: string;
  totalMinutes: number;
  transferCount: number;
  /** Compact meta chips, e.g. "1 transfer", "25 min pickup". */
  metaParts: string[];
  events: ItineraryEvent[];
  /** Rationale phrases from route selection; omit section when empty. */
  rationale: string[];
  primaryActionLabel: string;
  secondaryActionLabel: string;
  invalid: boolean;
  invalidReason?: string;
  /** Source card ids represented by this itinerary (one or more for multi-leg). */
  sourceCardIds: string[];
  /** Preferred card for map selection (usually the recommended primary). */
  primaryCardId: string;
}

const COLLAPSE_EVENT_THRESHOLD = 5;

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

/** Split a reason string on middle dots / bullets into short rationale phrases. */
export function parseRationale(reason: string | undefined | null): string[] {
  if (!reason) return [];
  const trimmed = reason.trim();
  if (!trimmed) return [];
  return trimmed
    .split(/\s*[·•|]\s*/)
    .map((part) => part.trim().replace(/[.]+$/, ""))
    .filter(Boolean);
}

function stepRouteId(step: AgentRouteStep): string | null {
  const raw = step.train_line || step.route_id;
  if (!raw || typeof raw !== "string") return null;
  const normalized = raw.trim().toUpperCase();
  return normalized || null;
}

function stepDurationMinutes(step: AgentRouteStep): number | null {
  if (
    typeof step.minutes_until_arrival === "number" &&
    Number.isFinite(step.minutes_until_arrival)
  ) {
    return Math.max(0, Math.round(step.minutes_until_arrival));
  }
  const depart = step.departure_time_iso ? Date.parse(step.departure_time_iso) : NaN;
  const arrive = step.arrival_time_iso ? Date.parse(step.arrival_time_iso) : NaN;
  if (Number.isFinite(depart) && Number.isFinite(arrive) && arrive >= depart) {
    return Math.max(0, Math.round((arrive - depart) / 60_000));
  }
  return null;
}

function lastArrivalIso(steps: AgentRouteStep[]): string | null {
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const iso = steps[i]?.arrival_time_iso;
    if (iso) return iso;
  }
  return null;
}

function firstDepartureIso(steps: AgentRouteStep[], cardDepart?: string): string | null {
  if (cardDepart) return cardDepart;
  for (const step of steps) {
    if (step.departure_time_iso) return step.departure_time_iso;
  }
  return null;
}

function placeTitle(...parts: Array<string | undefined | null>): string {
  const cleaned = parts.map((p) => (p ?? "").trim()).filter(Boolean);
  if (cleaned.length === 0) return "Segment";
  if (cleaned.length === 1) return cleaned[0];
  return `${cleaned[0]} → ${cleaned[cleaned.length - 1]}`;
}

function walkTitle(step: AgentRouteStep, destinationLabel: string, isLast: boolean): string {
  const from = step.departure_stop?.trim();
  const to = step.arrival_stop?.trim() || (isLast ? destinationLabel : undefined);
  if (from && to) return `${from} → ${to}`;
  if (to) return `Walk to ${to}`;
  if (from) return `Walk from ${from}`;
  return isLast ? `Walk to ${destinationLabel}` : "Walk";
}

function transitTitle(step: AgentRouteStep, originFallback: string): string {
  const from = step.departure_stop?.trim() || originFallback;
  const to = step.arrival_stop?.trim();
  if (from && to) return `${from} → ${to}`;
  if (to) return to;
  if (from) return from;
  const line = stepRouteId(step);
  return line ? `${line} train` : "Transit";
}

function durationLabelFromMinutes(minutes: number | null | undefined): string | undefined {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) return undefined;
  return formatDurationMinutes(minutes);
}

/**
 * Collapse consecutive subway/bus steps into one multi-bullet event when they
 * form a pure transfer chain (no intermediate walk between them).
 */
function buildEventsFromSteps(
  steps: AgentRouteStep[],
  originLabel: string,
  destinationLabel: string,
  idPrefix: string,
): ItineraryEvent[] {
  const events: ItineraryEvent[] = [];
  let i = 0;
  let eventIndex = 0;

  while (i < steps.length) {
    const step = steps[i];
    if (!step) {
      i += 1;
      continue;
    }

    if (step.type === "WALK") {
      const minutes = stepDurationMinutes(step);
      events.push({
        id: `${idPrefix}-e${eventIndex++}`,
        kind: "walk",
        routeIds: [],
        title: walkTitle(step, destinationLabel, i === steps.length - 1),
        durationMinutes: minutes ?? undefined,
        durationLabel: durationLabelFromMinutes(minutes),
      });
      i += 1;
      continue;
    }

    if (step.type === "SUBWAY" || step.type === "BUS") {
      const chain: AgentRouteStep[] = [step];
      let j = i + 1;
      // Group consecutive transit steps without an intervening walk.
      while (j < steps.length && (steps[j].type === "SUBWAY" || steps[j].type === "BUS")) {
        chain.push(steps[j]);
        j += 1;
      }

      const routeIds = chain
        .map(stepRouteId)
        .filter((id): id is string => Boolean(id));
      const kind: ItineraryEventKind =
        chain.every((s) => s.type === "BUS") ? "bus" : "subway";

      let totalMinutes: number | null = null;
      for (const s of chain) {
        const m = stepDurationMinutes(s);
        if (m != null) totalMinutes = (totalMinutes ?? 0) + m;
      }

      const first = chain[0];
      const last = chain[chain.length - 1];
      const from =
        first.departure_stop?.trim() ||
        (i === 0 ? originLabel : undefined);
      const to = last.arrival_stop?.trim();

      events.push({
        id: `${idPrefix}-e${eventIndex++}`,
        kind,
        routeIds,
        title: placeTitle(from, to) === "Segment" ? transitTitle(first, originLabel) : placeTitle(from, to),
        durationMinutes: totalMinutes ?? undefined,
        durationLabel: durationLabelFromMinutes(totalMinutes),
      });
      i = j;
      continue;
    }

    i += 1;
  }

  return events;
}

function isValidCard(card: RouteCard): boolean {
  if (!card || typeof card !== "object") return false;
  if (!card.card_id) return false;
  if (!card.destination?.label) return false;
  if (!card.summary || typeof card.summary.eta_minutes !== "number") return false;
  if (!Number.isFinite(card.summary.eta_minutes)) return false;
  return true;
}

function cardArrivalLabel(card: RouteCard): string | null {
  const fromSteps = lastArrivalIso(card.route ?? []);
  if (fromSteps) return formatClockTime(fromSteps);

  const departIso = firstDepartureIso(card.route ?? [], card.depart_iso);
  if (departIso && typeof card.summary.eta_minutes === "number") {
    const start = Date.parse(departIso);
    if (Number.isFinite(start)) {
      const arrive = new Date(start + card.summary.eta_minutes * 60_000);
      return formatClockTime(arrive.toISOString());
    }
  }
  return null;
}

function dwellMinutesBetween(earlier: RouteCard, later: RouteCard): number | null {
  const arriveIso = lastArrivalIso(earlier.route ?? []);
  const departIso = firstDepartureIso(later.route ?? [], later.depart_iso);
  if (!arriveIso || !departIso) return null;
  const arrive = Date.parse(arriveIso);
  const depart = Date.parse(departIso);
  if (!Number.isFinite(arrive) || !Number.isFinite(depart) || depart < arrive) return null;
  const minutes = Math.round((depart - arrive) / 60_000);
  // Only surface meaningful dwell / pickup windows (agent default is 25 min).
  if (minutes < 5) return null;
  return minutes;
}

export function buildItineraryViewModel(
  card: RouteCard,
  options?: {
    primaryActionLabel?: string;
    secondaryActionLabel?: string;
  },
): ItineraryViewModel {
  const primaryActionLabel = options?.primaryActionLabel ?? "Open on map";
  const secondaryActionLabel = options?.secondaryActionLabel ?? "View itinerary";

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

  const originLabel = card.origin?.label?.trim() || "Origin";
  const destinationLabel = card.destination.label.trim();
  const placeNames = [originLabel, destinationLabel];
  const totalMinutes = Math.round(card.summary.eta_minutes);
  const transferCount =
    typeof card.summary.transfers === "number" && Number.isFinite(card.summary.transfers)
      ? Math.max(0, Math.round(card.summary.transfers))
      : 0;

  const events = buildEventsFromSteps(
    Array.isArray(card.route) ? card.route : [],
    originLabel,
    destinationLabel,
    card.card_id,
  );

  const metaParts: string[] = [transferLabel(transferCount)];

  return {
    id: card.card_id,
    recommended: card.role === "recommended",
    placeNames,
    arrivalLabel: cardArrivalLabel(card),
    durationLabel: formatDurationMinutes(totalMinutes),
    totalMinutes,
    transferCount,
    metaParts,
    events,
    rationale: parseRationale(card.summary.reason),
    primaryActionLabel,
    secondaryActionLabel,
    invalid: false,
    sourceCardIds: [card.card_id],
    primaryCardId: card.card_id,
  };
}

/**
 * Merge ordered recommended cards into one multi-stop itinerary when a turn
 * produced multiple legs (pizza first → destination, etc.). Alternatives are
 * left alone for the list renderer.
 */
export function buildMergedItineraryViewModel(
  recommendedCards: RouteCard[],
  options?: {
    primaryActionLabel?: string;
    secondaryActionLabel?: string;
  },
): ItineraryViewModel | null {
  if (recommendedCards.length === 0) return null;
  if (recommendedCards.length === 1) {
    return buildItineraryViewModel(recommendedCards[0], options);
  }

  const primaryActionLabel = options?.primaryActionLabel ?? "Open on map";
  const secondaryActionLabel = options?.secondaryActionLabel ?? "View itinerary";

  const valid = recommendedCards.filter(isValidCard);
  if (valid.length === 0) {
    return buildItineraryViewModel(recommendedCards[0], options);
  }

  const placeNames: string[] = [];
  const first = valid[0];
  const last = valid[valid.length - 1];
  placeNames.push(first.origin?.label?.trim() || "Origin");
  for (const card of valid) {
    const dest = card.destination.label.trim();
    if (placeNames[placeNames.length - 1] !== dest) placeNames.push(dest);
  }

  const events: ItineraryEvent[] = [];
  let totalMinutes = 0;
  let transferCount = 0;
  let pickupMinutesTotal = 0;
  const rationale: string[] = [];
  const sourceCardIds: string[] = [];

  for (let i = 0; i < valid.length; i += 1) {
    const card = valid[i];
    sourceCardIds.push(card.card_id);
    totalMinutes += Math.round(card.summary.eta_minutes);
    transferCount +=
      typeof card.summary.transfers === "number" && Number.isFinite(card.summary.transfers)
        ? Math.max(0, Math.round(card.summary.transfers))
        : 0;

    const originLabel = card.origin?.label?.trim() || placeNames[i] || "Origin";
    const destinationLabel = card.destination.label.trim();
    const legEvents = buildEventsFromSteps(
      Array.isArray(card.route) ? card.route : [],
      originLabel,
      destinationLabel,
      card.card_id,
    );
    events.push(...legEvents);

    if (i < valid.length - 1) {
      const next = valid[i + 1];
      const dwell = dwellMinutesBetween(card, next);
      if (dwell != null) {
        pickupMinutesTotal += dwell;
        events.push({
          id: `${card.card_id}-pickup-${i}`,
          kind: "pickup",
          routeIds: [],
          title: "Pickup",
          subtitle: destinationLabel,
          durationMinutes: dwell,
          durationLabel: formatDurationMinutes(dwell),
        });
        totalMinutes += dwell;
      }
    }

    for (const phrase of parseRationale(card.summary.reason)) {
      if (!rationale.includes(phrase)) rationale.push(phrase);
    }
  }

  const metaParts = [transferLabel(transferCount)];
  if (pickupMinutesTotal > 0) {
    metaParts.push(`${formatDurationMinutes(pickupMinutesTotal)} pickup`);
  }

  // Prefer the last leg's arrival (final destination).
  const arrivalLabel = cardArrivalLabel(last);

  return {
    id: sourceCardIds.join("+"),
    recommended: true,
    placeNames,
    arrivalLabel,
    durationLabel: formatDurationMinutes(totalMinutes),
    totalMinutes,
    transferCount,
    metaParts,
    events,
    rationale,
    primaryActionLabel,
    secondaryActionLabel,
    invalid: false,
    sourceCardIds,
    primaryCardId: last.card_id,
  };
}

export function shouldCollapseEvents(eventCount: number): boolean {
  return eventCount > COLLAPSE_EVENT_THRESHOLD;
}

export function isSupportedSubwayRoute(routeId: string): boolean {
  return SUBWAY_BULLET_ROUTES.has(routeId.trim().toUpperCase());
}

/** Dev-only warning for unsupported route IDs (does not throw). */
export function warnUnsupportedRouteId(routeId: string): void {
  if (process.env.NODE_ENV === "production") return;
  if (isSupportedSubwayRoute(routeId)) return;
  // eslint-disable-next-line no-console
  console.warn(`[itinerary-card] unsupported subway route id "${routeId}"`);
}
