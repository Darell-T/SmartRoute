/**
 * Pure adapter: RouteCard (agent SSE shape) → compact itinerary preview model.
 *
 * Formats display values from supplied itinerary facts. The chat card shows a
 * curated preview (key journey chunks), not a full expanded step dump.
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
  /** Primary line under bullets, e.g. "Sunday Morning" or "Walk to Prada". */
  title: string;
  /** Secondary line when needed (e.g. "25 min pickup"). */
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
  /** Condensed preview rows for the chat card (max ~5). */
  events: ItineraryEvent[];
  /** Rationale phrases from route selection; omit section when empty. */
  rationale: string[];
  primaryActionLabel: string;
  secondaryActionLabel: string;
  invalid: boolean;
  invalidReason?: string;
  sourceCardIds: string[];
  primaryCardId: string;
}

/** Hard cap for inline chat preview rows. */
export const PREVIEW_EVENT_MAX = 5;

/** Walks shorter than this (minutes) are omitted unless they are the final approach. */
const SHORT_WALK_THRESHOLD_MIN = 5;

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

function durationLabelFromMinutes(minutes: number | null | undefined): string | undefined {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) return undefined;
  return formatDurationMinutes(minutes);
}

/**
 * Build raw leg events from steps, then condense into a curated preview.
 * Consecutive transit steps collapse to one multi-bullet row.
 */
function buildEventsFromSteps(
  steps: AgentRouteStep[],
  originLabel: string,
  destinationLabel: string,
  idPrefix: string,
): ItineraryEvent[] {
  const raw: ItineraryEvent[] = [];
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
      const isLast = i === steps.length - 1;
      const to = step.arrival_stop?.trim() || (isLast ? destinationLabel : undefined);
      raw.push({
        id: `${idPrefix}-e${eventIndex++}`,
        kind: "walk",
        routeIds: [],
        title: isLast || to === destinationLabel
          ? `Walk to ${destinationLabel}`
          : to
            ? `Walk to ${to}`
            : "Walk",
        durationMinutes: minutes ?? undefined,
        durationLabel: durationLabelFromMinutes(minutes),
      });
      i += 1;
      continue;
    }

    if (step.type === "SUBWAY" || step.type === "BUS") {
      const chain: AgentRouteStep[] = [step];
      let j = i + 1;
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
      // Compact place label: arrival of the chunk (mock-style), not full path.
      const place =
        last.arrival_stop?.trim() ||
        first.departure_stop?.trim() ||
        originLabel;

      raw.push({
        id: `${idPrefix}-e${eventIndex++}`,
        kind,
        routeIds,
        title: place,
        durationMinutes: totalMinutes ?? undefined,
        durationLabel: durationLabelFromMinutes(totalMinutes),
      });
      i = j;
      continue;
    }

    i += 1;
  }

  return condensePreviewEvents(raw, destinationLabel);
}

/**
 * Curate a chat-card preview from raw events:
 * - nest pickup/dwell under the preceding transit row
 * - drop short non-final walks (micro fragments)
 * - keep starting walk only when substantial
 * - keep main transit, meaningful transfer chains, final walk
 * - hard-cap at PREVIEW_EVENT_MAX rows
 */
export function condensePreviewEvents(
  events: ItineraryEvent[],
  destinationLabel: string,
): ItineraryEvent[] {
  if (events.length === 0) return [];

  // 1) Nest pickup into the previous transit row (mock: place + "25 min pickup").
  const nested: ItineraryEvent[] = [];
  for (const event of events) {
    if (event.kind === "pickup" && nested.length > 0) {
      const prev = nested[nested.length - 1];
      if (prev.kind === "subway" || prev.kind === "bus") {
        nested[nested.length - 1] = {
          ...prev,
          title: event.subtitle?.trim() || prev.title,
          subtitle: event.durationLabel
            ? `${event.durationLabel} pickup`
            : "Pickup",
        };
        continue;
      }
    }
    nested.push({ ...event });
  }

  // 2) Drop short / intermediate walks; keep a meaningful lead walk and final approach.
  const curated: ItineraryEvent[] = [];
  for (let i = 0; i < nested.length; i += 1) {
    const event = nested[i];
    const isLast = i === nested.length - 1;
    const mins = event.durationMinutes ?? 0;

    if (event.kind === "walk") {
      if (isLast) {
        curated.push({
          ...event,
          title: `Walk to ${destinationLabel}`,
        });
        continue;
      }

      // Starting walk: keep only when substantial.
      const isLead = curated.length === 0;
      if (isLead && mins >= SHORT_WALK_THRESHOLD_MIN) {
        curated.push({
          ...event,
          title: mins >= SHORT_WALK_THRESHOLD_MIN ? event.title : "Walk",
        });
      }
      // Intermediate short walks (transfer connectors) are dropped.
      continue;
    }

    if (event.kind === "pickup") {
      // Orphan pickup (no prior transit): keep as its own compact row.
      curated.push(event);
      continue;
    }

    curated.push(event);
  }

  // 3) If we still only have walks and one is tiny lead + final, leave final only.
  if (
    curated.length >= 2 &&
    curated.every((e) => e.kind === "walk") &&
    (curated[0].durationMinutes ?? 0) < SHORT_WALK_THRESHOLD_MIN
  ) {
    return curated.slice(-1).slice(0, PREVIEW_EVENT_MAX);
  }

  return curated.slice(0, PREVIEW_EVENT_MAX);
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
  // Prefer canonical itinerary arrival; never invent depart+eta when present.
  const itineraryArrival = card.itinerary?.arrival_at;
  if (typeof itineraryArrival === "string" && itineraryArrival.trim()) {
    return formatClockTime(itineraryArrival);
  }

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

/** Hero total minutes: itinerary seconds first, else legacy summary ETA. */
function cardTotalMinutes(card: RouteCard): number {
  const seconds = card.itinerary?.total_duration_seconds;
  if (typeof seconds === "number" && Number.isFinite(seconds)) {
    return Math.round(seconds / 60);
  }
  return Math.round(card.summary.eta_minutes);
}

/** Transfer count: itinerary first, else summary. */
function cardTransferCount(card: RouteCard): number {
  const fromItin = card.itinerary?.transfer_count;
  if (typeof fromItin === "number" && Number.isFinite(fromItin)) {
    return Math.max(0, Math.round(fromItin));
  }
  if (typeof card.summary.transfers === "number" && Number.isFinite(card.summary.transfers)) {
    return Math.max(0, Math.round(card.summary.transfers));
  }
  return 0;
}

function dwellMinutesBetween(earlier: RouteCard, later: RouteCard): number | null {
  const arriveIso = lastArrivalIso(earlier.route ?? []);
  const departIso = firstDepartureIso(later.route ?? [], later.depart_iso);
  if (!arriveIso || !departIso) return null;
  const arrive = Date.parse(arriveIso);
  const depart = Date.parse(departIso);
  if (!Number.isFinite(arrive) || !Number.isFinite(depart) || depart < arrive) return null;
  const minutes = Math.round((depart - arrive) / 60_000);
  if (minutes < 5) return null;
  return minutes;
}

function buildMetaParts(transferCount: number, pickupMinutesTotal: number): string[] {
  const parts: string[] = [];
  if (transferCount > 0) parts.push(transferLabel(transferCount));
  if (pickupMinutesTotal > 0) {
    parts.push(`${formatDurationMinutes(pickupMinutesTotal)} pickup`);
  }
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

  const originLabel = card.origin?.label?.trim() || "Origin";
  const destinationLabel = card.destination.label.trim();
  const placeNames = [originLabel, destinationLabel];
  const totalMinutes = cardTotalMinutes(card);
  const transferCount = cardTransferCount(card);

  const events = buildEventsFromSteps(
    Array.isArray(card.route) ? card.route : [],
    originLabel,
    destinationLabel,
    card.card_id,
  );

  return {
    id: card.card_id,
    recommended: card.role === "recommended",
    placeNames,
    arrivalLabel: cardArrivalLabel(card),
    durationLabel: formatDurationMinutes(totalMinutes),
    totalMinutes,
    transferCount,
    metaParts: buildMetaParts(transferCount, 0),
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
 * Merge ordered recommended cards into one multi-stop itinerary preview.
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
  const secondaryActionLabel = options?.secondaryActionLabel ?? "View steps";

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

  // Build raw (pre-condense) events across legs so pickup nesting works.
  const rawEvents: ItineraryEvent[] = [];
  let totalMinutes = 0;
  let transferCount = 0;
  let pickupMinutesTotal = 0;
  const rationale: string[] = [];
  const sourceCardIds: string[] = [];
  let eventIndex = 0;

  for (let i = 0; i < valid.length; i += 1) {
    const card = valid[i];
    sourceCardIds.push(card.card_id);
    totalMinutes += cardTotalMinutes(card);
    transferCount += cardTransferCount(card);

    const originLabel = card.origin?.label?.trim() || placeNames[i] || "Origin";
    const destinationLabel = card.destination.label.trim();

    // Use uncondensed step builder pieces via a private path:
    // buildEventsFromSteps already condenses; for multi-leg we need pickups
    // inserted before the final condense. Reconstruct lightly from route.
    const legRaw = buildRawEventsFromSteps(
      Array.isArray(card.route) ? card.route : [],
      originLabel,
      destinationLabel,
      card.card_id,
      eventIndex,
    );
    eventIndex += legRaw.length;
    rawEvents.push(...legRaw);

    if (i < valid.length - 1) {
      const next = valid[i + 1];
      const dwell = dwellMinutesBetween(card, next);
      if (dwell != null) {
        pickupMinutesTotal += dwell;
        rawEvents.push({
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

  const finalDestination = last.destination.label.trim();
  const events = condensePreviewEvents(rawEvents, finalDestination);

  return {
    id: sourceCardIds.join("+"),
    recommended: true,
    placeNames,
    arrivalLabel: cardArrivalLabel(last),
    durationLabel: formatDurationMinutes(totalMinutes),
    totalMinutes,
    transferCount,
    metaParts: buildMetaParts(transferCount, pickupMinutesTotal),
    events,
    rationale,
    primaryActionLabel,
    secondaryActionLabel,
    invalid: false,
    sourceCardIds,
    primaryCardId: last.card_id,
  };
}

/** Uncondensed event list used when stitching multi-leg journeys. */
function buildRawEventsFromSteps(
  steps: AgentRouteStep[],
  originLabel: string,
  destinationLabel: string,
  idPrefix: string,
  startIndex: number,
): ItineraryEvent[] {
  const raw: ItineraryEvent[] = [];
  let i = 0;
  let eventIndex = startIndex;

  while (i < steps.length) {
    const step = steps[i];
    if (!step) {
      i += 1;
      continue;
    }

    if (step.type === "WALK") {
      const minutes = stepDurationMinutes(step);
      const isLast = i === steps.length - 1;
      raw.push({
        id: `${idPrefix}-e${eventIndex++}`,
        kind: "walk",
        routeIds: [],
        title: isLast ? `Walk to ${destinationLabel}` : "Walk",
        durationMinutes: minutes ?? undefined,
        durationLabel: durationLabelFromMinutes(minutes),
      });
      i += 1;
      continue;
    }

    if (step.type === "SUBWAY" || step.type === "BUS") {
      const chain: AgentRouteStep[] = [step];
      let j = i + 1;
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

      const last = chain[chain.length - 1];
      const place =
        last.arrival_stop?.trim() ||
        chain[0].departure_stop?.trim() ||
        originLabel ||
        destinationLabel;

      raw.push({
        id: `${idPrefix}-e${eventIndex++}`,
        kind,
        routeIds,
        title: place,
        durationMinutes: totalMinutes ?? undefined,
        durationLabel: durationLabelFromMinutes(totalMinutes),
      });
      i = j;
      continue;
    }

    i += 1;
  }

  return raw;
}

export function shouldCollapseEvents(eventCount: number): boolean {
  return eventCount > PREVIEW_EVENT_MAX;
}

export function isSupportedSubwayRoute(routeId: string): boolean {
  return SUBWAY_BULLET_ROUTES.has(routeId.trim().toUpperCase());
}

export function warnUnsupportedRouteId(routeId: string): void {
  if (process.env.NODE_ENV === "production") return;
  if (isSupportedSubwayRoute(routeId)) return;
  // eslint-disable-next-line no-console
  console.warn(`[itinerary-card] unsupported subway route id "${routeId}"`);
}
