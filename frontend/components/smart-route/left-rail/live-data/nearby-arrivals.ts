import type { LiveFeedResponse } from "@/types/api";
import type {
  Arrival,
  NearbyGroupedArrival,
  NearbyTransitDirection,
  NearbyTransitGroup,
  Next5Entry,
} from "../types";
import { HALF_MILE_METERS } from "./constants";
import {
  cleanDestinationLabel,
  labelForArrivalMinutes,
  labelForMinutes,
  minutesUntilArrival,
  splitBusHeadsign,
} from "./formatters";
import {
  compareRouteId,
  normalizeRouteId,
  ROUTE_DESTINATION_FALLBACKS,
  ROUTE_SERVICE_PATTERNS,
} from "./route-metadata";
import type { ArrivalRows } from "./types";

function normalizeWay(direction: unknown, stopId?: string): NearbyTransitDirection {
  const label = String(direction ?? "").toLowerCase();
  if (
    label.includes("downtown")
    || label.includes("south")
    || label.includes("outbound")
  ) {
    return "downtown";
  }
  if (
    label.includes("uptown")
    || label.includes("north")
    || label.includes("inbound")
  ) {
    return "uptown";
  }
  const suffix = String(stopId ?? "").slice(-1).toUpperCase();
  if (suffix === "S") return "downtown";
  if (suffix === "N") return "uptown";
  return "unknown";
}

function busWay(stopCompass: unknown, direction: unknown): NearbyTransitDirection {
  // OBA bus stops carry a compass heading (NE, SW, E, ...). Manhattan's
  // grid tilt means uptown service reads as N/NE/NW and downtown as
  // S/SE/SW. Pure E/W and numeric BusTime directions are not passenger
  // destinations, so they remain unknown and are shown under all directions.
  const compass = String(stopCompass ?? "").toUpperCase();
  if (compass.includes("N")) return "uptown";
  if (compass.includes("S")) return "downtown";
  return normalizeWay(direction) === "unknown" ? "unknown" : normalizeWay(direction);
}

/* GTFS abbreviations that keep their transit spelling when title-cased. */

function isDirectionOnlyDestination(value: string, direction: NearbyTransitDirection): boolean {
  const clean = value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const directionLabels =
    direction === "uptown"
      ? ["uptown", "northbound", "inbound", "uptown bound", "north bound"]
      : direction === "downtown"
        ? ["downtown", "southbound", "outbound", "downtown bound", "south bound"]
        : ["unknown"];
  return directionLabels.includes(clean);
}

function destinationForArrival(
  arrival: Record<string, unknown>,
  line: string,
  direction: NearbyTransitDirection,
  mode: "subway" | "bus",
): string {
  const candidates = [
    arrival.terminal_stop_name,
    arrival.trip_headsign,
    arrival.headsign,
    arrival.destination,
    arrival.destination_name,
  ]
    .map(cleanDestinationLabel)
    .filter(Boolean);

  const passengerFacing = candidates.find(
    (candidate) => !isDirectionOnlyDestination(candidate, direction),
  );
  if (passengerFacing) return passengerFacing;

  if (mode === "subway" && direction !== "unknown") {
    return ROUTE_DESTINATION_FALLBACKS[line]?.[direction] ?? "Terminal";
  }

  return mode === "bus" ? "Route terminal" : "Terminal";
}

function servicePatternForArrival(
  arrival: Record<string, unknown>,
  line: string,
  mode: "subway" | "bus",
): string | undefined {
  if (mode === "bus") return undefined;
  const raw = [
    arrival.service_pattern,
    arrival.route_long_name,
    arrival.line_name,
    arrival.route_name,
  ]
    .map(cleanDestinationLabel)
    .find(Boolean);
  return raw ?? ROUTE_SERVICE_PATTERNS[line];
}

function walkMinutesForDistance(meters: number | undefined): number | undefined {
  if (typeof meters !== "number" || !Number.isFinite(meters)) return undefined;
  return Math.max(1, Math.round(meters / 84));
}

function distanceMilesForMeters(meters: number | undefined): number | undefined {
  if (typeof meters !== "number" || !Number.isFinite(meters)) return undefined;
  return Number(Math.max(0.1, meters / 1609.344).toFixed(1));
}

function stationNameForArrival(arrival: Record<string, unknown>): string | undefined {
  return (
    cleanDestinationLabel(arrival.station_name ?? arrival.parent_stop_name)
    || undefined
  );
}

function stationDistanceForArrival(arrival: Record<string, unknown>): number | undefined {
  const distance = Number(arrival.distance_m);
  return Number.isFinite(distance) ? distance : undefined;
}

function isInsideHalfMile(distanceM: number | undefined): boolean {
  return typeof distanceM !== "number" || distanceM <= HALF_MILE_METERS;
}

function predictionTypeForArrival(arrival: Record<string, unknown>): "live" | "scheduled" {
  const raw = String(
    arrival.prediction_type
      ?? arrival.predictionType
      ?? arrival.source
      ?? "",
  ).toLowerCase();
  if (
    raw.includes("schedule")
    || arrival.realtime === false
    || arrival.live === false
  ) {
    return "scheduled";
  }
  return "live";
}

function predictionFreshnessForArrival(
  predictionType: "live" | "scheduled",
  stale: boolean,
): "fresh" | "stale" | "scheduled" {
  if (predictionType === "scheduled") return "scheduled";
  return stale ? "stale" : "fresh";
}

function alertSeverityForDelay(delay: number): "none" | "minor" | "major" {
  if (!Number.isFinite(delay) || delay < 300) return "none";
  return delay >= 600 ? "major" : "minor";
}

function worstAlertSeverity(
  arrivals: Arrival[],
): "none" | "minor" | "major" | "planned" {
  if (arrivals.some((arrival) => arrival.alertSeverity === "major")) return "major";
  if (arrivals.some((arrival) => arrival.alertSeverity === "minor")) return "minor";
  if (arrivals.some((arrival) => arrival.alertSeverity === "planned")) return "planned";
  return "none";
}

function bestPredictionFreshness(
  arrivals: Arrival[],
): "fresh" | "stale" | "scheduled" {
  if (arrivals.some((arrival) => arrival.predictionFreshness === "fresh")) {
    return "fresh";
  }
  if (arrivals.some((arrival) => arrival.predictionFreshness === "stale")) {
    return "stale";
  }
  return "scheduled";
}

function directionKey(arrival: Arrival): string {
  // Direction is only used for filtering/grouping. Passenger-facing copy uses
  // `destination`, never "Uptown to Uptown" style labels.
  return arrival.direction;
}

function arrivalKey(arrival: Arrival): string {
  return [
    arrival.mode,
    arrival.line,
    directionKey(arrival),
    arrival.destination,
    arrival.servicePattern ?? "",
    arrival.stopName ?? "",
  ].join("|");
}

function serviceKey(arrival: Arrival): string {
  // servicePattern keeps Limited and local variants of the same headsign as
  // separate rows — they are different services, not duplicates.
  return [
    arrival.mode,
    arrival.line,
    directionKey(arrival),
    arrival.destination,
    arrival.servicePattern ?? "",
  ].join("|");
}

function compareArrivalTime(left: Arrival, right: Arrival): number {
  return left.arrivalMinutes[0] - right.arrivalMinutes[0];
}

function firstArrivalMinutes(arrival: Arrival): number {
  return arrival.arrivalMinutes[0] ?? Number.POSITIVE_INFINITY;
}

function longHeadwayPenalty(arrival: Arrival): number {
  const [first, second] = arrival.arrivalMinutes;
  if (typeof first !== "number" || typeof second !== "number") return 0;
  const gap = second - first;
  if (gap >= 20) return 3;
  if (gap >= 15) return 1.5;
  return 0;
}

function alertPenalty(arrival: Arrival): number {
  if (arrival.alertSeverity === "major") return 18;
  if (arrival.alertSeverity === "minor") return 7;
  if (arrival.alertSeverity === "planned") return 5;
  return 0;
}

function predictionPenalty(arrival: Arrival): number {
  if (arrival.predictionFreshness === "scheduled") return 4;
  if (arrival.predictionFreshness === "stale") return 1.5;
  return -1;
}

function displayUsefulnessScore(arrival: Arrival): number {
  const walk = arrival.walkMinutes ?? 10;
  const modeBias = arrival.mode === "subway" ? -0.5 : 0.5;
  return (
    walk * 2
    + firstArrivalMinutes(arrival)
    + predictionPenalty(arrival)
    + alertPenalty(arrival)
    + longHeadwayPenalty(arrival)
    + modeBias
  );
}

function compareDisplayUsefulness(left: Arrival, right: Arrival): number {
  const score = displayUsefulnessScore(left) - displayUsefulnessScore(right);
  if (score !== 0) return score;
  const walk =
    (left.walkMinutes ?? Number.POSITIVE_INFINITY)
    - (right.walkMinutes ?? Number.POSITIVE_INFINITY);
  if (walk !== 0) return walk;
  const time = compareArrivalTime(left, right);
  if (time !== 0) return time;
  if (left.mode !== right.mode) return left.mode === "subway" ? -1 : 1;
  return left.line.localeCompare(right.line);
}

function compareStationPriority(left: Arrival, right: Arrival): number {
  const leftDistance = left.stationDistanceM ?? Number.POSITIVE_INFINITY;
  const rightDistance = right.stationDistanceM ?? Number.POSITIVE_INFINITY;
  if (leftDistance !== rightDistance) return leftDistance - rightDistance;
  return compareArrivalTime(left, right);
}

function nextEntriesForBucket(bucket: Arrival[]): Next5Entry[] {
  return bucket.slice(0, 5).map((arrival) => ({
    label: arrival.label,
    mins: arrival.mins,
    stale: arrival.stale,
  }));
}

function buildStationBuckets(arrivals: Arrival[]): Arrival[] {
  const groupedByStation = new Map<string, Arrival[]>();
  for (const arrival of arrivals) {
    const key = arrivalKey(arrival);
    const bucket = groupedByStation.get(key) ?? [];
    bucket.push(arrival);
    groupedByStation.set(key, bucket);
  }

  const stationBuckets: Arrival[] = [];
  for (const bucket of groupedByStation.values()) {
    bucket.sort(compareArrivalTime);
    const arrivalMinutes = Array.from(
      new Set(bucket.map((arrival) => arrival.arrivalMinutes[0])),
    ).sort((left, right) => left - right);
    const alertSeverity = worstAlertSeverity(bucket);
    const predictionFreshness = bestPredictionFreshness(bucket);
    stationBuckets.push({
      ...bucket[0],
      arrivalMinutes,
      label: labelForArrivalMinutes(arrivalMinutes),
      status: alertSeverity === "none" ? "On Time" : "Delayed",
      alertSeverity,
      predictionType: bucket.some((arrival) => arrival.predictionType === "live")
        ? "live"
        : "scheduled",
      predictionFreshness,
      stale: bucket.every((arrival) => arrival.stale),
      nextArrivals: nextEntriesForBucket(bucket),
    });
  }
  return stationBuckets;
}

function collapseToServiceRows(arrivals: Arrival[]): Arrival[] {
  const stationBuckets = buildStationBuckets(arrivals);
  const groupedByService = new Map<string, Arrival[]>();
  for (const arrival of stationBuckets) {
    const key = serviceKey(arrival);
    const bucket = groupedByService.get(key) ?? [];
    bucket.push(arrival);
    groupedByService.set(key, bucket);
  }

  const serviceRows: Arrival[] = [];
  for (const bucket of groupedByService.values()) {
    bucket.sort(compareStationPriority);
    serviceRows.push(bucket[0]);
  }

  serviceRows.sort(compareDisplayUsefulness);
  return serviceRows;
}


function sortedRouteIds(arrivals: Arrival[]): string[] {
  const routeIds = new Set<string>();
  for (const arrival of arrivals) {
    for (const routeId of arrival.routeIds) routeIds.add(routeId);
  }
  return Array.from(routeIds).sort(compareRouteId);
}

function stationGroupKey(arrival: Arrival): string {
  return (arrival.stationName ?? arrival.stopName ?? "Nearby station")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function groupFirstArrivalMinutes(group: NearbyTransitGroup): number {
  return Math.min(
    ...group.arrivals.map((arrival) =>
      arrival.arrivalMinutes[0] ?? Number.POSITIVE_INFINITY,
    ),
  );
}

function groupAlertPenalty(group: NearbyTransitGroup): number {
  if (group.arrivals.some((arrival) => arrival.alertSeverity === "major")) return 18;
  if (group.arrivals.some((arrival) => arrival.alertSeverity === "minor")) return 7;
  if (group.arrivals.some((arrival) => arrival.alertSeverity === "planned")) return 5;
  return 0;
}

function compareNearbyTransitGroups(
  left: NearbyTransitGroup,
  right: NearbyTransitGroup,
): number {
  const walk =
    (left.walkMinutes ?? Number.POSITIVE_INFINITY)
    - (right.walkMinutes ?? Number.POSITIVE_INFINITY);
  if (walk !== 0) return walk;
  const first = groupFirstArrivalMinutes(left) - groupFirstArrivalMinutes(right);
  if (first !== 0) return first;
  const coverage = right.routeIds.length - left.routeIds.length;
  if (coverage !== 0) return coverage;
  const alert = groupAlertPenalty(left) - groupAlertPenalty(right);
  if (alert !== 0) return alert;
  return left.name.localeCompare(right.name);
}

function firstGroupedArrivalMinutes(arrival: NearbyGroupedArrival): number {
  return arrival.arrivalMinutes[0] ?? Number.POSITIVE_INFINITY;
}

function compareGroupedArrival(
  left: NearbyGroupedArrival,
  right: NearbyGroupedArrival,
): number {
  const first = firstGroupedArrivalMinutes(left) - firstGroupedArrivalMinutes(right);
  if (first !== 0) return first;
  const leftRoute = left.routeIds[0] ?? "";
  const rightRoute = right.routeIds[0] ?? "";
  const route = compareRouteId(leftRoute, rightRoute);
  if (route !== 0) return route;
  return left.destination.localeCompare(right.destination);
}

function groupedArrivalFromRow(arrival: Arrival): NearbyGroupedArrival {
  return {
    id: arrival.id,
    mode: arrival.mode,
    routeIds: arrival.routeIds,
    destination: arrival.destination,
    servicePattern: arrival.servicePattern,
    stopName: arrival.stopName,
    walkMinutes: arrival.walkMinutes,
    distanceMiles: arrival.distanceMiles,
    arrivalMinutes: arrival.arrivalMinutes,
    direction: arrival.direction,
    predictionType: arrival.predictionType,
    predictionFreshness: arrival.predictionFreshness,
    alertSeverity: arrival.alertSeverity,
  };
}

export function buildNearbySubwayGroups(stationRows: Arrival[]): NearbyTransitGroup[] {
  const groupedByStation = new Map<string, Arrival[]>();
  for (const arrival of stationRows) {
    if (arrival.mode !== "subway") continue;
    const key = stationGroupKey(arrival);
    const bucket = groupedByStation.get(key) ?? [];
    bucket.push(arrival);
    groupedByStation.set(key, bucket);
  }

  const groups: NearbyTransitGroup[] = [];
  for (const [key, bucket] of groupedByStation.entries()) {
    bucket.sort(compareDisplayUsefulness);
    const nearest = bucket.reduce((best, arrival) =>
      (arrival.walkMinutes ?? Number.POSITIVE_INFINITY)
        < (best.walkMinutes ?? Number.POSITIVE_INFINITY)
        ? arrival
        : best,
    );
    const arrivals = bucket
      .map(groupedArrivalFromRow)
      .sort(compareGroupedArrival);
    groups.push({
      id: `subway-${key}`,
      name: nearest.stationName ?? nearest.stopName ?? "Nearby station",
      mode: "subway",
      routeIds: sortedRouteIds(bucket),
      walkMinutes: nearest.walkMinutes,
      distanceMiles: nearest.distanceMiles,
      arrivals,
    });
  }

  return groups.sort(compareNearbyTransitGroups).slice(0, 8);
}

export function buildNearbyBusArrivals(serviceRows: Arrival[]): Arrival[] {
  return serviceRows
    .filter((arrival) => arrival.mode === "bus")
    .sort(compareDisplayUsefulness)
    .slice(0, 12);
}

export function buildArrivalRows(
  liveFeed: Partial<LiveFeedResponse> | null | undefined,
  nowMs = Date.now(),
): ArrivalRows {
  const arrivals: Arrival[] = [];
  for (const raw of liveFeed?.arrivals ?? []) {
    const arrival = raw as unknown as Record<string, unknown>;
    const arrivalTime = Number(arrival.arrival_time);
    if (!Number.isFinite(arrivalTime)) continue;
    const line = normalizeRouteId(String(arrival.route_id ?? "").trim());
    if (!line) continue;
    const mins = minutesUntilArrival(arrivalTime, nowMs);
    const distanceM = stationDistanceForArrival(arrival);
    if (!isInsideHalfMile(distanceM)) continue;
    const delay = Number(arrival.delay);
    const stopId = String(arrival.stop_id ?? "");
    const mode: "subway" | "bus" =
      String(arrival.mode ?? "subway") === "bus" ? "bus" : "subway";
    const direction =
      mode === "bus"
        ? busWay(arrival.stop_compass, arrival.direction)
        : normalizeWay(arrival.direction, stopId);
    let destination = destinationForArrival(arrival, line, direction, mode);
    let servicePattern = servicePatternForArrival(arrival, line, mode);
    if (mode === "bus") {
      const parts = splitBusHeadsign(destination);
      if (parts.destination) destination = parts.destination;
      if (parts.qualifiers.length > 0) {
        servicePattern = parts.qualifiers.join(" · ");
      }
    }
    const stopName = stationNameForArrival(arrival);
    const walkMinutes = walkMinutesForDistance(distanceM);
    const distanceMiles = distanceMilesForMeters(distanceM);
    const stale = arrivalTime < nowMs / 1000 - 60;
    const alertSeverity = alertSeverityForDelay(delay);
    const predictionType = predictionTypeForArrival(arrival);
    const predictionFreshness = predictionFreshnessForArrival(predictionType, stale);
    arrivals.push({
      id: [
        mode,
        line,
        direction,
        destination,
        stopName ?? "",
      ].join("|"),
      mode,
      routeIds: [line],
      destination,
      servicePattern,
      stopName,
      walkMinutes,
      distanceMiles,
      arrivalMinutes: [mins],
      direction,
      predictionType,
      predictionFreshness,
      alertSeverity,
      line,
      way: direction,
      dest: destination,
      label: labelForMinutes(mins),
      mins,
      status: alertSeverity === "none" ? "On Time" : "Delayed",
      stale,
      stationName: stopName,
      stationDistanceM: distanceM,
    });
  }

  arrivals.sort((left, right) => left.mins - right.mins);
  return {
    serviceRows: collapseToServiceRows(arrivals).slice(0, 48),
    stationRows: buildStationBuckets(arrivals)
      .sort(compareDisplayUsefulness)
      .slice(0, 72),
  };
}
