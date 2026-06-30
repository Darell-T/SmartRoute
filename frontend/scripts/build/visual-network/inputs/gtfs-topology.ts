import type { CsvRow } from "./gtfs-ingest.ts";

export type GtfsStop = {
  stop_id: string;
  name: string;
  lat: number;
  lon: number;
  parent_station: string | null;
  location_type: number;
};

export type GtfsRoute = {
  raw_route_id: string;
  route_id: string;
  short_name: string;
  long_name: string;
  color: string | null;
};

export type GtfsTrip = {
  trip_id: string;
  raw_route_id: string;
  route_id: string;
  direction_id: string;
  shape_id: string | null;
  service_id: string;
  headsign: string;
};

type StopTimeEntry = {
  seq: number;
  stopId: string;
};

export type StopsById = Map<string, GtfsStop>;
export type RoutesByRawId = Map<string, GtfsRoute>;
export type TripsById = Map<string, GtfsTrip>;
export type TripStations = Map<string, string[]>;

// --- Build stops map. Resolve parent_station so platform-level stop_ids
//     collapse to station-level (e.g., "101N" + "101S" -> "101"). ---
export function buildStopsById(stopRows: CsvRow[]): StopsById {
  const stopsById: StopsById = new Map();
  for (const r of stopRows) {
    const id = String(r.stop_id || "").trim();
    if (!id) continue;
    stopsById.set(id, {
      stop_id: id,
      name: String(r.stop_name || "").trim(),
      lat: Number(r.stop_lat),
      lon: Number(r.stop_lon),
      parent_station: String(r.parent_station || "").trim() || null,
      location_type: Number(r.location_type || 0),
    });
  }
  return stopsById;
}

// Station-level stop resolver: returns the parent_station id when present,
// otherwise the platform's own stop_id. Used to collapse 101N/101S -> 101.
function stationIdOf(stopId: string, stopsById: StopsById) {
  const s = stopsById.get(stopId);
  if (!s) return stopId;
  if (s.parent_station && stopsById.has(s.parent_station)) {
    return s.parent_station;
  }
  return stopId;
}

// --- Build routes map ---
export function buildRoutesByRawId(
  routeRows: CsvRow[],
  normalizeRouteId: (value: string) => string,
): RoutesByRawId {
  const routesByRawId: RoutesByRawId = new Map();
  for (const r of routeRows) {
    const rawId = String(r.route_id || "").trim();
    if (!rawId) continue;
    routesByRawId.set(rawId, {
      raw_route_id: rawId,
      route_id: normalizeRouteId(rawId),
      short_name: String(r.route_short_name || rawId).trim(),
      long_name: String(r.route_long_name || "").trim(),
      color: String(r.route_color || "").trim() || null,
    });
  }
  return routesByRawId;
}

// --- Build trips map ---
export function buildTripsById(
  tripRows: CsvRow[],
  routesByRawId: RoutesByRawId,
): TripsById {
  const tripsById: TripsById = new Map();
  for (const r of tripRows) {
    const tid = String(r.trip_id || "").trim();
    if (!tid) continue;
    const rawRouteId = String(r.route_id || "").trim();
    const route = routesByRawId.get(rawRouteId);
    if (!route) continue;
    tripsById.set(tid, {
      trip_id: tid,
      raw_route_id: rawRouteId,
      route_id: route.route_id,
      direction_id: String(r.direction_id || "").trim(),
      shape_id: String(r.shape_id || "").trim() || null,
      service_id: String(r.service_id || "").trim(),
      headsign: String(r.trip_headsign || "").trim(),
    });
  }
  return tripsById;
}

// --- Group stop_times by trip_id, sort by stop_sequence numeric.
//     Collapse to station-level. Build per-trip ordered station sequence. ---
export function buildTripStations(
  stopTimeRows: CsvRow[],
  stopsById: StopsById,
): TripStations {
  const stopTimesByTrip = new Map<string, StopTimeEntry[]>();
  for (const r of stopTimeRows) {
    const tid = String(r.trip_id || "").trim();
    if (!tid) continue;
    const seq = Number(r.stop_sequence);
    const stopId = String(r.stop_id || "").trim();
    if (!Number.isFinite(seq) || !stopId) continue;
    if (!stopTimesByTrip.has(tid)) stopTimesByTrip.set(tid, []);
    stopTimesByTrip.get(tid)!.push({ seq, stopId });
  }

  const tripStations: TripStations = new Map(); // trip_id -> ordered [stationId, ...]
  for (const [tid, list] of stopTimesByTrip) {
    list.sort((a: { seq: number }, b: { seq: number }) => a.seq - b.seq);
    const seen = new Set<string>();
    const sequence: string[] = [];
    for (const item of list) {
      const sid = stationIdOf(item.stopId, stopsById);
      if (seen.has(sid)) continue; // collapse adjacent duplicates (rare but defensive)
      seen.add(sid);
      sequence.push(sid);
    }
    if (sequence.length >= 2) tripStations.set(tid, sequence);
  }
  return tripStations;
}
