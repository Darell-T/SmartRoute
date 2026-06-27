// Trim terminal overhang: revenue lanes are sliced from full OpenData line
// geometry, which continues past the last passenger station into yards and
// non-revenue track. For every NETWORK-FREE lane endpoint (no other lane of a
// shared route continues from it), cut the geometry back to the outermost
// station that projects onto the lane, plus a small grace so the rounded cap
// still clears the stop marker.
//
// Pure + in-place: mutates feature.geometry like the other late passes.

import type { Feature, FeatureCollection, LineStringGeometry, PointGeometry, Position } from "./types.ts";

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
  [key: string]: unknown;
};

type TrimLineFeature = Feature<LineStringGeometry, TrimLineProperties>;

type StationProperties = {
  station_id?: string;
  name?: string;
  route_ids?: string[];
  [key: string]: unknown;
};

type StationFeature = Feature<PointGeometry, StationProperties>;
type StationCollection = FeatureCollection<StationFeature>;

type TerminalEntry = {
  route: string;
  coord: Position;
};

type TrimOptions = {
  graceM?: number;
  minTrimM?: number;
};

type TrimTerminalArgs = {
  features: TrimLineFeature[];
  stations?: StationCollection | null;
  terminals?: TerminalEntry[];
  options?: TrimOptions;
};

type Projection = {
  t: number;
  lateral: number;
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

export function trimTerminalOverhang({
  features,
  stations,
  terminals = [],
  options = {},
}: TrimTerminalArgs): TrimTerminalSummary {
  const grace = options.graceM ?? GRACE_M;
  const minTrim = options.minTrimM ?? MIN_TRIM_M;
  const terminalEntries = (terminals ?? [])
    .filter((t) => Array.isArray(t?.coord))
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
  const lineMeta = lines.map((f) => {
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

  const stationFeatures = (stations?.features ?? []).map((s) => ({
    coord: s.geometry?.coordinates,
    routes: expandRouteIds(s.properties?.route_ids),
  }));

  let trimmedEnds = 0;
  let removedM = 0;
  let droppedSpurs = 0;
  const spursToDrop = new Set<TrimLineFeature>();
  const actions: TrimAction[] = [];

  lines.forEach((feature, idx) => {
    const coords = feature.geometry.coordinates;
    const lat0 = coords[0][1];
    const routeSet = expandRouteIds(feature.properties?.route_ids);
    if (routeSet.size === 0) return;

    const startAttached = nearbySameRoute(coords[0], idx, routeSet, lat0);
    const endAttached = nearbySameRoute(
      coords[coords.length - 1],
      idx,
      routeSet,
      lat0,
    );

    // Outermost station projections on this lane.
    let minT = Infinity;
    let maxT = -Infinity;
    let total = 0;
    for (const s of stationFeatures) {
      if (!s.coord) continue;
      if (![...routeSet].some((r) => s.routes.has(r))) continue;
      const proj = projectOntoLine(s.coord, coords, lat0);
      if (!proj || proj.lateral > MAX_STATION_LATERAL_M) continue;
      // A projection clamped to an ATTACHED endpoint is junction capture (a
      // station beside the junction grabbing the spur that branches off
      // there) — not a real anchor. Clamped at a FREE endpoint is the normal
      // terminal-station case and counts.
      if (proj.t <= 1 && startAttached && proj.lateral > 30) continue;
      if (proj.t >= proj.total - 1 && endAttached && proj.lateral > 30) continue;
      total = proj.total;
      minT = Math.min(minT, proj.t);
      maxT = Math.max(maxT, proj.t);
    }
    if (!Number.isFinite(minT)) {
      // No stations anywhere along this feature. If it hangs off the network
      // (attached at exactly one end, free at the other) it is a yard lead /
      // non-revenue spur: drop it whole. Connectors attached at both ends and
      // isolated fragments are left alone.
      // Spur classification ignores route membership: stations.geojson route
      // lists are weekday-pattern (Rockaway Park stations say "S" while the
      // A also serves the leg), so ANY station along the feature or near its
      // free end vetoes the drop — revenue track must never look like a yard
      // lead because of a route-list mismatch.
      const anchorsAnyStation = stationFeatures.some((s) => {
        if (!s.coord) return false;
        const proj = projectOntoLine(s.coord, coords, lat0);
        if (!proj || proj.lateral > MAX_STATION_LATERAL_M) return false;
        if (proj.t <= 1 && startAttached && proj.lateral > 30) return false;
        if (proj.t >= proj.total - 1 && endAttached && proj.lateral > 30) return false;
        return true;
      });
      if (anchorsAnyStation) return;
      const freeEnd = startAttached ? coords[coords.length - 1] : coords[0];
      const stationNearFreeEnd = stationFeatures.some(
        (s) => s.coord && distM(s.coord, freeEnd, lat0) <= SPUR_FREE_END_STATION_M,
      );
      const spurLen = coords.reduce(
        (sum: number, c: Position, i: number) => (i === 0 ? 0 : sum + distM(coords[i - 1], c, lat0)),
        0,
      );
      if (
        startAttached !== endAttached &&
        !stationNearFreeEnd &&
        spurLen >= MIN_SPUR_LENGTH_M
      ) {
        spursToDrop.add(feature);
        droppedSpurs += 1;
        removedM += spurLen;
        actions.push({
          action: "drop-spur",
          routes: [...routeSet].join(","),
          at: coords[0].map((v) => +v.toFixed(4)),
          removed_m: Math.round(spurLen),
        });
      }
      return;
    }

    let fromT = 0;
    let toT = total ||
      coords.reduce(
        (sum: number, c: Position, i: number) => (i === 0 ? 0 : sum + distM(coords[i - 1], c, lat0)),
        0,
      );
    const fullLen = toT;

    const startFree = !startAttached;
    const endFree = !endAttached;

    // Terminal gate: the trim boundary (outermost anchoring station) must be
    // a true GTFS terminal of one of this feature's routes. Without that
    // evidence the "overhang" is mid-service geometry (merge approach, branch
    // with weekday-only station route lists, ...) and must not be touched.
    const terminalTs: number[] = [];
    for (const term of terminalEntries) {
      if (![...routeSet].some((r) => term.routes.has(r))) continue;
      const proj = projectOntoLine(term.coord, coords, lat0);
      if (proj && proj.lateral <= TERMINAL_LATERAL_M) terminalTs.push(proj.t);
    }
    const boundaryIsTerminal = (t: number): boolean =>
      terminalTs.some((tt) => Math.abs(tt - t) <= TERMINAL_BOUNDARY_TOLERANCE_M);

    if (startFree && minT - grace > minTrim && boundaryIsTerminal(minT)) {
      fromT = minT - grace;
      trimmedEnds += 1;
      removedM += fromT;
      actions.push({
        action: "trim-start",
        routes: [...routeSet].join(","),
        at: coords[0].map((v) => +v.toFixed(4)),
        removed_m: Math.round(fromT),
      });
    }
    if (endFree && fullLen - (maxT + grace) > minTrim && boundaryIsTerminal(maxT)) {
      toT = maxT + grace;
      trimmedEnds += 1;
      removedM += fullLen - toT;
      actions.push({
        action: "trim-end",
        routes: [...routeSet].join(","),
        at: coords[coords.length - 1].map((v) => +v.toFixed(4)),
        removed_m: Math.round(fullLen - toT),
      });
    }
    if (fromT > 0 || toT < fullLen) {
      feature.geometry = {
        type: "LineString",
        coordinates: sliceByArc(coords, fromT, toT, lat0),
      };
      if (typeof feature.properties?.length_m === "number") {
        feature.properties.length_m = Math.max(0, toT - fromT);
      }
    }
  });

  if (spursToDrop.size > 0) {
    for (let i = features.length - 1; i >= 0; i -= 1) {
      if (spursToDrop.has(features[i])) features.splice(i, 1);
    }
  }

  return { trimmedEnds, removedM: Math.round(removedM), droppedSpurs, actions };
}
