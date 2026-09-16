// Trim terminal overhang: revenue lanes are sliced from full OpenData line
// geometry, which continues past the last passenger station into yards and
// non-revenue track. For every NETWORK-FREE lane endpoint (no other lane of a
// shared route continues from it), cut the geometry back to the outermost
// station that projects onto the lane, plus a small grace so the rounded cap
// still clears the stop marker.
//
// Pure + in-place: mutates feature.geometry like the other late passes.

import type { Feature, JsonValue, LineStringGeometry, Position } from "./types.ts";
import { isJsonObject } from "./visual-network/shared/route-config.ts";

const DEG_LAT_M = 111320;
const GRACE_M = 20; // keep this much line past the outermost stop
const MIN_TRIM_M = 40; // ignore smaller overhangs (not visually offensive)
const ATTACH_RADIUS_M = 25; // endpoint continues if a same-route lane is this close
const MAX_STATION_LATERAL_M = 120; // station must be this close to anchor a trim

// stations.geojson publishes the three physically distinct shuttles as plain
// "S"; lane features carry FS / GS / H. SIR appears as both SI and SIR.
const ROUTE_ALIASES = new Map([
  ["S", ["S", "FS", "GS", "H"]],
  ["SIR", ["SIR", "SI"]],
]);

type TrimLineProperties = {
  route_ids?: string[];
  visual_feature_type?: string;
  length_m?: number;
};

type TrimLineFeature = Feature<LineStringGeometry, TrimLineProperties>;

type TerminalEntry = {
  route: string;
  coord?: Position;
};

type TrimOptions = {
  graceM?: number;
  minTrimM?: number;
};

function hasTerminalCoord(entry: TerminalEntry): entry is TerminalEntry & { coord: Position } {
  return Array.isArray(entry.coord);
}

type TrimTerminalArgs = {
  features: TrimLineFeature[];
  stations?: JsonValue;
  terminals?: TerminalEntry[];
  options?: TrimOptions;
};

type StationAnchor = {
  coord: Position | undefined;
  routes: Set<string>;
};

type Projection = {
  t: number;
  lateral: number;
  total: number;
};

type StationWindow = {
  minT: number;
  maxT: number;
  total: number;
};

type TrimAction = {
  action: "trim-start" | "trim-end" | "drop-spur";
  routes: string;
  at: number[];
  removed_m: number;
};

type TrimTerminalSummary = {
  trimmedEnds: number;
  removedM: number;
  droppedSpurs: number;
  actions: TrimAction[];
};

function expandRouteIds(routeIds?: string[]): Set<string> {
  const out = new Set<string>();
  for (const r of routeIds ?? []) {
    for (const alias of ROUTE_ALIASES.get(r) ?? [r]) out.add(alias);
  }
  return out;
}

function metersXY(coord: Position, lat0: number): Position {
  const mx = DEG_LAT_M * Math.cos((lat0 * Math.PI) / 180);
  return [coord[0] * mx, coord[1] * DEG_LAT_M];
}

function distM(a: Position, b: Position, lat0: number): number {
  const [ax, ay] = metersXY(a, lat0);
  const [bx, by] = metersXY(b, lat0);
  return Math.hypot(ax - bx, ay - by);
}

// Project a point onto a polyline. Returns { t: arc-length position (m),
// lateral: perpendicular distance (m) } for the closest segment.
function projectOntoLine(point: Position, coords: Position[], lat0: number): Projection | null {
  const p = metersXY(point, lat0);
  let cum = 0;
  let best: Omit<Projection, "total"> | null = null;
  for (let i = 1; i < coords.length; i += 1) {
    const a = metersXY(coords[i - 1], lat0);
    const b = metersXY(coords[i], lat0);
    const seg = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (seg === 0) continue;
    let u = ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) /
      (seg * seg);
    u = Math.min(Math.max(u, 0), 1);
    const qx = a[0] + u * (b[0] - a[0]);
    const qy = a[1] + u * (b[1] - a[1]);
    const lateral = Math.hypot(p[0] - qx, p[1] - qy);
    if (!best || lateral < best.lateral) {
      best = { t: cum + u * seg, lateral };
    }
    cum += seg;
  }
  return best ? { ...best, total: cum } : null;
}

// Cut a polyline to the arc-length window [fromT, toT], interpolating the
// boundary vertices.
function sliceByArc(coords: Position[], fromT: number, toT: number, lat0: number): Position[] {
  const out: Position[] = [];
  let cum = 0;
  const pushInterpolated = (a: Position, b: Position, segLen: number, t: number): void => {
    const u = segLen === 0 ? 0 : (t - cum) / segLen;
    out.push([a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1])]);
  };
  for (let i = 1; i < coords.length; i += 1) {
    const a = coords[i - 1];
    const b = coords[i];
    const segLen = distM(a, b, lat0);
    const segStart = cum;
    const segEnd = cum + segLen;
    if (segEnd < fromT) {
      cum = segEnd;
      continue;
    }
    if (segStart > toT) break;
    if (out.length === 0) {
      if (segStart >= fromT) out.push([...a]);
      else pushInterpolated(a, b, segLen, fromT);
    }
    if (segEnd <= toT) out.push([...b]);
    else {
      pushInterpolated(a, b, segLen, toT);
      break;
    }
    cum = segEnd;
  }
  return out.length >= 2 ? out : coords;
}

// A trim boundary must coincide with a TRUE route terminal (from GTFS stop
// sequences) within this along-line tolerance. stations.geojson route lists
// are weekday-pattern only (e.g. the Nostrand 2/5 stations list just "2"),
// so station anchoring alone misreads mid-service geometry as overhang.
const TERMINAL_BOUNDARY_TOLERANCE_M = 250;
const TERMINAL_LATERAL_M = 140;
// A stationless spur may only be dropped when its free end is genuinely far
// from every station served by its routes (yard leads are; shadows near
// revenue stations are not).
const SPUR_FREE_END_STATION_M = 300;
// Yard leads are long; sub-150m stationless pieces are chain links between
// fragmented source segments (the shattered SIR taught us this) — never drop.
const MIN_SPUR_LENGTH_M = 150;

type LaneTrimAccount = {
  trimmedEnds: number;
  removedM: number;
  droppedSpurs: number;
  spursToDrop: Set<TrimLineFeature>;
  actions: TrimAction[];
};

type LineMeta = {
  routes: Set<string>;
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
};

function stationAnchorFromValue(value: JsonValue | undefined): StationAnchor | null {
  if (!isJsonObject(value)) return null;
  const geometry = value.geometry;
  if (!isJsonObject(geometry) || !Array.isArray(geometry.coordinates)) return null;
  const coordinates = geometry.coordinates;
  let coord: Position | undefined;
  if (coordinates.length >= 2) {
    const lon = Number(coordinates[0]);
    const lat = Number(coordinates[1]);
    if ([Number.isFinite(lon), coordinates[0] === lon, Number.isFinite(lat), coordinates[1] === lat].includes(false)) {
      return null;
    }
    coord = [lon, lat];
  }
  const properties = value.properties;
  const routeIds = isJsonObject(properties) && Array.isArray(properties.route_ids)
    ? properties.route_ids.map(String)
    : [];
  return { coord, routes: expandRouteIds(routeIds) };
}

function stationAnchorsFromDoc(stations: TrimTerminalArgs["stations"]): StationAnchor[] {
  if (!isJsonObject(stations) || !Array.isArray(stations.features)) return [];
  const anchors: StationAnchor[] = [];
  for (const value of stations.features) {
    const anchor = stationAnchorFromValue(value);
    if (anchor) anchors.push(anchor);
  }
  return anchors;
}

function polylineLengthM(coords: Position[], lat0: number): number {
  return coords.reduce(
    (sum, _coord, i) => (i === 0 ? 0 : sum + distM(coords[i - 1], coords[i], lat0)),
    0,
  );
}

function projectionIsJunctionCapture(
  proj: Projection,
  startAttached: boolean,
  endAttached: boolean,
): boolean {
  if (proj.t <= 1 && startAttached && proj.lateral > 30) return true;
  return proj.t >= proj.total - 1 && endAttached && proj.lateral > 30;
}

function stationWindowOnLane(
  coords: Position[],
  lat0: number,
  routeSet: Set<string>,
  startAttached: boolean,
  endAttached: boolean,
  stationFeatures: StationAnchor[],
): StationWindow {
  let minT = Infinity;
  let maxT = -Infinity;
  let total = 0;
  for (const station of stationFeatures) {
    if (!station.coord) continue;
    if (![...routeSet].some((routeId) => station.routes.has(routeId))) continue;
    const proj = projectOntoLine(station.coord, coords, lat0);
    if (!proj || proj.lateral > MAX_STATION_LATERAL_M) continue;
    if (projectionIsJunctionCapture(proj, startAttached, endAttached)) continue;
    total = proj.total;
    minT = Math.min(minT, proj.t);
    maxT = Math.max(maxT, proj.t);
  }
  return { minT, maxT, total };
}

function dropStationlessSpur(
  feature: TrimLineFeature,
  coords: Position[],
  lat0: number,
  startAttached: boolean,
  endAttached: boolean,
  stationFeatures: StationAnchor[],
  account: LaneTrimAccount,
): void {
  const anchorsAnyStation = stationFeatures.some((station) => {
    if (!station.coord) return false;
    const proj = projectOntoLine(station.coord, coords, lat0);
    if (!proj || proj.lateral > MAX_STATION_LATERAL_M) return false;
    return !projectionIsJunctionCapture(proj, startAttached, endAttached);
  });
  if (anchorsAnyStation) return;
  const freeEnd = startAttached ? coords[coords.length - 1] : coords[0];
  const stationNearFreeEnd = stationFeatures.some(
    (station) => station.coord && distM(station.coord, freeEnd, lat0) <= SPUR_FREE_END_STATION_M,
  );
  const spurLen = polylineLengthM(coords, lat0);
  if (startAttached === endAttached || stationNearFreeEnd || spurLen < MIN_SPUR_LENGTH_M) return;
  const routeSet = expandRouteIds(feature.properties?.route_ids);
  account.spursToDrop.add(feature);
  account.droppedSpurs += 1;
  account.removedM += spurLen;
  account.actions.push({
    action: "drop-spur",
    routes: [...routeSet].join(","),
    at: coords[0].map((value) => +value.toFixed(4)),
    removed_m: Math.round(spurLen),
  });
}

function terminalArcPositions(
  terminalEntries: Array<{ coord: Position; routes: Set<string> }>,
  routeSet: Set<string>,
  coords: Position[],
  lat0: number,
): number[] {
  const terminalTs: number[] = [];
  for (const terminal of terminalEntries) {
    if (![...routeSet].some((routeId) => terminal.routes.has(routeId))) continue;
    const projection = projectOntoLine(terminal.coord, coords, lat0);
    if (projection && projection.lateral <= TERMINAL_LATERAL_M) terminalTs.push(projection.t);
  }
  return terminalTs;
}

function trimFreeTerminalEnds(
  feature: TrimLineFeature,
  coords: Position[],
  lat0: number,
  routeSet: Set<string>,
  startAttached: boolean,
  endAttached: boolean,
  window: StationWindow,
  terminalEntries: Array<{ coord: Position; routes: Set<string> }>,
  grace: number,
  minTrim: number,
  account: LaneTrimAccount,
): void {
  const fullLen = window.total || polylineLengthM(coords, lat0);
  const terminalTs = terminalArcPositions(terminalEntries, routeSet, coords, lat0);
  const boundaryIsTerminal = (t: number): boolean =>
    terminalTs.some((tt) => Math.abs(tt - t) <= TERMINAL_BOUNDARY_TOLERANCE_M);
  let fromT = 0;
  if (!startAttached && window.minT - grace > minTrim && boundaryIsTerminal(window.minT)) {
    fromT = window.minT - grace;
    account.trimmedEnds += 1;
    account.removedM += fromT;
    account.actions.push({
      action: "trim-start",
      routes: [...routeSet].join(","),
      at: coords[0].map((value) => +value.toFixed(4)),
      removed_m: Math.round(fromT),
    });
  }
  let toT = fullLen;
  if (!endAttached && fullLen - (window.maxT + grace) > minTrim && boundaryIsTerminal(window.maxT)) {
    toT = window.maxT + grace;
    account.trimmedEnds += 1;
    account.removedM += fullLen - toT;
    account.actions.push({
      action: "trim-end",
      routes: [...routeSet].join(","),
      at: coords[coords.length - 1].map((value) => +value.toFixed(4)),
      removed_m: Math.round(fullLen - toT),
    });
  }
  if (fromT === 0 && toT === fullLen) return;
  feature.geometry = {
    type: "LineString",
    coordinates: sliceByArc(coords, fromT, toT, lat0),
  };
  if (feature.properties.length_m !== undefined) {
    feature.properties.length_m = Math.max(0, toT - fromT);
  }
}

function processLaneTrim(
  feature: TrimLineFeature,
  idx: number,
  nearbySameRoute: (endpoint: Position, selfIdx: number, routeSet: Set<string>, lat0: number) => boolean,
  stationFeatures: StationAnchor[],
  terminalEntries: Array<{ coord: Position; routes: Set<string> }>,
  grace: number,
  minTrim: number,
  account: LaneTrimAccount,
): void {
  const coords = feature.geometry.coordinates;
  const lat0 = coords[0][1];
  const routeSet = expandRouteIds(feature.properties?.route_ids);
  if (routeSet.size === 0) return;
  const startAttached = nearbySameRoute(coords[0], idx, routeSet, lat0);
  const endAttached = nearbySameRoute(coords[coords.length - 1], idx, routeSet, lat0);
  const window = stationWindowOnLane(coords, lat0, routeSet, startAttached, endAttached, stationFeatures);
  if (!Number.isFinite(window.minT)) {
    dropStationlessSpur(feature, coords, lat0, startAttached, endAttached, stationFeatures, account);
    return;
  }
  trimFreeTerminalEnds(
    feature,
    coords,
    lat0,
    routeSet,
    startAttached,
    endAttached,
    window,
    terminalEntries,
    grace,
    minTrim,
    account,
  );
}

export function trimTerminalOverhang({
  features,
  stations,
  terminals = [],
  options = {},
}: TrimTerminalArgs): TrimTerminalSummary {
  const grace = options.graceM ?? GRACE_M;
  const minTrim = options.minTrimM ?? MIN_TRIM_M;
  const terminalEntries = (terminals ?? [])
    .filter(hasTerminalCoord)
    .map((t) => ({ coord: t.coord, routes: expandRouteIds([t.route]) }));
  const lines = features.filter(
    (f) => f?.geometry?.type === "LineString" && f.geometry.coordinates.length >= 2,
  );

  // Attachment test: an endpoint is attached when it lies within
  // ATTACH_RADIUS_M of ANY POINT (not just a vertex) of another lane that
  // shares a route. Vertex-only proximity misclassified merge connectors that
  // join a sparse-vertex trunk mid-segment as dangling -- which deleted the
  // B/D 6th Av merge and the authored Nostrand 5 peel.
  const MARGIN_DEG = 0.0006; // ~50m bbox expansion, > ATTACH_RADIUS_M
  const lineMeta: LineMeta[] = lines.map((f) => {
    let minLon = Infinity;
    let maxLon = -Infinity;
    let minLat = Infinity;
    let maxLat = -Infinity;
    for (const c of f.geometry.coordinates) {
      if (c[0] < minLon) minLon = c[0];
      if (c[0] > maxLon) maxLon = c[0];
      if (c[1] < minLat) minLat = c[1];
      if (c[1] > maxLat) maxLat = c[1];
    }
    return {
      routes: expandRouteIds(f.properties?.route_ids),
      minLon: minLon - MARGIN_DEG,
      maxLon: maxLon + MARGIN_DEG,
      minLat: minLat - MARGIN_DEG,
      maxLat: maxLat + MARGIN_DEG,
    };
  });
  const nearbySameRoute = (endpoint: Position, selfIdx: number, routeSet: Set<string>, lat0: number): boolean => {
    for (let idx = 0; idx < lines.length; idx += 1) {
      if (idx === selfIdx) continue;
      const meta = lineMeta[idx];
      if (
        endpoint[0] < meta.minLon ||
        endpoint[0] > meta.maxLon ||
        endpoint[1] < meta.minLat ||
        endpoint[1] > meta.maxLat
      ) {
        continue;
      }
      if (![...routeSet].some((r) => meta.routes.has(r))) continue;
      const proj = projectOntoLine(endpoint, lines[idx].geometry.coordinates, lat0);
      if (proj && proj.lateral <= ATTACH_RADIUS_M) return true;
    }
    return false;
  };

  const stationFeatures = stationAnchorsFromDoc(stations);
  const account: LaneTrimAccount = {
    trimmedEnds: 0,
    removedM: 0,
    droppedSpurs: 0,
    spursToDrop: new Set<TrimLineFeature>(),
    actions: [],
  };

  lines.forEach((feature, idx) => {
    processLaneTrim(
      feature,
      idx,
      nearbySameRoute,
      stationFeatures,
      terminalEntries,
      grace,
      minTrim,
      account,
    );
  });

  if (account.spursToDrop.size > 0) {
    for (let i = features.length - 1; i >= 0; i -= 1) {
      if (account.spursToDrop.has(features[i])) features.splice(i, 1);
    }
  }

  return {
    trimmedEnds: account.trimmedEnds,
    removedM: Math.round(account.removedM),
    droppedSpurs: account.droppedSpurs,
    actions: account.actions,
  };
}
