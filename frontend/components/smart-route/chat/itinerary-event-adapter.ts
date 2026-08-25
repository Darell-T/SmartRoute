import type {
  CanonicalAccessibility,
  CanonicalItinerary,
  CanonicalItineraryLeg,
  CanonicalTransferKind,
  CanonicalTransferSemantics,
} from "@/lib/agent-route-card-contract";

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

export function durationMinutesFromSeconds(seconds: unknown): number | null {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
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

export function canonicalPlaceLabel(place: unknown, fallback: string): string {
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

function durationSecondsFromTransfer(
  semantics: CanonicalTransferSemantics | null | undefined,
): number | null {
  const seconds = semantics?.total_seconds;
  return typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 0
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
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function canonicalLegStops(leg: CanonicalItineraryLeg): string[] {
  if (!Array.isArray(leg.stops)) return [];
  return leg.stops
    .map((stop) => canonicalStopLabel(stop))
    .filter((stop): stop is string => Boolean(stop));
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

function appendCanonicalLegs(
  events: ItineraryEvent[],
  legs: CanonicalItineraryLeg[],
  segmentDestination: string,
  idPrefix: string,
): void {
  legs.forEach((leg, index) => {
    const mode = leg.mode.trim().toUpperCase();
    const semantics = leg.transfer_semantics;
    const isSemanticTransfer =
      mode === "WALK" &&
      semantics != null &&
      semantics.kind !== "street_transfer" &&
      semantics.kind !== "ordinary_walk";
    const stops = canonicalLegStops(leg);
    const fromLabel =
      (isSemanticTransfer ? canonicalStopLabel(semantics?.from_station_label) : null) ??
      canonicalStopLabel(leg.board) ??
      stops[0] ??
      undefined;
    const toLabel =
      (isSemanticTransfer ? canonicalStopLabel(semantics?.to_station_label) : null) ??
      canonicalStopLabel(leg.alight) ??
      stops.at(-1) ??
      undefined;
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

    if (isSemanticTransfer && semantics) {
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
    const isRail = ["RAIL", "TRAIN", "LIGHT_RAIL", "TRAM"].includes(mode);
    if (mode !== "SUBWAY" && mode !== "BUS" && !isRail) return;

    const serviceId =
      typeof leg.service_id === "string" ? leg.service_id.trim().toUpperCase() : "";
    let kind: ItineraryEventKind;
    if (mode === "BUS") {
      kind = "bus";
    } else if (isRail) {
      kind = "rail";
    } else {
      kind = "subway";
    }
    const waitSeconds =
      typeof leg.wait_seconds === "number" &&
      Number.isFinite(leg.wait_seconds) &&
      leg.wait_seconds > 0
        ? leg.wait_seconds
        : 0;
    if (waitSeconds > 0) {
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
    events.push({
      ...base,
      kind,
      routeIds: serviceId ? [serviceId] : [],
      stopCount:
        typeof leg.stop_count === "number" && Number.isFinite(leg.stop_count)
          ? Math.max(0, Math.round(leg.stop_count))
          : undefined,
      stops,
    });
  });
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
    if (typeof event.durationSeconds === "number" && Number.isFinite(event.durationSeconds)) {
      return total + Math.max(0, event.durationSeconds);
    }
    if (typeof event.durationMinutes === "number" && Number.isFinite(event.durationMinutes)) {
      return total + Math.max(0, event.durationMinutes) * 60;
    }
    return total;
  }, 0);
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
    const group = events.slice(index, end);
    const durationSeconds = walkGroupDurationSeconds(group);
    const explicitFrom = group.find((item) => item.fromLabel)?.fromLabel;
    const explicitTo = [...group].reverse().find((item) => item.toLabel)?.toLabel;
    const fromLabel =
      explicitFrom ??
      eventBoundaryLabel(sections.at(-1), "end") ??
      (index === 0 ? originLabel : undefined);
    const toLabel =
      explicitTo ??
      eventBoundaryLabel(events[end], "start") ??
      (end === events.length ? destinationLabel : undefined);
    const hasExplicitIdentity = Boolean(explicitFrom || explicitTo);
    const isInternalSamePlaceTransfer = labelsMatch(fromLabel, toLabel);
    const isUnlabeledMicroFragment =
      !hasExplicitIdentity && durationSeconds < MIN_INFERRED_WALK_SECONDS;

    if (
      durationSeconds > 0 &&
      !isInternalSamePlaceTransfer &&
      !isUnlabeledMicroFragment &&
      (fromLabel || toLabel)
    ) {
      const durationMinutes = Math.max(1, Math.round(durationSeconds / 60));
      sections.push({
        id: `${group[0].id}-walk-section`,
        kind: "walk",
        routeIds: [],
        title: toLabel ?? fromLabel ?? "Walk",
        fromLabel,
        toLabel,
        durationSeconds,
        durationMinutes,
        durationLabel: durationLabelFromSeconds(durationSeconds),
      });
    }
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
