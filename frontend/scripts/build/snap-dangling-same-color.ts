// Pure helper -- no fs, no globals.
//
// Snap a dangling SAME-COLOR endpoint onto the sibling it is converging into.
// At junctions where several routes of one color merge onto a trunk (e.g. the
// B/D + F + M onto 6 Av), each route's lane is a separate feature. When one
// lane's end stops a few meters short of the trunk it reads as a line that
// "does not touch". The same-route bridge pass cannot help (B/D and M are not
// the same route) and the endpoint-crossing fabric only fixes actual crossings,
// not gaps. This pass closes that specific gap: if an endpoint is within
// snapDistM of a same-color sibling AND its incoming direction points INTO that
// sibling (a convergence, not two parallel lanes running abreast), the endpoint
// is moved onto the sibling so the merge is clean. Parallel lanes, different
// colors, already-touching ends, and far gaps are all left untouched.

import type { Feature, LineStringGeometry, Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;

type SnapFeatureProperties = {
  corridor_id?: string;
  color?: string;
  route_ids?: string[] | string;
  same_color_endpoint_snapped?: boolean;
  same_color_y_join_fabric?: boolean;
  same_color_y_join_fabric_count?: number;
};

type SnapFeature = Feature<LineStringGeometry, SnapFeatureProperties>;
type EndpointSide = "start" | "end";
type Vector = [number, number];

type Projection = {
  point: Position;
  distM: number;
  segmentIndex: number;
  segmentStart: Position;
  segmentEnd: Position;
};

type SiblingProjection = Projection & {
  sibling: SnapFeature;
};

type SplitAtArc = {
  point: Position;
  before: Position[];
  after: Position[];
};

type HermiteOptions = {
  sampleM: number;
  handleM: number;
};

type MergeOptions = {
  mergeCurveM: number;
  curveSampleM: number;
  curveHandleM: number;
  maxDirectSnapTangentDeg: number;
};

type SnapDanglingOptions = {
  snapDistM?: number;
  touchingEpsM?: number;
  convergeSampleM?: number;
  convergeMarginM?: number;
  looseSnapDistM?: number;
  looseEndM?: number;
  mergeCurveM?: number;
  curveSampleM?: number;
  curveHandleM?: number;
  maxDirectSnapTangentDeg?: number;
};

type SnapDanglingResult = {
  features: SnapFeature[];
  snappedCount: number;
};

type EndpointMergeResult = {
  coords: Position[];
  curved: boolean;
};

type SnapDanglingGates = {
  snapDistM: number;
  touchingEpsM: number;
  convergeSampleM: number;
  convergeMarginM: number;
  looseSnapDistM: number;
  looseEndM: number;
  mergeCurveM: number;
  curveSampleM: number;
  curveHandleM: number;
  maxDirectSnapTangentDeg: number;
};

type IndexedLine = {
  f: SnapFeature;
  i: number;
};

type SiblingJoinAngle = {
  siblingTangentBase: Vector;
  directAngle: number;
};

function mPerDegLng(lat: number): number {
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

function projectToPolyline(coords: Position[], p: Position): Projection | null {
  const k = mPerDegLng(p[1]);
  const px = p[0] * k;
  const py = p[1] * M_PER_DEG_LAT;
  let best: Projection | null = null;
  for (let i = 0; i < coords.length - 1; i += 1) {
    const a = coords[i];
    const b = coords[i + 1];
    const ax = a[0] * k, ay = a[1] * M_PER_DEG_LAT;
    const bx = b[0] * k, by = b[1] * M_PER_DEG_LAT;
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-12;
    let t = ((px - ax) * dx + (py - ay) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const point: Position = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    const ex = point[0] * k - px;
    const ey = point[1] * M_PER_DEG_LAT - py;
    const distM = Math.hypot(ex, ey);
    if (!best || distM < best.distM) {
      best = {
        point,
        distM,
        segmentIndex: i,
        segmentStart: a,
        segmentEnd: b,
      };
    }
  }
  return best;
}

function sameColor(a: SnapFeature, b: SnapFeature): boolean {
  const ac = a.properties?.color;
  const bc = b.properties?.color;
  return Boolean(ac && bc && String(ac).toUpperCase() === String(bc).toUpperCase());
}

function routeIds(f: SnapFeature): string[] {
  return Array.isArray(f.properties?.route_ids) ? f.properties.route_ids.map(String) : [];
}

function sharesRoute(a: SnapFeature, b: SnapFeature): boolean {
  const set = new Set(routeIds(a));
  return routeIds(b).some((r) => set.has(r));
}

function minDistToFeature(coords: Position[], p: Position): number {
  const proj = projectToPolyline(coords, p);
  return proj ? proj.distM : Infinity;
}

// A point `sampleM` of arc length inward from the given side of a polyline.
function pointInwardFrom(coords: Position[], side: EndpointSide, sampleM: number): Position {
  const seq = side === "start" ? coords : coords.slice().reverse();
  let acc = 0;
  for (let i = 1; i < seq.length; i += 1) {
    const segLen = haversineM(seq[i - 1], seq[i]);
    if (acc + segLen >= sampleM) {
      const t = (sampleM - acc) / (segLen || 1e-9);
      return [seq[i - 1][0] + (seq[i][0] - seq[i - 1][0]) * t, seq[i - 1][1] + (seq[i][1] - seq[i - 1][1]) * t];
    }
    acc += segLen;
  }
  return seq[seq.length - 1];
}

function projectAtLat(point: Position, originLat: number): Vector {
  return [point[0] * mPerDegLng(originLat), point[1] * M_PER_DEG_LAT];
}

function unprojectAtLat(point: Vector, originLat: number): Position {
  return [point[0] / mPerDegLng(originLat), point[1] / M_PER_DEG_LAT];
}

function unitVector(from: Position, to: Position, originLat: number): Vector {
  const a = projectAtLat(from, originLat);
  const b = projectAtLat(to, originLat);
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len = Math.hypot(dx, dy);
  if (len < 1e-9) return [0, 0];
  return [dx / len, dy / len];
}

function orientVectorToward(vector: Vector, fromPoint: Position, toPoint: Position, originLat: number): Vector {
  const toward = unitVector(fromPoint, toPoint, originLat);
  const dot = vector[0] * toward[0] + vector[1] * toward[1];
  return dot < 0 ? [-vector[0], -vector[1]] : vector;
}

function angleBetweenDeg(a: Vector, b: Vector): number {
  const al = Math.hypot(a[0], a[1]);
  const bl = Math.hypot(b[0], b[1]);
  if (al < 1e-9 || bl < 1e-9) return 0;
  const dot = Math.max(-1, Math.min(1, (a[0] * b[0] + a[1] * b[1]) / (al * bl)));
  return (Math.acos(dot) * 180) / Math.PI;
}

function totalLengthM(coords: Position[]): number {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) total += haversineM(coords[i - 1], coords[i]);
  return total;
}

function splitAtArcFromStart(coords: Position[], arcM: number): SplitAtArc {
  if (coords.length < 2 || arcM <= 0) {
    return { point: coords[0], before: [coords[0]], after: coords.slice() };
  }
  let acc = 0;
  for (let i = 1; i < coords.length; i += 1) {
    const seg = haversineM(coords[i - 1], coords[i]);
    if (acc + seg >= arcM) {
      const t = seg > 0 ? (arcM - acc) / seg : 0;
      const point: Position = [
        coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t,
        coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t,
      ];
      return {
        point,
        before: [...coords.slice(0, i), point],
        after: [point, ...coords.slice(i)],
      };
    }
    acc += seg;
  }
  return {
    point: coords[coords.length - 1],
    before: coords.slice(),
    after: [coords[coords.length - 1]],
  };
}

function hermiteCurve(
  startCoord: Position,
  endCoord: Position,
  startTangent: Vector,
  endTangent: Vector,
  options: HermiteOptions,
): Position[] {
  const { sampleM, handleM } = options;
  const originLat = (startCoord[1] + endCoord[1]) / 2;
  const p0 = projectAtLat(startCoord, originLat);
  const p1 = projectAtLat(endCoord, originLat);
  const distanceM = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
  const h = Math.min(handleM, Math.max(distanceM * 0.35, distanceM * 0.2));
  const m0 = [startTangent[0] * h, startTangent[1] * h];
  const m1 = [endTangent[0] * h, endTangent[1] * h];
  const steps = Math.max(4, Math.ceil(distanceM / sampleM));
  const out: Position[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const t2 = t * t;
    const t3 = t2 * t;
    const h00 = 2 * t3 - 3 * t2 + 1;
    const h10 = t3 - 2 * t2 + t;
    const h01 = -2 * t3 + 3 * t2;
    const h11 = t3 - t2;
    out.push(unprojectAtLat([
      h00 * p0[0] + h10 * m0[0] + h01 * p1[0] + h11 * m1[0],
      h00 * p0[1] + h10 * m0[1] + h01 * p1[1] + h11 * m1[1],
    ], originLat));
  }
  return out;
}

function clonePosition(position: Position): Position {
  return [position[0], position[1]];
}

function clonePolyline(coords: Position[]): Position[] {
  return coords.map(clonePosition);
}

function replaceEndpoint(coords: Position[], side: EndpointSide, point: Position): Position[] {
  const next = clonePolyline(coords);
  if (side === "start") next[0] = clonePosition(point);
  else next[next.length - 1] = clonePosition(point);
  return next;
}

function curveJoinAtEndpoint(
  coords: Position[],
  side: EndpointSide,
  targetPoint: Position,
  siblingTangentBase: Vector,
  usableMergeM: number,
  curveSampleM: number,
  curveHandleM: number,
): EndpointMergeResult {
  const orientedCoords = side === "start" ? coords : coords.slice().reverse();
  const split = splitAtArcFromStart(orientedCoords, usableMergeM);
  const rest = split.after.slice(1);
  const curveOriginLat = (targetPoint[1] + split.point[1]) / 2;
  const startTangent = orientVectorToward(siblingTangentBase, targetPoint, split.point, curveOriginLat);
  const nextForPort = rest[0] ?? split.point;
  const endTangent = unitVector(split.point, nextForPort, curveOriginLat);
  const joined = [
    ...hermiteCurve(targetPoint, split.point, startTangent, endTangent, {
        sampleM: curveSampleM,
        handleM: curveHandleM,
      }),
    ...rest,
  ];
  return { coords: side === "start" ? joined : joined.reverse(), curved: true };
}

function siblingJoinAngle(
  coords: Position[],
  side: EndpointSide,
  targetPoint: Position,
  projection: Projection,
): SiblingJoinAngle {
  const neighbor = side === "start" ? coords[1] : coords[coords.length - 2];
  const originLat = (targetPoint[1] + neighbor[1]) / 2;
  const siblingTangentBase = unitVector(projection.segmentStart, projection.segmentEnd, originLat);
  const directBranchTangent = side === "start"
    ? unitVector(targetPoint, neighbor, originLat)
    : unitVector(neighbor, targetPoint, originLat);
  const directSiblingTangent = orientVectorToward(
    siblingTangentBase,
    side === "start" ? targetPoint : neighbor,
    side === "start" ? neighbor : targetPoint,
    originLat,
  );
  return {
    siblingTangentBase,
    directAngle: angleBetweenDeg(directBranchTangent, directSiblingTangent),
  };
}

function tangentMatchedEndpointMerge(
  coords: Position[],
  side: EndpointSide,
  targetPoint: Position,
  projection: Projection | null,
  options: MergeOptions,
): EndpointMergeResult {
  if (coords.length < 2 || !projection?.segmentStart || !projection?.segmentEnd) {
    return { coords: replaceEndpoint(coords, side, targetPoint), curved: false };
  }

  const join = siblingJoinAngle(coords, side, targetPoint, projection);
  if (join.directAngle <= options.maxDirectSnapTangentDeg) {
    return { coords: replaceEndpoint(coords, side, targetPoint), curved: false };
  }

  const usableMergeM = Math.min(options.mergeCurveM, Math.max(8, totalLengthM(coords) - 1));
  if (usableMergeM < 8) {
    return { coords: replaceEndpoint(coords, side, targetPoint), curved: false };
  }

  return curveJoinAtEndpoint(
    coords,
    side,
    targetPoint,
    join.siblingTangentBase,
    usableMergeM,
    options.curveSampleM,
    options.curveHandleM,
  );
}

function nearestSameColorSibling(
  endpoint: Position,
  feature: SnapFeature,
  lines: IndexedLine[],
  gates: SnapDanglingGates,
): SiblingProjection | null {
  let best: SiblingProjection | null = null;
  for (const { f: sibling } of lines) {
    if (sibling === feature || !sameColor(feature, sibling)) continue;
    const projection = projectToPolyline(sibling.geometry.coordinates, endpoint);
    if (!projection) continue;
    if (projection.distM <= gates.touchingEpsM) return null;
    if (projection.distM <= gates.snapDistM && (!best || projection.distM < best.distM)) {
      best = { ...projection, sibling };
    }
  }
  return best;
}

function isConvergingOntoSibling(
  coords: Position[],
  side: EndpointSide,
  nearest: SiblingProjection,
  gates: SnapDanglingGates,
): boolean {
  const inward = pointInwardFrom(coords, side, gates.convergeSampleM);
  const inwardProjection = projectToPolyline(nearest.sibling.geometry.coordinates, inward);
  return Boolean(inwardProjection && inwardProjection.distM >= nearest.distM + gates.convergeMarginM);
}

function isLooseEndTerminus(
  endpoint: Position,
  feature: SnapFeature,
  lines: IndexedLine[],
  gates: SnapDanglingGates,
): boolean {
  let nearestSameRoute = Infinity;
  for (const { f: sibling } of lines) {
    if (sibling === feature || !sharesRoute(feature, sibling)) continue;
    const distanceM = minDistToFeature(sibling.geometry.coordinates, endpoint);
    if (distanceM < nearestSameRoute) nearestSameRoute = distanceM;
    if (nearestSameRoute <= gates.looseEndM) break;
  }
  return nearestSameRoute > gates.looseEndM;
}

function snapOneEndpoint(
  coords: Position[],
  side: EndpointSide,
  feature: SnapFeature,
  lines: IndexedLine[],
  gates: SnapDanglingGates,
): EndpointMergeResult | null {
  const endpoint = side === "start" ? coords[0] : coords[coords.length - 1];
  const nearest = nearestSameColorSibling(endpoint, feature, lines, gates);
  if (!nearest) return null;
  const converging = isConvergingOntoSibling(coords, side, nearest, gates);
  if (!converging && nearest.distM > gates.looseSnapDistM) return null;
  if (!converging && !isLooseEndTerminus(endpoint, feature, lines, gates)) return null;
  return tangentMatchedEndpointMerge(coords, side, nearest.point, nearest, gates);
}

function snapFeatureEndpoints(
  feature: SnapFeature,
  lines: IndexedLine[],
  gates: SnapDanglingGates,
): { feature: SnapFeature; snappedCount: number } | null {
  const coords = clonePolyline(feature.geometry.coordinates);
  let snappedCount = 0;
  let curvedCount = 0;
  for (const side of ["start", "end"] as const) {
    const merge = snapOneEndpoint(coords, side, feature, lines, gates);
    if (!merge) continue;
    coords.length = 0;
    coords.push(...merge.coords);
    snappedCount += 1;
    if (merge.curved) curvedCount += 1;
  }
  if (snappedCount === 0) return null;
  const properties: SnapFeatureProperties = {
    ...feature.properties,
    same_color_endpoint_snapped: true,
  };
  if (curvedCount > 0) {
    properties.same_color_y_join_fabric = true;
    properties.same_color_y_join_fabric_count =
      Number(feature.properties?.same_color_y_join_fabric_count ?? 0) + curvedCount;
  }
  return {
    feature: {
      ...feature,
      geometry: { ...feature.geometry, coordinates: coords },
      properties,
    },
    snappedCount,
  };
}

function resolveSnapGates(options: SnapDanglingOptions): SnapDanglingGates {
  return {
    snapDistM: options.snapDistM ?? 14,
    touchingEpsM: options.touchingEpsM ?? 1.5,
    convergeSampleM: options.convergeSampleM ?? 22,
    convergeMarginM: options.convergeMarginM ?? 3,
    looseSnapDistM: options.looseSnapDistM ?? 7,
    looseEndM: options.looseEndM ?? 20,
    mergeCurveM: options.mergeCurveM ?? 90,
    curveSampleM: options.curveSampleM ?? 5,
    curveHandleM: options.curveHandleM ?? 45,
    maxDirectSnapTangentDeg: options.maxDirectSnapTangentDeg ?? 25,
  };
}

/**
 * Snap a dangling endpoint onto the same-color sibling it is converging into.
 * Convergence is judged by distance: the lane must be getting closer to the
 * sibling toward its endpoint, not staying equidistant (a parallel lane).
 */
export function snapDanglingSameColorEndpoints(
  features: SnapFeature[],
  options: SnapDanglingOptions = {},
): SnapDanglingResult {
  const gates = resolveSnapGates(options);
  const lines: IndexedLine[] = [];
  for (let index = 0; index < features.length; index += 1) {
    const feature = features[index];
    if (feature.geometry?.type !== "LineString") continue;
    if (!Array.isArray(feature.geometry.coordinates) || feature.geometry.coordinates.length < 2) continue;
    lines.push({ f: feature, i: index });
  }
  const out = features.slice();
  let snappedCount = 0;
  for (const { f, i } of lines) {
    const snapped = snapFeatureEndpoints(f, lines, gates);
    if (!snapped) continue;
    out[i] = snapped.feature;
    snappedCount += snapped.snappedCount;
  }
  return { features: out, snappedCount };
}
