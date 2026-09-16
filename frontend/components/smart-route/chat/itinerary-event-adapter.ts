import type {
  CanonicalAccessibility,
  CanonicalItinerary,
  CanonicalItineraryLeg,
  CanonicalTransferKind,
  CanonicalTransferSemantics,
} from "@/lib/agent-chat/route-card-contract";
import {
  canonicalPlaceLabel,
  canonicalStopLabel,
} from "@/lib/canonical-itinerary-label";

export { canonicalPlaceLabel } from "@/lib/canonical-itinerary-label";

export type ItineraryEventKind =
  | "subway"
  | "bus"
  | "rail"
  | "walk"
  | "wait"
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
  durationSeconds?: number;
  durationLabel?: string;
  durationMinutes?: number;
  fromLabel?: string;
  toLabel?: string;
  stopCount?: number;
  stops?: string[];
  sourceLabel?: string;
  transferKind?: CanonicalTransferKind;
  accessibility?: CanonicalAccessibility;
}

const MIN_INFERRED_WALK_SECONDS = 90;

export function formatDurationMinutes(totalMinutes: number): string {
  if (!Number.isFinite(totalMinutes) || totalMinutes < 0) return "—";
  const minutes = Math.round(totalMinutes);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours} hr ${rest} min` : `${hours} hr`;
}

export function durationMinutesFromSeconds(
  seconds: number | null | undefined,
): number | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) {
    return null;
  }
  return Math.round(seconds / 60);
}

function durationLabelFromMinutes(minutes: number | null): string | undefined {
  return minutes == null ? undefined : formatDurationMinutes(minutes);
}

function durationLabelFromSeconds(seconds: number): string {
  return formatDurationMinutes(Math.max(1, Math.round(seconds / 60)));
}

function durationSecondsFromTransfer(
  semantics: CanonicalTransferSemantics | null | undefined,
): number | null {
  const seconds = semantics?.total_seconds;
  return seconds !== undefined && Number.isFinite(seconds) && seconds >= 0
    ? seconds
    : null;
}

function canonicalLegDurationSeconds(leg: CanonicalItineraryLeg): number | null {
  if (
    leg.transfer_kind &&
    leg.transfer_kind !== "street_transfer" &&
    leg.transfer_kind !== "ordinary_walk"
  ) {
    return durationSecondsFromTransfer(leg.transfer_semantics);
  }
  const value =
    leg.mode.trim().toUpperCase() === "WALK" ? leg.walk_seconds : leg.ride_seconds;
  return value !== undefined && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function canonicalLegStops(leg: CanonicalItineraryLeg): string[] {
  if (!Array.isArray(leg.stops)) return [];
  return leg.stops
    .map((stop) => canonicalStopLabel(stop))
    .filter((stop): stop is string => Boolean(stop));
}

export function intermediateStopNames(event: {
  stops?: string[];
  fromLabel?: string;
  toLabel?: string;
}): string[] {
  const stops = [...(event.stops ?? [])];
  if (event.fromLabel && stops[0] === event.fromLabel) stops.shift();
  if (event.toLabel && stops.at(-1) === event.toLabel) stops.pop();
  return stops;
}

function transferRouteIds(semantics: CanonicalTransferSemantics): string[] {
  const routeId = semantics.to_route_id?.trim().toUpperCase();
  return routeId ? [routeId] : [];
}

function transferTitle(semantics: CanonicalTransferSemantics): string {
  const routeId = semantics.to_route_id?.trim();
  return routeId ? `Transfer to the ${routeId}` : "Transfer";
}

function transferSubtitle(
  semantics: CanonicalTransferSemantics,
  durationSeconds: number | null,
): string {
  let location = "Transfer";
  if (semantics.kind === "station_complex") location = "Station complex";
  else if (semantics.kind === "same_platform" || semantics.kind === "same_station") {
    location = "Same station";
  }

  const duration =
    durationSeconds == null
      ? null
      : `about ${durationLabelFromSeconds(durationSeconds)}`;
  let accessibility: string | null = null;
  if (semantics.accessibility === "unknown") accessibility = "Accessibility unknown";
  else if (semantics.accessibility === "inaccessible") {
    accessibility = "Accessibility unavailable";
  }
  return [location, duration, accessibility].filter(Boolean).join(" · ");
}

function transitEventKind(mode: string): ItineraryEventKind | null {
  if (mode === "BUS") return "bus";
  if (mode === "SUBWAY") return "subway";
  if (["RAIL", "TRAIN", "LIGHT_RAIL", "TRAM"].includes(mode)) return "rail";
  return null;
}

function isSemanticWalkTransfer(
  mode: string,
  semantics: CanonicalTransferSemantics | null | undefined,
): semantics is CanonicalTransferSemantics {
  return (
    mode === "WALK"
    && semantics != null
    && semantics.kind !== "street_transfer"
    && semantics.kind !== "ordinary_walk"
  );
}

function stopCountFromLeg(leg: CanonicalItineraryLeg): number | undefined {
  if (leg.stop_count === null || leg.stop_count === undefined) return undefined;
  if (!Number.isFinite(leg.stop_count)) return undefined;
  return Math.max(0, Math.round(leg.stop_count));
}

function waitSecondsFromLeg(leg: CanonicalItineraryLeg): number {
  if (leg.wait_seconds === undefined || !Number.isFinite(leg.wait_seconds)) return 0;
  return Math.max(0, leg.wait_seconds);
}

function firstPresentLabel(
  ...values: Array<string | null | undefined>
): string | undefined {
  for (const value of values) {
    if (value) return value;
  }
  return undefined;
}

function legEndpoints(
  transfer: boolean,
  semantics: CanonicalTransferSemantics | undefined,
  leg: CanonicalItineraryLeg,
  stops: string[],
) {
  return {
    fromLabel: firstPresentLabel(
      transfer ? canonicalStopLabel(semantics?.from_station_label) : null,
      canonicalStopLabel(leg.board),
      stops[0],
    ),
    toLabel: firstPresentLabel(
      transfer ? canonicalStopLabel(semantics?.to_station_label) : null,
      canonicalStopLabel(leg.alight),
      stops.at(-1),
    ),
  };
}

function pushWaitEvent(
  events: ItineraryEvent[],
  idPrefix: string,
  index: number,
  serviceId: string,
  waitSeconds: number,
  fromLabel: string | undefined,
): void {
  if (waitSeconds <= 0) return;
  const waitMinutes = durationMinutesFromSeconds(waitSeconds);
  events.push({
    id: `${idPrefix}-${index}-wait`,
    kind: "wait",
    routeIds: serviceId ? [serviceId] : [],
    title: serviceId ? `Wait for ${serviceId}` : "Wait to board",
    subtitle: fromLabel,
    durationSeconds: waitSeconds,
    durationMinutes: waitMinutes ?? undefined,
    durationLabel: durationLabelFromMinutes(waitMinutes),
    fromLabel,
  });
}

function pushCanonicalLeg(
  events: ItineraryEvent[],
  leg: CanonicalItineraryLeg,
  index: number,
  segmentDestination: string,
  idPrefix: string,
): void {
  const mode = leg.mode.trim().toUpperCase();
  const semantics = leg.transfer_semantics;
  const transfer = isSemanticWalkTransfer(mode, semantics);
  const stops = canonicalLegStops(leg);
  const { fromLabel, toLabel } = legEndpoints(transfer, semantics ?? undefined, leg, stops);
  const durationSeconds = canonicalLegDurationSeconds(leg);
  const durationMinutes = durationMinutesFromSeconds(durationSeconds);
  const base = {
    id: `${idPrefix}-${index}`,
    title: toLabel || fromLabel || segmentDestination,
    durationSeconds: durationSeconds ?? undefined,
    durationMinutes: durationMinutes ?? undefined,
    durationLabel: durationLabelFromMinutes(durationMinutes),
    fromLabel,
    toLabel,
  };

  if (transfer) {
    events.push({
      ...base,
      kind: "transfer",
      title: transferTitle(semantics),
      subtitle: transferSubtitle(semantics, durationSeconds),
      routeIds: transferRouteIds(semantics),
      transferKind: semantics.kind,
      accessibility: semantics.accessibility,
    });
    return;
  }
  if (mode === "WALK") {
    events.push({ ...base, kind: "walk", routeIds: [] });
    return;
  }
  const kind = transitEventKind(mode);
  if (!kind) return;
  const serviceId = leg.service_id?.trim().toUpperCase() ?? "";
  pushWaitEvent(events, idPrefix, index, serviceId, waitSecondsFromLeg(leg), fromLabel);
  events.push({
    ...base,
    kind,
    routeIds: serviceId ? [serviceId] : [],
    stopCount: stopCountFromLeg(leg),
    stops,
  });
}

function appendCanonicalLegs(
  events: ItineraryEvent[],
  legs: CanonicalItineraryLeg[],
  segmentDestination: string,
  idPrefix: string,
): void {
  for (const [index, leg] of legs.entries()) {
    pushCanonicalLeg(events, leg, index, segmentDestination, idPrefix);
  }
}

function eventBoundaryLabel(
  event: ItineraryEvent | undefined,
  edge: "start" | "end",
): string | undefined {
  if (!event) return undefined;
  if (edge === "start") {
    return event.fromLabel ?? (event.kind === "waypoint" ? event.title : undefined);
  }
  return event.toLabel ?? (event.kind === "waypoint" ? event.title : undefined);
}

function labelsMatch(a: string | undefined, b: string | undefined): boolean {
  if (!a || !b) return false;
  return a.trim().toLocaleLowerCase() === b.trim().toLocaleLowerCase();
}

function walkGroupDurationSeconds(events: ItineraryEvent[]): number {
  return events.reduce((total, event) => {
    if (event.durationSeconds !== undefined && Number.isFinite(event.durationSeconds)) {
      return total + Math.max(0, event.durationSeconds);
    }
    if (event.durationMinutes !== undefined && Number.isFinite(event.durationMinutes)) {
      return total + Math.max(0, event.durationMinutes) * 60;
    }
    return total;
  }, 0);
}

function walkSectionEndpoints(
  group: ItineraryEvent[],
  previous: ItineraryEvent | undefined,
  next: ItineraryEvent | undefined,
  atStart: boolean,
  atEnd: boolean,
  originLabel: string,
  destinationLabel: string,
) {
  const explicitFrom = group.find((item) => item.fromLabel)?.fromLabel;
  const explicitTo = [...group].reverse().find((item) => item.toLabel)?.toLabel;
  return {
    fromLabel: firstPresentLabel(
      explicitFrom,
      eventBoundaryLabel(previous, "end"),
      atStart ? originLabel : undefined,
    ),
    toLabel: firstPresentLabel(
      explicitTo,
      eventBoundaryLabel(next, "start"),
      atEnd ? destinationLabel : undefined,
    ),
    hasExplicitIdentity: Boolean(explicitFrom || explicitTo),
  };
}

function walkSectionFromGroup(
  group: ItineraryEvent[],
  previous: ItineraryEvent | undefined,
  next: ItineraryEvent | undefined,
  atStart: boolean,
  atEnd: boolean,
  originLabel: string,
  destinationLabel: string,
): ItineraryEvent | null {
  const durationSeconds = walkGroupDurationSeconds(group);
  const { fromLabel, toLabel, hasExplicitIdentity } = walkSectionEndpoints(
    group,
    previous,
    next,
    atStart,
    atEnd,
    originLabel,
    destinationLabel,
  );
  if (durationSeconds <= 0) return null;
  if (labelsMatch(fromLabel, toLabel)) return null;
  if (!hasExplicitIdentity && durationSeconds < MIN_INFERRED_WALK_SECONDS) return null;
  if (!fromLabel && !toLabel) return null;
  const durationMinutes = Math.max(1, Math.round(durationSeconds / 60));
  return {
    id: `${group[0].id}-walk-section`,
    kind: "walk",
    routeIds: [],
    title: toLabel ?? fromLabel ?? "Walk",
    fromLabel,
    toLabel,
    durationSeconds,
    durationMinutes,
    durationLabel: durationLabelFromSeconds(durationSeconds),
  };
}

export function condensePreviewEvents(
  events: ItineraryEvent[],
  destinationLabel: string,
  originLabel = "Your location",
): ItineraryEvent[] {
  const sections: ItineraryEvent[] = [];
  let index = 0;

  while (index < events.length) {
    const event = events[index];
    if (event.kind !== "walk") {
      sections.push({ ...event });
      index += 1;
      continue;
    }

    let end = index + 1;
    while (end < events.length && events[end].kind === "walk") end += 1;
    const section = walkSectionFromGroup(
      events.slice(index, end),
      sections.at(-1),
      events[end],
      index === 0,
      end === events.length,
      originLabel,
      destinationLabel,
    );
    if (section) sections.push(section);
    index = end;
  }
  return sections;
}

export function buildEventsFromCanonicalItinerary(
  itinerary: CanonicalItinerary,
  originLabel: string,
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
          subtitle:
            minutes == null ? "Planned stop" : `${formatDurationMinutes(minutes)} stop`,
          durationMinutes: minutes ?? undefined,
          durationLabel: durationLabelFromMinutes(minutes),
          sourceLabel: dwell.source === "user" ? "Requested stop" : "Planned stop",
        });
      });
    return condensePreviewEvents(events, destinationLabel, originLabel);
  }

  appendCanonicalLegs(
    events,
    Array.isArray(itinerary.legs) ? itinerary.legs : [],
    destinationLabel,
    `${idPrefix}-direct`,
  );
  return condensePreviewEvents(events, destinationLabel, originLabel);
}
