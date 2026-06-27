// Pure helper -- no fs, no globals.
//
// Local schematic cleanup for Eastern Pkwy / Nostrand Av:
//  - 3 and 4 continue straight along Eastern Parkway.
//  - 2 and 5 peel from that straight trunk into the southbound branch.
//
// The OpenData geometry contains the correct services, but after same-color
// clipping + route-gap bridging the 4 tail receives a tiny terminal hook and the
// 2/5 branches can begin by backtracking. This helper owns only that local
// split and replaces the branch starts with tangent-matched curves.

import type { Feature, LineStringGeometry, Position } from "./types.ts";

type Vector = [number, number];
type Direction = "forward" | "backward";
type EndpointSide = "start" | "end";

type NostrandProperties = {
  corridor_id?: string;
  color?: unknown;
  route_ids?: unknown;
  color_route_ids?: unknown;
  length_m?: number;
  [key: string]: unknown;
};

type NostrandFeature = Feature<LineStringGeometry, NostrandProperties>;

type BBox = {
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
};

type ArcOptions = {
  branchTurnSpanM: number;
  trunkBlendM: number;
  sampleM: number;
};

type PartialArcOptions = Partial<ArcOptions>;

type HermiteOptions = {
  sampleM?: number;
  handleFrac?: number;
};

type SegmentProjection = {
  point: Position;
  t: number;
  distanceM: number;
};

type PolylineProjection = SegmentProjection & {
  arcM: number;
  segmentIndex: number;
};

type EndpointCandidate = {
  side: EndpointSide;
  point: Position;
  projection: PolylineProjection;
  distanceM: number;
};

type Diagnostics = {
  applied: boolean;
  reason: string | null;
  red_branch_rebuilt: boolean;
  green_tail_straightened: boolean;
  green_branch_rebuilt: boolean;
  green_split_point?: Position;
  red_split_point?: Position;
};

type SchematicResult = {
  features: NostrandFeature[];
  diagnostics: Diagnostics;
};

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;
const GREEN = "#00933C";
const RED = "#EE352E";

const NOSTRAND_BBOX = {
  minLon: -73.958,
  maxLon: -73.943,
  minLat: 40.664,
  maxLat: 40.673,
};

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function project(point: Position, originLat: number): Vector {
  return [point[0] * metersPerDegLng(originLat), point[1] * M_PER_DEG_LAT];
}

function unproject(point: Vector, originLat: number): Position {
  return [point[0] / metersPerDegLng(originLat), point[1] / M_PER_DEG_LAT];
}

function vectorMeters(from: Position, to: Position): Vector {
  const lat = (from[1] + to[1]) / 2;
  return [
    (to[0] - from[0]) * metersPerDegLng(lat),
    (to[1] - from[1]) * M_PER_DEG_LAT,
  ];
}

function unitVector(from: Position, to: Position): Vector {
  const v = vectorMeters(from, to);
  const len = Math.hypot(v[0], v[1]);
  if (len < 1e-9) return [1, 0];
  return [v[0] / len, v[1] / len];
}

function normalizeVector(vector: Vector, fallback: Vector = [1, 0]): Vector {
  const len = Math.hypot(vector[0], vector[1]);
  if (len < 1e-9) return fallback;
  return [vector[0] / len, vector[1] / len];
}

function orientToward(vector: Vector, from: Position, to: Position): Vector {
  const toward = vectorMeters(from, to);
  return vector[0] * toward[0] + vector[1] * toward[1] < 0
    ? [-vector[0], -vector[1]]
    : vector;
}

function inBBox(point: Position, bbox: BBox = NOSTRAND_BBOX): boolean {
  return (
    point[0] >= bbox.minLon &&
    point[0] <= bbox.maxLon &&
    point[1] >= bbox.minLat &&
    point[1] <= bbox.maxLat
  );
}

function routeIds(feature: NostrandFeature): string[] {
  return Array.isArray(feature.properties?.route_ids)
    ? feature.properties.route_ids.map(String)
    : [];
}

function hasRoute(feature: NostrandFeature, routeId: string): boolean {
  return routeIds(feature).includes(routeId);
}

function color(feature: NostrandFeature): string {
  return String(feature.properties?.color ?? "").toUpperCase();
}

function isLine(feature: NostrandFeature): boolean {
  return (
    feature?.geometry?.type === "LineString" &&
    Array.isArray(feature.geometry.coordinates) &&
    feature.geometry.coordinates.length >= 2
  );
}

function touchesNostrand(feature: NostrandFeature): boolean {
  return isLine(feature) && feature.geometry.coordinates.some((point) => inBBox(point));
}

function cumulativeArcs(coords: Position[]): number[] {
  const arcs = [0];
  for (let index = 1; index < coords.length; index += 1) {
    arcs.push(arcs[index - 1] + haversineM(coords[index - 1], coords[index]));
  }
  return arcs;
}

function interpolateAtArc(coords: Position[], arcs: number[], arcM: number): Position {
  const total = arcs[arcs.length - 1] ?? 0;
  if (arcM <= 0) return [...coords[0]];
  if (arcM >= total) return [...coords[coords.length - 1]];
  for (let index = 1; index < arcs.length; index += 1) {
    if (arcs[index] < arcM) continue;
    const prevArc = arcs[index - 1];
    const nextArc = arcs[index];
    const t = (arcM - prevArc) / Math.max(1e-9, nextArc - prevArc);
    const previous = coords[index - 1];
    const next = coords[index];
    return [
      previous[0] + (next[0] - previous[0]) * t,
      previous[1] + (next[1] - previous[1]) * t,
    ];
  }
  return [...coords[coords.length - 1]];
}

function appendCoord(out: Position[], coord: Position | undefined, epsM = 0.2): void {
  if (!coord) return;
  const previous = out[out.length - 1];
  if (previous && haversineM(previous, coord) <= epsM) return;
  out.push([...coord]);
}

function sliceByArc(coords: Position[], startArc: number, endArc: number): Position[] {
  const arcs = cumulativeArcs(coords);
  const total = arcs[arcs.length - 1] ?? 0;
  const start = Math.max(0, Math.min(total, startArc));
  const end = Math.max(0, Math.min(total, endArc));
  if (end - start <= 0.5) return [];
  const out: Position[] = [];
  appendCoord(out, interpolateAtArc(coords, arcs, start));
  for (let index = 1; index < coords.length - 1; index += 1) {
    if (arcs[index] > start + 0.2 && arcs[index] < end - 0.2) {
      appendCoord(out, coords[index]);
    }
  }
  appendCoord(out, interpolateAtArc(coords, arcs, end));
  return out;
}

function tangentAtArc(coords: Position[], arcM: number, direction: Direction = "forward"): Vector {
  const arcs = cumulativeArcs(coords);
  const total = arcs[arcs.length - 1] ?? 0;
  const a = interpolateAtArc(coords, arcs, Math.max(0, Math.min(total, arcM - 18)));
  const b = interpolateAtArc(coords, arcs, Math.max(0, Math.min(total, arcM + 18)));
  const vector = direction === "backward" ? unitVector(b, a) : unitVector(a, b);
  return vector;
}

function projectPointToSegment(point: Position, a: Position, b: Position): SegmentProjection {
  const lat = (point[1] + a[1] + b[1]) / 3;
  const [px, py] = project(point, lat);
  const [ax, ay] = project(a, lat);
  const [bx, by] = project(b, lat);
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy || 1e-12;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  const projected = unproject([ax + dx * t, ay + dy * t], lat);
  return { point: projected, t, distanceM: haversineM(point, projected) };
}

function projectPointToPolyline(point: Position, coords: Position[]): PolylineProjection | null {
  const arcs = cumulativeArcs(coords);
  let best: PolylineProjection | null = null;
  for (let index = 0; index < coords.length - 1; index += 1) {
    const projection = projectPointToSegment(point, coords[index], coords[index + 1]);
    const arcM = arcs[index] + (arcs[index + 1] - arcs[index]) * projection.t;
    if (!best || projection.distanceM < best.distanceM) {
      best = {
        ...projection,
        arcM,
        segmentIndex: index,
      };
    }
  }
  return best;
}

function nearestIndex(coords: Position[], point: Position): { index: number; distanceM: number } {
  let best = { index: -1, distanceM: Infinity };
  coords.forEach((coord: Position, index: number) => {
    const distanceM = haversineM(coord, point);
    if (distanceM < best.distanceM) best = { index, distanceM };
  });
  return best;
}

function hermiteCurve(start: Position, end: Position, startTangent: Vector, endTangent: Vector, options: HermiteOptions = {}): Position[] {
  const { sampleM = 6, handleFrac = 0.45 } = options;
  const originLat = (start[1] + end[1]) / 2;
  const p0 = project(start, originLat);
  const p1 = project(end, originLat);
  const dist = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
  const handle = dist * handleFrac;
  const m0 = [startTangent[0] * handle, startTangent[1] * handle];
  const m1 = [endTangent[0] * handle, endTangent[1] * handle];
  const steps = Math.max(10, Math.ceil(dist / sampleM));
  const out: Position[] = [];
  for (let index = 0; index <= steps; index += 1) {
    const t = index / steps;
    const t2 = t * t;
    const t3 = t2 * t;
    out.push(unproject([
      (2 * t3 - 3 * t2 + 1) * p0[0] +
        (t3 - 2 * t2 + t) * m0[0] +
        (-2 * t3 + 3 * t2) * p1[0] +
        (t3 - t2) * m1[0],
      (2 * t3 - 3 * t2 + 1) * p0[1] +
        (t3 - 2 * t2 + t) * m0[1] +
        (-2 * t3 + 3 * t2) * p1[1] +
        (t3 - t2) * m1[1],
    ], originLat));
  }
  return out;
}

function polylineLengthM(coords: Position[]): number {
  return cumulativeArcs(coords).at(-1) ?? 0;
}

function cloneWithCoordinates(feature: NostrandFeature, coordinates: Position[], properties: NostrandProperties = {}): NostrandFeature {
  return {
    ...feature,
    geometry: {
      ...feature.geometry,
      coordinates,
    },
    properties: {
      ...(feature.properties ?? {}),
      ...properties,
      length_m: Number(polylineLengthM(coordinates).toFixed(2)),
    },
  };
}

function stableStraightTailEnd(coords: Position[], windowSize = 12): { index: number; point: Position } {
  const start = Math.max(0, coords.length - windowSize);
  let bestIndex = coords.length - 1;
  // The bad local bridge bends south. The intended Eastern Parkway tail is the
  // northernmost point in the terminal window before that downward hook.
  for (let index = start; index < coords.length; index += 1) {
    if (coords[index][1] >= coords[bestIndex][1]) bestIndex = index;
  }
  return { index: bestIndex, point: [...coords[bestIndex]] };
}

function rebuildEndpointBranch(
  feature: NostrandFeature,
  endpointSide: EndpointSide,
  splitPoint: Position,
  startTangent: Vector,
  options: Pick<ArcOptions, "branchTurnSpanM" | "sampleM">,
): Position[] {
  const { branchTurnSpanM, sampleM } = options;
  const coords = feature.geometry.coordinates;
  const working = endpointSide === "start" ? coords : coords.slice().reverse();
  const arcs = cumulativeArcs(working);
  const total = arcs[arcs.length - 1] ?? 0;
  const joinArc = Math.min(branchTurnSpanM, Math.max(80, total * 0.2));
  const joinPoint = interpolateAtArc(working, arcs, joinArc);
  const after = sliceByArc(working, joinArc, total);
  const endTangent = tangentAtArc(working, joinArc, "forward");
  const tangent = normalizeVector(orientToward(startTangent, splitPoint, joinPoint), [1, 0]);
  const curve = hermiteCurve(splitPoint, joinPoint, tangent, endTangent, {
    sampleM,
    handleFrac: 0.38,
  });
  const next = [...curve, ...after.slice(1)];
  return endpointSide === "start" ? next : next.reverse();
}

function rebuildInternalBranch(
  feature: NostrandFeature,
  splitPoint: Position,
  horizontalTangent: Vector,
  options: ArcOptions,
): Position[] | null {
  const { branchTurnSpanM, trunkBlendM, sampleM } = options;
  const coords = feature.geometry.coordinates;
  const near = nearestIndex(coords, splitPoint);
  if (near.index < 2 || near.index > coords.length - 3 || near.distanceM > 45) {
    return null;
  }

  const arcs = cumulativeArcs(coords);
  const total = arcs.at(-1) ?? 0;
  const splitArc = arcs[near.index];
  const southArc = Math.max(0, splitArc - branchTurnSpanM);
  const westArc = Math.min(total, splitArc + trunkBlendM);
  if (splitArc - southArc < 70 || westArc - splitArc < 40) return null;

  const southPoint = interpolateAtArc(coords, arcs, southArc);
  const westPoint = interpolateAtArc(coords, arcs, westArc);
  const southTangent = tangentAtArc(coords, southArc, "forward");
  const westTangent = normalizeVector(orientToward(
    [-horizontalTangent[0], -horizontalTangent[1]],
    splitPoint,
    westPoint,
  ), [-1, 0]);
  const westEndTangent = tangentAtArc(coords, westArc, "forward");

  const intoSplit = hermiteCurve(southPoint, splitPoint, southTangent, westTangent, {
    sampleM,
    handleFrac: 0.42,
  });
  const outOfSplit = hermiteCurve(splitPoint, westPoint, westTangent, westEndTangent, {
    sampleM,
    handleFrac: 0.35,
  });

  const before = sliceByArc(coords, 0, southArc);
  const after = sliceByArc(coords, westArc, total);
  return [
    ...before.slice(0, -1),
    ...intoSplit,
    ...outOfSplit.slice(1),
    ...after.slice(1),
  ];
}

function endpointCandidate(feature: NostrandFeature, sibling: NostrandFeature): EndpointCandidate | null {
  const coords = feature.geometry.coordinates;
  const endpoints: Array<{ side: EndpointSide; point: Position }> = [
    { side: "start", point: coords[0] },
    { side: "end", point: coords[coords.length - 1] },
  ];
  let best: EndpointCandidate | null = null;
  for (const endpoint of endpoints) {
    const projection = projectPointToPolyline(endpoint.point, sibling.geometry.coordinates);
    if (!projection) continue;
    if (!inBBox(endpoint.point) && !inBBox(projection.point)) continue;
    if (!best || projection.distanceM < best.distanceM) {
      best = { ...endpoint, projection, distanceM: projection.distanceM };
    }
  }
  return best;
}

function horizontalTangentAtProjection(feature: NostrandFeature, point: Position): Vector {
  const projection = projectPointToPolyline(point, feature.geometry.coordinates);
  if (!projection) return [1, 0];
  const tangent = tangentAtArc(feature.geometry.coordinates, projection.arcM, "forward");
  return tangent[0] < 0 ? [-tangent[0], -tangent[1]] : tangent;
}

function isExactRoute(feature: NostrandFeature, ids: string[]): boolean {
  const actual = routeIds(feature).sort().join(",");
  return actual === [...ids].sort().join(",");
}

/**
 * @param {Array<GeoJSON.Feature>} features
 * @returns {{features:Array<GeoJSON.Feature>, diagnostics: object}}
 */
export function applyNostrandEasternSchematic(features: NostrandFeature[], options: PartialArcOptions = {}): SchematicResult {
  const {
    branchTurnSpanM = 420,
    trunkBlendM = 170,
    sampleM = 6,
  } = options;

  const redTrunk = features.find((feature) =>
    touchesNostrand(feature) &&
    color(feature) === RED &&
    hasRoute(feature, "3"),
  );
  const redBranch = features.find((feature) =>
    touchesNostrand(feature) &&
    color(feature) === RED &&
    hasRoute(feature, "2") &&
    !hasRoute(feature, "3"),
  );
  const greenTail = features.find((feature) =>
    touchesNostrand(feature) &&
    color(feature) === GREEN &&
    isExactRoute(feature, ["4"]),
  );
  const greenBranch = features.find((feature) =>
    touchesNostrand(feature) &&
    color(feature) === GREEN &&
    hasRoute(feature, "5") &&
    feature !== greenTail,
  );

  const diagnostics: Diagnostics = {
    applied: false,
    reason: null,
    red_branch_rebuilt: false,
    green_tail_straightened: false,
    green_branch_rebuilt: false,
  };

  if (!redTrunk || !redBranch || !greenTail || !greenBranch) {
    diagnostics.reason = "missing_required_features";
    return { features, diagnostics };
  }

  const next = features.slice();

  const greenTailCoords = greenTail.geometry.coordinates;
  const stableTail = stableStraightTailEnd(greenTailCoords);
  const greenSplit = stableTail.point;
  const straightTailCoords = greenTailCoords.slice(0, stableTail.index + 1);

  const greenTailIndex = next.indexOf(greenTail);
  next[greenTailIndex] = cloneWithCoordinates(greenTail, straightTailCoords, {
    nostrand_eastern_straight_tail: true,
    nostrand_eastern_removed_terminal_hook: true,
    qa_orphan_origin: false,
    qa_orphan_severity: null,
  });
  diagnostics.green_tail_straightened = true;

  const redCandidate = endpointCandidate(redBranch, redTrunk);
  const redSplit = redCandidate?.projection?.point ?? redBranch.geometry.coordinates[0];
  const redTangent = horizontalTangentAtProjection(redTrunk, redSplit);
  const redBranchCoords = rebuildEndpointBranch(
    redBranch,
    redCandidate?.side ?? "start",
    redSplit,
    redTangent,
    { branchTurnSpanM, sampleM },
  );
  const redBranchIndex = next.indexOf(redBranch);
  next[redBranchIndex] = cloneWithCoordinates(redBranch, redBranchCoords, {
    nostrand_eastern_branch_curve: true,
    nostrand_eastern_split_point: redSplit,
    qa_orphan_origin: false,
    qa_orphan_severity: null,
  });
  diagnostics.red_branch_rebuilt = true;

  const greenTangent = horizontalTangentAtProjection(next[greenTailIndex], greenSplit);
  const greenBranchCoords = rebuildInternalBranch(
    greenBranch,
    greenSplit,
    greenTangent,
    { branchTurnSpanM, trunkBlendM, sampleM },
  );
  if (!greenBranchCoords) {
    diagnostics.reason = "green_branch_split_not_found";
    return { features, diagnostics };
  }
  const greenBranchIndex = next.indexOf(greenBranch);
  next[greenBranchIndex] = cloneWithCoordinates(greenBranch, greenBranchCoords, {
    nostrand_eastern_branch_curve: true,
    nostrand_eastern_split_point: greenSplit,
    qa_orphan_origin: false,
    qa_orphan_severity: null,
  });
  diagnostics.green_branch_rebuilt = true;

  diagnostics.applied = true;
  diagnostics.green_split_point = greenSplit;
  diagnostics.red_split_point = redSplit;
  return { features: next, diagnostics };
}
