import type {
  LiveFeedIncident,
  LiveFeedResponse,
  RouteCandidate,
  RouteStep as ApiRouteStep,
  ServiceAlertDetail,
} from "../../../types/api";
import type {
  Alternative,
  Arrival,
  FeedEvent,
  NearbyGroupedArrival,
  NearbyTransitDirection,
  NearbyTransitGroup,
  NetworkHealth,
  Next5Entry,
  RouteDetailStep,
  RoutePlan,
  RouteReasoningInsight,
  RouteStripSegment,
  ServiceAlert,
  Station,
} from "./types";

export const HALF_MILE_METERS = 804.672;

type LineState = Record<string, "major" | "minor" | "planned">;

export interface BuildLeftRailDataInput {
  liveFeed?: Partial<LiveFeedResponse> | null;
  routeSteps?: ApiRouteStep[];
  routeCandidates?: RouteCandidate[];
  activeRouteCandidate?: RouteCandidate | null;
  // Canned line shown after the user switches to an alternative; overrides
  // the default plan headline until the next trip/clear.
  switchHeadline?: string | null;
  // Public recommendation copy for the picked route;
  // shown as the plan rationale so the card reflects the recommendation.
  recommendationText?: string | null;
  // Real ETA for the picked route: arrival clock time + trip duration. Replaces
  // the placeholder "Live · Calculated".
  routeEta?: string | null;
  routeTotalTime?: string | null;
  serviceAlerts?: ServiceAlertDetail[];
  incidents?: LiveFeedIncident[];
  nowMs?: number;
}

export interface LeftRailLiveData {
  station: Station;
  health: NetworkHealth;
  arrivals: Arrival[];
  nearbyTransitGroups: NearbyTransitGroup[];
  nearbyBusArrivals: Arrival[];
  plan: RoutePlan;
  feed: FeedEvent[];
  lineState: LineState;
  alerts: ServiceAlert[];
}

function secondsSince(epochSeconds: number | null | undefined, nowMs: number): number {
  if (!epochSeconds) return 0;
  return Math.max(0, Math.round(nowMs / 1000 - epochSeconds));
}

function formatDistance(meters: number | null | undefined): string {
  if (typeof meters !== "number" || !Number.isFinite(meters)) return "nearby";
  if (meters < 160) return `${Math.round(meters)} m`;
  return `${Math.max(0.1, meters / 1609.344).toFixed(1)} mi`;
}

function formatWalk(meters: number | null | undefined): string {
  if (typeof meters !== "number" || !Number.isFinite(meters)) return "nearby";
  return `${Math.max(1, Math.round(meters / 84))} min walk`;
}

const ROUTE_DESTINATION_FALLBACKS: Record<
  string,
  Partial<Record<"uptown" | "downtown", string>>
> = {
  "1": { uptown: "Van Cortlandt Park-242 St", downtown: "South Ferry" },
  "2": { uptown: "Wakefield-241 St", downtown: "Flatbush Av-Brooklyn College" },
  "3": { uptown: "Harlem-148 St", downtown: "New Lots Av" },
  "4": { uptown: "Woodlawn", downtown: "New Lots Av" },
  "5": { uptown: "Eastchester-Dyre Av", downtown: "Flatbush Av-Brooklyn College" },
  "6": { uptown: "Pelham Bay Park", downtown: "Brooklyn Bridge-City Hall" },
  "7": { uptown: "Flushing-Main St", downtown: "34 St-Hudson Yards" },
  A: { uptown: "Inwood-207 St", downtown: "Far Rockaway-Mott Av" },
  B: { uptown: "Bedford Park Blvd", downtown: "Brighton Beach" },
  C: { uptown: "168 St", downtown: "Euclid Av" },
  D: { uptown: "Norwood-205 St", downtown: "Coney Island-Stillwell Av" },
  E: { uptown: "Jamaica Center-Parsons/Archer", downtown: "World Trade Center" },
  F: { uptown: "Jamaica-179 St", downtown: "Coney Island-Stillwell Av" },
  G: { uptown: "Court Sq", downtown: "Church Av" },
  J: { uptown: "Jamaica Center-Parsons/Archer", downtown: "Broad St" },
  L: { uptown: "8 Av", downtown: "Canarsie-Rockaway Pkwy" },
  M: { uptown: "Forest Hills-71 Av", downtown: "Middle Village-Metropolitan Av" },
  N: { uptown: "Astoria-Ditmars Blvd", downtown: "Coney Island-Stillwell Av" },
  Q: { uptown: "96 St", downtown: "Coney Island-Stillwell Av" },
  R: { uptown: "Forest Hills-71 Av", downtown: "Bay Ridge-95 St" },
  W: { uptown: "Astoria-Ditmars Blvd", downtown: "Whitehall St" },
  Z: { uptown: "Jamaica Center-Parsons/Archer", downtown: "Broad St" },
  SI: { uptown: "St George", downtown: "Tottenville" },
  S: { uptown: "Shuttle", downtown: "Shuttle" },
};

const ROUTE_SERVICE_PATTERNS: Record<string, string> = {
  "1": "Broadway-7 Av Local",
  "2": "Broadway-7 Av Express",
  "3": "Broadway-7 Av Express",
  "4": "Lexington Av Express",
  "5": "Lexington Av Express",
  "6": "Lexington Av Local",
  "7": "Flushing Local",
  A: "8 Av Express",
  B: "6 Av Express",
  C: "8 Av Local",
  D: "6 Av Express",
  E: "8 Av Local",
  F: "6 Av Local",
  G: "Crosstown Local",
  J: "Nassau St Local",
  L: "14 St-Canarsie Local",
  M: "6 Av Local",
  N: "Broadway Local",
  Q: "Broadway Express",
  R: "Broadway Local",
  W: "Broadway Local",
  Z: "Nassau St Express",
  S: "Shuttle",
  SI: "Staten Island Railway",
};

const SUBWAY_ROUTE_SORT_ORDER = [
  "1", "2", "3",
  "4", "5", "6",
  "7",
  "A", "C", "E",
  "B", "D", "F", "M",
  "G",
  "J", "Z",
  "L",
  "N", "Q", "R", "W",
  "S", "SI",
];

const SUBWAY_ROUTE_SORT_INDEX = new Map(
  SUBWAY_ROUTE_SORT_ORDER.map((routeId, index) => [routeId, index]),
);

function normalizeRouteId(routeId: string): string {
  const upper = routeId.toUpperCase();
  if (upper === "6X") return "6";
  if (upper === "7X") return "7";
  if (upper === "FX") return "F";
  if (upper === "FS" || upper === "GS" || upper === "H") return "S";
  if (upper === "SIR") return "SI";
  return upper;
}

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
const TRANSIT_ABBREVIATIONS: Record<string, string> = {
  AV: "Av",
  AVE: "Av",
  ST: "St",
  STS: "Sts",
  SQ: "Sq",
  BLVD: "Blvd",
  BL: "Bl",
  PKWY: "Pkwy",
  PKY: "Pkwy",
  STA: "Sta",
  RD: "Rd",
  DR: "Dr",
  PL: "Pl",
  PK: "Pk",
  HTS: "Hts",
  CTR: "Ctr",
  JCT: "Jct",
  TER: "Ter",
  EXPY: "Expy",
  HWY: "Hwy",
  BCH: "Bch",
  TPKE: "Tpke",
};

/* Real acronyms stay all-caps; everything else all-caps is shouting. */
const KEEP_ALL_CAPS = new Set([
  "JFK",
  "LGA",
  "SBS",
  "SIR",
  "NYC",
  "WTC",
  "LIRR",
]);

function titleCaseTransitToken(token: string): string {
  if (!token) return token;
  const upper = token.toUpperCase();
  if (token !== upper) {
    // Already mixed case — trust it, except raw GTFS "McDONALD"-style
    // tokens where only the Mc survives in lowercase.
    const mc = token.match(/^(Mc)([A-Z]{2,})$/);
    if (mc) return `Mc${mc[2].charAt(0)}${mc[2].slice(1).toLowerCase()}`;
    return token;
  }
  if (upper === "VIA") return "via";
  if (KEEP_ALL_CAPS.has(upper)) return upper;
  const bare = upper.replace(/[^A-Z0-9]/g, "");
  if (TRANSIT_ABBREVIATIONS[bare]) {
    return upper.replace(bare, TRANSIT_ABBREVIATIONS[bare]);
  }
  if (upper.length === 1) return upper; // compass letters: E 18 St, W 4 St
  const ordinal = upper.match(/^(\d+)(ST|ND|RD|TH)$/);
  if (ordinal) return `${ordinal[1]}${ordinal[2].toLowerCase()}`;
  if (/^\d/.test(upper)) return upper;
  return upper.charAt(0) + upper.slice(1).toLowerCase();
}

/* Token-wise, so "BROWNSVILLE MOTHER GASTON BL via AMBOY" cleans even
   though the lowercase "via" means the string as a whole isn't all-caps. */
function titleCaseTransitLabel(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  return trimmed
    .split(/(\s+|\/|-)/)
    .map((part) => (/^\s+$|^[-/]$/.test(part) ? part : titleCaseTransitToken(part)))
    .join("");
}

function cleanDestinationLabel(value: unknown): string {
  return titleCaseTransitLabel(String(value ?? "").replace(/\s+/g, " ").trim());
}

/* Bus headsigns pack qualifiers into the destination ("LIMITED SUNSET PARK
   3 AV via CHURCH"). The row title should be the destination alone; the
   qualifiers belong on the metadata line. */
function splitBusHeadsign(label: string): {
  destination: string;
  qualifiers: string[];
} {
  let rest = label.trim();
  const qualifiers: string[] = [];
  const limited = rest.match(/^(?:limited|ltd\.?)\s+/i);
  if (limited) {
    qualifiers.push("Limited");
    rest = rest.slice(limited[0].length);
  }
  const via = rest.match(/\s+via\s+(.+)$/i);
  if (via && typeof via.index === "number") {
    qualifiers.push(`via ${via[1].trim()}`);
    rest = rest.slice(0, via.index).trim();
  }
  return { destination: rest, qualifiers };
}

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

function minutesUntilArrival(arrivalTimeSeconds: number, nowMs: number): number {
  const deltaSeconds = arrivalTimeSeconds - nowMs / 1000;
  if (deltaSeconds < 60) return 0;
  return Math.max(1, Math.ceil(deltaSeconds / 60));
}

function labelForMinutes(mins: number): string {
  if (mins <= 0) return "Now";
  if (mins === 1) return "1 min";
  return `${mins} min`;
}

function labelForArrivalMinutes(minutes: number[]): string {
  const values = minutes.slice(0, 3);
  if (values.length <= 1) return labelForMinutes(values[0] ?? 0);
  const rendered = values.map((mins) => (mins <= 0 ? "Now" : String(mins)));
  return `${rendered.join(", ")} min`.replace("Now min", "Now");
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

interface ArrivalRows {
  serviceRows: Arrival[];
  stationRows: Arrival[];
}

function compareRouteId(left: string, right: string): number {
  const leftIndex = SUBWAY_ROUTE_SORT_INDEX.get(left);
  const rightIndex = SUBWAY_ROUTE_SORT_INDEX.get(right);
  if (typeof leftIndex === "number" && typeof rightIndex === "number") {
    return leftIndex - rightIndex;
  }
  if (typeof leftIndex === "number") return -1;
  if (typeof rightIndex === "number") return 1;
  return left.localeCompare(right, undefined, { numeric: true });
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

function buildNearbySubwayGroups(stationRows: Arrival[]): NearbyTransitGroup[] {
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

function buildNearbyBusArrivals(serviceRows: Arrival[]): Arrival[] {
  return serviceRows
    .filter((arrival) => arrival.mode === "bus")
    .sort(compareDisplayUsefulness)
    .slice(0, 12);
}

function buildArrivalRows(
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

function buildStation(liveFeed: Partial<LiveFeedResponse> | null | undefined, nowMs: number): Station {
  const nearest = liveFeed?.nearest_stop ?? null;
  return {
    name: "Nearby transit",
    walk: formatWalk(nearest?.distance_m),
    dist: formatDistance(nearest?.distance_m),
    updatedSec: secondsSince(liveFeed?.updated_at, nowMs),
  };
}

function buildHealth(liveFeed: Partial<LiveFeedResponse> | null | undefined): NetworkHealth {
  const signals = liveFeed?.signals;
  const rawStatus = signals?.network_status ?? (liveFeed?.degraded ? "caution" : "healthy");
  const status: NetworkHealth["status"] =
    rawStatus === "disrupted" ? "disrupted" : rawStatus === "caution" ? "minor" : "clear";
  const affected = Array.from(
    new Set(
      (liveFeed?.stops ?? [])
        .flatMap((stop) => stop.route_ids ?? [])
        .map((routeId) => String(routeId).toUpperCase()),
    ),
  ).sort();

  return {
    status,
    alerts: signals?.active_alert_count ?? liveFeed?.alerts?.length ?? 0,
    lines: signals?.affected_route_count ?? affected.length,
    major: signals?.major_alert_count ?? 0,
    stale: signals?.stale_vehicle_count ?? 0,
    summary:
      liveFeed?.summary?.body
      ?? `${affected.length || "Nearby"} subway routes are being monitored inside a half-mile radius.`,
    affected: affected.slice(0, 12),
  };
}

function severityFromAlert(alert: ServiceAlertDetail): "major" | "minor" | "planned" {
  const text = `${alert.header ?? ""} ${alert.description ?? ""}`.toLowerCase();
  if (text.includes("suspend") || text.includes("no ") || text.includes("bypass")) {
    return "major";
  }
  if (text.includes("planned") || text.includes("weekend")) return "planned";
  return "minor";
}

function minutesAgo(epochSeconds: number | null | undefined, nowMs: number): string {
  if (!epochSeconds) return "live";
  const minutes = Math.max(0, Math.round((nowMs / 1000 - epochSeconds) / 60));
  if (minutes < 1) return "now";
  if (minutes === 1) return "1m";
  if (minutes < 60) return `${minutes}m`;
  return `${Math.round(minutes / 60)}h`;
}

function buildAlerts(
  liveAlerts: ServiceAlertDetail[] | undefined,
  extraAlerts: ServiceAlertDetail[] | undefined,
  nowMs: number,
): ServiceAlert[] {
  const allAlerts = [...(liveAlerts ?? []), ...(extraAlerts ?? [])];
  const seen = new Set<string>();
  return allAlerts
    .filter((alert) => {
      const key = alert.alert_id ?? `${alert.header}:${alert.description}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 12)
    .map((alert) => {
      const routes = (alert.route_ids ?? alert.routeIds ?? []).map((routeId) => String(routeId));
      const severity = severityFromAlert(alert);
      return {
        sev: severity,
        kind: "train",
        lines: routes,
        title: alert.header || "MTA service alert",
        sub: alert.description || "Active service notice",
        fullText: [alert.header, alert.description]
          .map((part) => String(part ?? "").trim())
          .filter(Boolean)
          .join("\n\n"),
        confidence: "high",
        affectedStops: alert.stop_names?.length ? alert.stop_names : undefined,
        startedAgo: minutesAgo(alert.start, nowMs),
        lastUpdate: minutesAgo(alert.end ?? alert.start, nowMs),
      };
    });
}

function buildLineState(alerts: ServiceAlert[]): LineState {
  const state: LineState = {};
  for (const alert of alerts) {
    for (const line of alert.lines) {
      const current = state[line];
      if (current === "major" || alert.sev === "watch") continue;
      state[line] = alert.sev === "major" ? "major" : alert.sev === "planned" ? "planned" : "minor";
    }
  }
  return state;
}

function buildFeed(alerts: ServiceAlert[], incidents: LiveFeedIncident[] | undefined, nowMs: number): FeedEvent[] {
  const alertEvents = alerts.slice(0, 4).map((alert) => ({
    src: "MTA" as const,
    sev: alert.sev,
    line: alert.lines[0] ?? null,
    title: alert.title,
    time: alert.startedAgo,
    detail: alert.sub,
  }));
  const incidentEvents = (incidents ?? []).slice(0, 4).map((incident) => ({
    src: "SYSTEM" as const,
    sev: incident.severity === "critical" || incident.severity === "high" ? "major" as const : "minor" as const,
    line: incident.routeIds?.[0] ?? null,
    title: incident.title,
    time: minutesAgo(incident.updated_at, nowMs),
    detail: incident.detail ?? "Nearby incident",
  }));
  return [...alertEvents, ...incidentEvents].slice(0, 8);
}

function routeStepToRailStep(step: ApiRouteStep, index: number) {
  if (step.type === "WALK") {
    const walkTarget = cleanDestinationLabel(step.arrival_stop);
    return {
      type: index === 0 ? "walk" as const : "exit" as const,
      action: "Walk",
      title: "Walk",
      detail: walkTarget ? `To ${walkTarget}` : "Continue on foot",
      duration: typeof step.minutes_until_arrival === "number" ? `${Math.round(step.minutes_until_arrival)} min` : "walk",
    };
  }

  const line = step.train_line || step.route_id || (step.type === "BUS" ? "BUS" : "");
  const departsIn = step.minutes_until_train_arrives;
  const hasLiveDeparture =
    typeof departsIn === "number" && Number.isFinite(departsIn);
  return {
    type: index === 0 ? "board" as const : "ride" as const,
    action: index === 0 ? "Board" : "Ride",
    line,
    title: `${line} ${step.type === "BUS" ? "bus" : "train"}`,
    detail:
      cleanDestinationLabel(step.direction || step.arrival_stop)
      || "Transit segment",
    note: hasLiveDeparture
      ? `Departs in ${Math.max(1, Math.round(departsIn))} min`
      : undefined,
    live: hasLiveDeparture || undefined,
    duration: typeof step.minutes_until_arrival === "number" ? `${Math.round(step.minutes_until_arrival)} min` : "live",
  };
}

function routeEtaMinutes(steps: ApiRouteStep[] | undefined): number | null {
  const sourceTotal = steps?.find(
    (step) =>
      typeof step.route_total_minutes === "number" &&
      Number.isFinite(step.route_total_minutes),
  )?.route_total_minutes;
  if (typeof sourceTotal === "number" && Number.isFinite(sourceTotal)) {
    return Math.max(1, Math.round(sourceTotal));
  }
  // Trip steps carry minutes relative to now; the largest arrival figure is
  // the trip's ETA. Good enough for candidate-vs-candidate deltas.
  let max: number | null = null;
  for (const step of steps ?? []) {
    const minutes = step.minutes_until_arrival;
    if (typeof minutes === "number" && Number.isFinite(minutes)) {
      max = max === null ? minutes : Math.max(max, minutes);
    }
  }
  return max;
}

function candidateEtaMinutes(candidate: RouteCandidate | null | undefined): number | null {
  if (
    typeof candidate?.total_minutes === "number" &&
    Number.isFinite(candidate.total_minutes)
  ) {
    return Math.max(1, Math.round(candidate.total_minutes));
  }
  return routeEtaMinutes(candidate?.steps);
}

function formatClockAt(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function candidateDelta(candidate: RouteCandidate, active: RouteCandidate | null | undefined): { delta: string; sev: "high" | "medium" | "low" } {
  const candidateEta = candidateEtaMinutes(candidate);
  const activeEta = candidateEtaMinutes(active);
  if (candidateEta === null || activeEta === null) {
    return { delta: "n/a", sev: "low" };
  }
  const diff = Math.round(candidateEta - activeEta);
  const delta = diff === 0 ? "same time" : `${diff > 0 ? "+" : ""}${diff} min`;
  const magnitude = Math.abs(diff);
  const sev = magnitude >= 8 ? "high" : magnitude >= 3 ? "medium" : "low";
  return { delta, sev };
}

function firstTransitStep(steps: ApiRouteStep[] | undefined): ApiRouteStep | undefined {
  // Local rather than importing lib/route-planning: this file is exercised
  // by the node --test runner, which cannot resolve extensionless VALUE
  // imports from .ts (type-only imports are erased and fine).
  return steps?.find((step) => step.type === "SUBWAY" || step.type === "BUS");
}

/* Route signature for dedup: mode sequence, route ids, boarding stops, and
   final arrival. Candidates that only differ in departure time collapse to
   the same signature. */
function candidateSignature(candidate: RouteCandidate | null | undefined): string {
  const transitSteps = (candidate?.steps ?? []).filter(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const legs = transitSteps.map((step) =>
    [
      step.type,
      (step.route_id || step.train_line || "").toUpperCase(),
      step.departure_stop ?? "",
      step.arrival_stop ?? "",
    ].join(":"),
  );
  return legs.join(">") || "walk-only";
}

function transitRouteIdsFromSteps(steps: ApiRouteStep[] | undefined): string[] {
  const ids: string[] = [];
  for (const step of steps ?? []) {
    if (step.type !== "SUBWAY" && step.type !== "BUS") continue;
    const id = (step.route_id || step.train_line || "").trim().toUpperCase();
    if (id && !ids.includes(id)) ids.push(id);
  }
  return ids;
}

/* Apple Maps-style option card facts, all read from the candidate's own
   precomputed steps: total time, live departure, ETA clock, and the
   boarding → alighting path. Selecting the card never replans. */
function alternativeCardFields(
  candidate: RouteCandidate,
  nowMs: number,
): Pick<
  Alternative,
  | "lines"
  | "totalMinutes"
  | "departsInMinutes"
  | "leavesLabel"
  | "arriveLabel"
  | "fromStop"
  | "toStop"
  | "strip"
> {
  const transitSteps = (candidate.steps ?? []).filter(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const first = transitSteps[0];
  const last = transitSteps[transitSteps.length - 1];
  const totalMinutes = candidateEtaMinutes(candidate) ?? undefined;
  const departsIn = first?.minutes_until_train_arrives;
  const departsInMinutes =
    typeof departsIn === "number" && Number.isFinite(departsIn)
      ? Math.max(1, Math.round(departsIn))
      : undefined;
  return {
    lines: transitRouteIdsFromSteps(candidate.steps),
    totalMinutes,
    departsInMinutes,
    leavesLabel:
      typeof departsInMinutes === "number" && nowMs > 0
        ? formatClockAt(nowMs + departsInMinutes * 60_000)
        : undefined,
    arriveLabel:
      typeof totalMinutes === "number" && nowMs > 0
        ? formatClockAt(nowMs + totalMinutes * 60_000)
        : undefined,
    fromStop: cleanDestinationLabel(first?.departure_stop) || undefined,
    toStop: cleanDestinationLabel(last?.arrival_stop) || undefined,
    strip: stripFromSteps(candidate.steps),
  };
}

function buildAlternatives(
  routeCandidates: RouteCandidate[] | undefined,
  activeRouteCandidate: RouteCandidate | null | undefined,
  nowMs: number,
): Alternative[] {
  if (!routeCandidates?.length || !activeRouteCandidate) return [];
  const activeSignature = candidateSignature(activeRouteCandidate);
  const seenSignatures = new Set<string>();
  const alternatives: Alternative[] = [];

  for (const candidate of routeCandidates) {
    if (candidate.id === activeRouteCandidate.id) continue;
    const transit = firstTransitStep(candidate.steps);
    const { delta, sev } = candidateDelta(candidate, activeRouteCandidate);
    const signature = candidateSignature(candidate);

    // Same route as the one already shown: keep at most one, labeled by
    // its real distinction instead of repeating an identical-looking row.
    // Same-time clones are dropped without consuming that one slot.
    if (signature === activeSignature) {
      if (seenSignatures.has(signature)) continue;
      if (delta === "same time") continue;
      seenSignatures.add(signature);
      alternatives.push({
        id: candidate.id,
        line: (transit?.route_id || transit?.train_line || "WALK").toUpperCase(),
        dest: "Later departure",
        delta,
        sev,
        reason: "Later departure",
        status: "rejected" as const,
        ...alternativeCardFields(candidate, nowMs),
      });
      continue;
    }

    // Duplicate of an already-kept alternate: candidates arrive ranked, so
    // the first (better) one wins.
    if (seenSignatures.has(signature)) continue;
    seenSignatures.add(signature);

    alternatives.push({
      id: candidate.id,
      line: (transit?.route_id || transit?.train_line || "WALK").toUpperCase(),
      dest:
        cleanDestinationLabel(transit?.direction || transit?.arrival_stop)
        || "Alternate routing",
      delta,
      sev,
      reason: normalizeAlternateReason(
        candidate.rejection_reason ?? candidate.recommendation_reason,
        delta,
      ),
      status: candidate.is_recommended ? ("recommended" as const) : ("rejected" as const),
      ...alternativeCardFields(candidate, nowMs),
    });
  }

  return alternatives;
}

/* Passenger-facing fallback when the backend supplied no reason — derived
   from the time delta, never from internal scoring text. */
function fallbackAlternateReason(delta: string): string {
  const diff = Number(delta.match(/[+-]?\d+/)?.[0]);
  if (Number.isFinite(diff) && diff > 0) return `Slower by ${diff} min`;
  if (Number.isFinite(diff) && diff < 0) return "Faster · lower reliability";
  return "Similar time";
}

const REASON_NUMBER_WORDS: Record<string, number> = {
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
  eleven: 11,
  twelve: 12,
  thirteen: 13,
  fourteen: 14,
  fifteen: 15,
  sixteen: 16,
  seventeen: 17,
  eighteen: 18,
  nineteen: 19,
  twenty: 20,
};

function reasonMinutes(value: string, delta: string): number | null {
  const numeric = value.match(/\b(\d+)\s*(?:min|minute|minutes)\b/i);
  if (numeric) return Number(numeric[1]);
  const word = value.match(
    /\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s*(?:min|minute|minutes)\b/i,
  )?.[1];
  if (word) return REASON_NUMBER_WORDS[word.toLowerCase()] ?? null;
  const diff = Number(delta.match(/[+-]?\d+/)?.[0]);
  return Number.isFinite(diff) && diff !== 0 ? Math.abs(diff) : null;
}

function extraTransferCount(value: string): number | null {
  const numeric = value.match(/\b(\d+)\s+extra\s+transfer/i);
  if (numeric) return Number(numeric[1]);
  const word = value.match(
    /\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+extra\s+transfer/i,
  )?.[1];
  return word ? REASON_NUMBER_WORDS[word.toLowerCase()] ?? null : null;
}

function trimReason(value: string): string {
  return value
    .replace(/\s+/g, " ")
    .replace(/\babout\s+/gi, "")
    .replace(/\bapproximately\s+/gi, "")
    .replace(/\bunder current service conditions\b/gi, "service conditions")
    .replace(/\bunder current conditions\b/gi, "current conditions")
    .replace(/\bminutes\b/gi, "min")
    .replace(/\bminute\b/gi, "min")
    .replace(/[.!]+$/g, "")
    .trim();
}

function normalizeAlternateReason(
  reason: string | null | undefined,
  delta: string,
): string {
  const cleaned = trimReason(reason ?? fallbackAlternateReason(delta));
  if (!cleaned) return fallbackAlternateReason(delta);
  const lower = cleaned.toLowerCase();

  if (/same route.*depart|departing later|later departure/.test(lower)) {
    return "Later departure";
  }

  const transferCount = extraTransferCount(cleaned);
  if (transferCount) {
    return `${transferCount} extra transfer${transferCount === 1 ? "" : "s"}`;
  }

  if (/\bmore walking\b/.test(lower)) return "More walking";

  if (/\bslower\b/.test(lower)) {
    const minutes = reasonMinutes(cleaned, delta);
    if (minutes) {
      const serviceNote = /\bservice conditions\b/.test(lower)
        ? " · service conditions"
        : "";
      return `Slower by ${minutes} min${serviceNote}`;
    }
    return "Slower route";
  }

  if (/\baffected by delays\b|\bdelays?\b|\bdelayed\b/.test(lower)) {
    return "Affected by delays";
  }

  if (/\bfaster\b/.test(lower) && /\breliability\b/.test(lower)) {
    return "Faster · lower reliability";
  }

  if (/\bsimilar time\b/.test(lower)) return "Similar time";

  return cleaned.length > 44 ? fallbackAlternateReason(delta) : cleaned;
}

function mergeConsecutiveWalks(steps: ApiRouteStep[]): ApiRouteStep[] {
  // Google Routes can split walking into back-to-back legs (approach + final
  // walk). The rail should show one "Walk" row per continuous walk, not a
  // repeated "Continue on foot". Fold a run of WALK steps into the first one,
  // extending its end to the run's last stop and keeping the later arrival ETA.
  const out: ApiRouteStep[] = [];
  for (const step of steps) {
    const prev = out[out.length - 1];
    if (step.type === "WALK" && prev?.type === "WALK") {
      out[out.length - 1] = {
        ...prev,
        arrival_stop: step.arrival_stop || prev.arrival_stop,
        minutes_until_arrival: step.minutes_until_arrival ?? prev.minutes_until_arrival,
      };
    } else {
      out.push(step);
    }
  }
  return out;
}

/* Compact visual route strip: [walk 2 min] › [Q] › [5] › [walk 5 min].
   One segment per merged walk or transit leg, in journey order. */
function stripFromSteps(steps: ApiRouteStep[] | undefined): RouteStripSegment[] {
  return mergeConsecutiveWalks(steps ?? []).map((step) =>
    step.type === "WALK"
      ? {
          kind: "walk" as const,
          minutes:
            typeof step.minutes_until_arrival === "number"
              ? Math.max(1, Math.round(step.minutes_until_arrival))
              : undefined,
        }
      : {
          kind: "ride" as const,
          routeId: (step.route_id || step.train_line || "").toUpperCase(),
          mode: step.type === "BUS" ? ("bus" as const) : ("subway" as const),
        },
  );
}

function walkDetailTitle(
  target: string,
  nextStep: ApiRouteStep | undefined,
  isLast: boolean,
): string {
  if (isLast) return "Walk to destination";
  if (target) {
    if (nextStep?.type === "SUBWAY") {
      return /station$/i.test(target)
        ? `Walk to ${target}`
        : `Walk to ${target} station`;
    }
    return `Walk to ${target}`;
  }
  return nextStep?.type === "BUS" ? "Walk to bus stop" : "Walk to station";
}

/* Full Apple Maps-style details chain: explicit walk / board / ride rows
   with headsigns, live departures, stop counts, and transfer hand-offs.
   The UI adds the Start and Arrive endpoint rows. */
function detailStepsFromSteps(steps: ApiRouteStep[] | undefined): RouteDetailStep[] {
  const merged = mergeConsecutiveWalks(steps ?? []);
  const transits = merged.filter(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const out: RouteDetailStep[] = [];
  let transitIndex = 0;

  merged.forEach((step, index) => {
    if (step.type === "WALK") {
      const target = cleanDestinationLabel(step.arrival_stop);
      const isLast = index === merged.length - 1;
      const nextStep = merged[index + 1];
      const minutes =
        typeof step.minutes_until_arrival === "number"
          ? Math.max(1, Math.round(step.minutes_until_arrival))
          : undefined;
      out.push({
        kind: "walk",
        title: walkDetailTitle(target, nextStep, isLast),
        subtitle: typeof minutes === "number" ? `About ${minutes} min` : undefined,
      });
      return;
    }

    const routeId = (step.route_id || step.train_line || "").toUpperCase();
    const mode = step.type === "BUS" ? ("bus" as const) : ("subway" as const);
    const vehicle = mode === "bus" ? "bus" : "train";
    const headsign = cleanDestinationLabel(step.direction);
    const departsIn = step.minutes_until_train_arrives;
    const hasLiveDeparture =
      typeof departsIn === "number" && Number.isFinite(departsIn);
    out.push({
      kind: "board",
      routeId,
      mode,
      title: `Board the ${routeId} ${vehicle}`,
      subtitle: headsign
        ? /bound|to /i.test(headsign)
          ? headsign
          : `Toward ${headsign}`
        : undefined,
      note: hasLiveDeparture
        ? `Departs in ${Math.max(1, Math.round(departsIn))} min`
        : undefined,
      live: hasLiveDeparture || undefined,
    });

    const stopCount =
      typeof step.stop_count === "number" && step.stop_count > 0
        ? step.stop_count
        : step.intermediate_stops?.length
          ? step.intermediate_stops.length + 1
          : undefined;
    const rideMinutes =
      typeof step.minutes_until_arrival === "number"
        ? Math.max(1, Math.round(step.minutes_until_arrival))
        : undefined;
    const next = transits[transitIndex + 1];
    const nextRouteId = next
      ? (next.route_id || next.train_line || "").toUpperCase()
      : undefined;
    out.push({
      kind: "ride",
      routeId,
      mode,
      title: `Ride the ${routeId}`,
      fromStop: cleanDestinationLabel(step.departure_stop) || undefined,
      toStop: cleanDestinationLabel(step.arrival_stop) || undefined,
      rideMeta: [
        typeof stopCount === "number"
          ? `Ride ${stopCount} stop${stopCount === 1 ? "" : "s"}`
          : "Ride",
        typeof rideMinutes === "number" ? `${rideMinutes} min` : null,
      ]
        .filter(Boolean)
        .join(" · "),
      transferTo: nextRouteId || undefined,
      transferMode: next
        ? next.type === "BUS"
          ? ("bus" as const)
          : ("subway" as const)
        : undefined,
    });
    transitIndex += 1;
  });

  return out;
}

function buildPlan(
  routeSteps: ApiRouteStep[] | undefined,
  activeRouteCandidate: RouteCandidate | null | undefined,
  routeCandidates?: RouteCandidate[],
  switchHeadline?: string | null,
  recommendationText?: string | null,
  routeEta?: string | null,
  routeTotalTime?: string | null,
  nowMs = Date.now(),
): RoutePlan {
  const transitStep = routeSteps?.find((step) => step.type === "SUBWAY" || step.type === "BUS");
  const line = transitStep?.train_line || transitStep?.route_id || "";
  const merged = mergeConsecutiveWalks(routeSteps ?? []);
  const steps: RoutePlan["steps"] = merged.map(routeStepToRailStep);
  // The final step of any plan is the destination: relabel it "Arrive" with
  // the celebratory checkered-flag icon, naming where you end up.
  if (steps.length > 0) {
    const lastRaw = merged[merged.length - 1];
    const dest = lastRaw.arrival_stop || lastRaw.departure_stop || steps[steps.length - 1].title || "Destination";
    steps[steps.length - 1] = {
      ...steps[steps.length - 1],
      type: "arrive",
      action: "Arrive",
      title: cleanDestinationLabel(dest) || "Destination",
      detail: "Arrive at destination",
    };
  }

  const defaultHeadline = activeRouteCandidate
    ? activeRouteCandidate.is_recommended === false
      ? "Alternative route engaged."
      : "Route plan is live."
    : "Choose a destination for route guidance.";

  const headsign =
    cleanDestinationLabel(transitStep?.direction || transitStep?.arrival_stop)
    || (steps.length > 0 ? steps[steps.length - 1].title : "")
    || "Walking route";

  const selectedEtaMinutes = candidateEtaMinutes(activeRouteCandidate);
  const selectedEta =
    selectedEtaMinutes !== null && nowMs > 0
      ? formatClockAt(nowMs + selectedEtaMinutes * 60_000)
      : null;
  const selectedTotalTime =
    selectedEtaMinutes !== null ? `${selectedEtaMinutes} min` : null;

  // Transfers = transit-vehicle boardings minus one. Walking segments never
  // count, whether they start, end, or connect the trip.
  const transitLegs = merged.filter(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const transferCount = Math.max(0, transitLegs.length - 1);

  // "Leave by" backs the transit departure off by the approach walk; if the
  // walk consumes the whole wait, it's simply "now".
  const firstWalkMinutes =
    merged[0]?.type === "WALK" &&
    typeof merged[0].minutes_until_arrival === "number"
      ? Math.max(0, Math.round(merged[0].minutes_until_arrival))
      : 0;
  const departsIn = transitStep?.minutes_until_train_arrives;
  const leaveByLabel =
    typeof departsIn === "number" && Number.isFinite(departsIn) && nowMs > 0
      ? departsIn - firstWalkMinutes <= 0
        ? "now"
        : formatClockAt(nowMs + (departsIn - firstWalkMinutes) * 60_000)
      : undefined;

  return {
    headline: publicRecommendationText(switchHeadline) || defaultHeadline,
    // The rail renders structured transit UI only: the rationale is derived
    // from route data, never from the model's narration/analysis text —
    // that's what keeps markdown reports out of the recommended card.
    rationale: activeRouteCandidate
      ? derivePublicRationale(activeRouteCandidate, routeSteps)
      : "Nearby arrivals are live within a half-mile radius.",
    headsign: activeRouteCandidate ? headsign : undefined,
    isAlternativeRoute: activeRouteCandidate?.is_recommended === false,
    eta: (activeRouteCandidate && (routeEta || selectedEta)) || "Live",
    totalTime: (activeRouteCandidate && (routeTotalTime || selectedTotalTime)) || (steps.length ? "Calculated" : "Pending"),
    leaveByLabel: activeRouteCandidate ? leaveByLabel : undefined,
    transferCount: activeRouteCandidate ? transferCount : undefined,
    strip: activeRouteCandidate ? stripFromSteps(routeSteps) : undefined,
    detailSteps: activeRouteCandidate ? detailStepsFromSteps(routeSteps) : undefined,
    pickedLine: line,
    steps,
    alternatives: buildAlternatives(routeCandidates, activeRouteCandidate, nowMs),
    notes: [],
  };
}

/* Short passenger-facing reason line for the recommended card, derived from
   structured route data ("Fastest available option · live arrival in 6 min
   · no service alerts."). Model/AI narration never reaches the rail. */
function derivePublicRationale(
  candidate: RouteCandidate | null | undefined,
  steps: ApiRouteStep[] | undefined,
): string {
  const parts: string[] = [
    candidate?.is_recommended === false
      ? "Alternative route"
      : "Fastest available option",
  ];
  const departsIn = firstTransitStep(steps)?.minutes_until_train_arrives;
  if (typeof departsIn === "number" && Number.isFinite(departsIn)) {
    parts.push(`live arrival in ${Math.max(1, Math.round(departsIn))} min`);
  }
  const activeAlerts = candidate?.score_breakdown?.active_alerts;
  if (activeAlerts === 0) {
    parts.push("no service alerts");
  } else if (typeof activeAlerts === "number" && activeAlerts > 0) {
    parts.push(
      `${activeAlerts} service alert${activeAlerts === 1 ? "" : "s"} on route`,
    );
  }
  return `${parts.join(" · ")}.`;
}

function publicRecommendationText(text: string | null | undefined): string {
  const cleaned = text
    ?.replace(/\bATLAS\b/gi, "SmartRoute")
    .replace(/\bVery well,\s*/i, "")
    .replace(/,\s*sir\.?/i, ".")
    .replace(/\bsir\.?\s*/i, "")
    .trim();
  return cleaned ?? "";
}

export function buildLeftRailData({
  liveFeed,
  routeSteps,
  routeCandidates,
  activeRouteCandidate,
  switchHeadline,
  recommendationText,
  routeEta,
  routeTotalTime,
  serviceAlerts,
  incidents,
  nowMs = Date.now(),
}: BuildLeftRailDataInput): LeftRailLiveData {
  const alerts = buildAlerts(liveFeed?.alerts, serviceAlerts, nowMs);
  const arrivalRows = buildArrivalRows(liveFeed, nowMs);
  return {
    station: buildStation(liveFeed, nowMs),
    health: buildHealth(liveFeed),
    arrivals: arrivalRows.serviceRows,
    nearbyTransitGroups: buildNearbySubwayGroups(arrivalRows.stationRows),
    nearbyBusArrivals: buildNearbyBusArrivals(arrivalRows.serviceRows),
    plan: buildPlan(routeSteps, activeRouteCandidate, routeCandidates, switchHeadline, recommendationText, routeEta, routeTotalTime, nowMs),
    feed: buildFeed(alerts, incidents, nowMs),
    lineState: buildLineState(alerts),
    alerts,
  };
}

/* ── Route-evaluation insights ─────────────────────────────────────────
   Public reasoning lines for the planning state, each derived from a real
   fact the rail already holds: nearby station access, live arrivals,
   official service alerts, and (separately) reported live incidents.
   A line is omitted whenever its supporting fact is unavailable — the
   fallback comparison lines are the only unconditional entries. */
export function buildRouteReasoningInsights({
  groups,
  busArrivals,
  alerts,
  incidents,
}: {
  groups: NearbyTransitGroup[];
  busArrivals: Arrival[];
  alerts: ServiceAlert[];
  incidents?: LiveFeedIncident[];
}): RouteReasoningInsight[] {
  const insights: RouteReasoningInsight[] = [];

  // Station access: the closest known entrance and its primary line.
  const closest = groups
    .filter((group) => typeof group.walkMinutes === "number" && group.routeIds.length > 0)
    .sort((left, right) => (left.walkMinutes ?? 99) - (right.walkMinutes ?? 99))[0];
  if (closest) {
    insights.push({
      id: "nearby-access",
      source: "nearby-access",
      priority: 1,
      text: `The closest ${closest.routeIds[0]} entrance is about a ${closest.walkMinutes} min walk.`,
    });
  }

  // Live arrivals: which nearby line has the soonest live train.
  let soonestLine: string | undefined;
  let soonestMins = Number.POSITIVE_INFINITY;
  for (const group of groups) {
    for (const arrival of group.arrivals) {
      const first = arrival.arrivalMinutes[0];
      if (
        typeof first === "number"
        && first < soonestMins
        && arrival.predictionType !== "scheduled"
        && arrival.routeIds[0]
      ) {
        soonestMins = first;
        soonestLine = arrival.routeIds[0];
      }
    }
  }
  if (soonestLine && soonestMins <= 8) {
    insights.push({
      id: "live-arrival",
      source: "live-arrival",
      priority: 2,
      text: `Live arrivals favor the ${soonestLine} right now.`,
    });
  }

  // Official MTA service alerts on nearby lines (kept distinct from
  // reported incidents below).
  const nearbyLines = new Set(groups.flatMap((group) => group.routeIds));
  const alertedLines: string[] = [];
  for (const alert of alerts) {
    for (const line of alert.lines) {
      const normalized = line.trim().toUpperCase();
      if (nearbyLines.has(normalized) && !alertedLines.includes(normalized)) {
        alertedLines.push(normalized);
      }
    }
  }
  if (alertedLines.length > 0) {
    insights.push({
      id: "service-alert",
      source: "service-alert",
      priority: 3,
      text: `Active service alerts on the ${alertedLines.slice(0, 2).join(" and ")} lower confidence on the fastest option.`,
    });
  } else if (nearbyLines.size > 0) {
    insights.push({
      id: "service-alert-clear",
      source: "service-alert",
      priority: 3,
      text: "No service alerts on nearby lines right now.",
    });
  }

  // Reported live incidents: a reliability signal, never a confirmed fact —
  // the copy stays at "reported" and "may affect".
  const incident = incidents?.[0];
  if (incident?.title) {
    const [kind, place] = String(incident.title)
      .split("·")
      .map((part) => part.trim());
    insights.push({
      id: "incident",
      source: "incident",
      priority: 4,
      text:
        kind && place
          ? `${kind} was reported near ${place}, so reliability there is lower.`
          : "A reported incident nearby may affect reliability.",
    });
  }

  // Bus vs subway wait, when both are on the table.
  const soonestBus = busArrivals
    .map((arrival) => ({
      line: arrival.routeIds[0],
      mins: arrival.arrivalMinutes[0],
    }))
    .filter((entry) => entry.line && typeof entry.mins === "number")
    .sort((left, right) => left.mins - right.mins)[0];
  if (soonestBus && Number.isFinite(soonestMins)) {
    insights.push({
      id: "bus-comparison",
      source: "comparison",
      priority: 5,
      text:
        soonestBus.mins > soonestMins
          ? `The ${soonestBus.line} is available, but the wait is longer right now.`
          : `The ${soonestBus.line} arrives sooner than nearby trains right now.`,
    });
  }

  // Closing comparison lines — the only unconditional entries.
  insights.push({
    id: "comparison",
    source: "comparison",
    priority: 8,
    text: "Comparing total time, walking distance, and transfers.",
  });
  insights.push({
    id: "weighting",
    source: "comparison",
    priority: 9,
    text:
      alertedLines.length > 0 || incident
        ? "Prioritizing reliability over the fastest scheduled time."
        : "Prioritizing the fastest dependable option.",
  });

  return insights
    .sort((left, right) => left.priority - right.priority)
    .slice(0, 6);
}
