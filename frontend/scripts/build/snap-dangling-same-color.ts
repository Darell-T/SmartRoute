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
  route_ids?: string[];
  same_color_endpoint_snapped?: boolean;
  same_color_y_join_fabric?: boolean;
  same_color_y_join_fabric_count?: number;
  [key: string]: unknown;
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

function splitAtArcFromEnd(coords: Position[], arcM: number): SplitAtArc {
  const reversed = coords.slice().reverse();
  const split = splitAtArcFromStart(reversed, arcM);
  return {
    point: split.point,
    before: split.after.slice().reverse(),
    after: split.before.slice().reverse(),
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

function tangentMatchedEndpointMerge(
  coords: Position[],
  side: EndpointSide,
  targetPoint: Position,
  projection: Projection | null,
  options: MergeOptions,
): { coords: Position[]; curved: boolean } {
  const {
    mergeCurveM,
    curveSampleM,
    curveHandleM,
    maxDirectSnapTangentDeg,
  } = options;
  if (coords.length < 2 || !projection?.segmentStart || !projection?.segmentEnd) {
    const next = coords.map((coord) => coord.slice() as Position);
    if (side === "start") next[0] = targetPoint.slice() as Position;
    else next[next.length - 1] = targetPoint.slice() as Position;
    return { coords: next, curved: false };
  }

  const originLat = (targetPoint[1] + (side === "start" ? coords[1][1] : coords[coords.length - 2][1])) / 2;
  const siblingTangentBase = unitVector(projection.segmentStart, projection.segmentEnd, originLat);
  const directBranchTangent = side === "start"
    ? unitVector(targetPoint, coords[1], originLat)
    : unitVector(coords[coords.length - 2], targetPoint, originLat);
  const directSiblingTangent = orientVectorToward(
    siblingTangentBase,
    side === "start" ? targetPoint : coords[coords.length - 2],
    side === "start" ? coords[1] : targetPoint,
    originLat,
  );
  const directAngle = angleBetweenDeg(directBranchTangent, directSiblingTangent);

  if (directAngle <= maxDirectSnapTangentDeg) {
    const next = coords.map((coord) => coord.slice() as Position);
    if (side === "start") next[0] = targetPoint.slice() as Position;
    else next[next.length - 1] = targetPoint.slice() as Position;
    return { coords: next, curved: false };
  }

  const usableMergeM = Math.min(mergeCurveM, Math.max(8, totalLengthM(coords) - 1));
  if (usableMergeM < 8) {
    const next = coords.map((coord) => coord.slice() as Position);
    if (side === "start") next[0] = targetPoint.slice() as Position;
    else next[next.length - 1] = targetPoint.slice() as Position;
    return { coords: next, curved: false };
  }

  if (side === "start") {
    const split = splitAtArcFromStart(coords, usableMergeM);
    const rest = split.after.slice(1);
    const curveOriginLat = (targetPoint[1] + split.point[1]) / 2;
    const startTangent = orientVectorToward(siblingTangentBase, targetPoint, split.point, curveOriginLat);
    const nextForPort = rest[0] ?? split.point;
    const endTangent = unitVector(split.point, nextForPort, curveOriginLat);
    const curve = hermiteCurve(targetPoint, split.point, startTangent, endTangent, {
      sampleM: curveSampleM,
      handleM: curveHandleM,
    });
    return {
      coords: [...curve, ...rest],
      curved: true,
    };
  }

  const split = splitAtArcFromEnd(coords, usableMergeM);
  const kept = split.before.slice(0, -1);
  const curveOriginLat = (split.point[1] + targetPoint[1]) / 2;
  const prevForPort = kept[kept.length - 1] ?? split.point;
  const startTangent = unitVector(prevForPort, split.point, curveOriginLat);
  const endTangent = orientVectorToward(siblingTangentBase, split.point, targetPoint, curveOriginLat);
  const curve = hermiteCurve(split.point, targetPoint, startTangent, endTangent, {
    sampleM: curveSampleM,
    handleM: curveHandleM,
  });
  return {
    coords: [...kept, ...curve],
    curved: true,
  };
}

/**
 * Snap a dangling endpoint onto the same-color sibling it is converging into.
 * Convergence is judged by DISTANCE: the lane must be getting closer to the
 * sibling toward its endpoint (a merge), not staying equidistant (a parallel
 * lane). This correctly catches offset lanes that run alongside the trunk and
 * then stop short -- whose endpoint tangent is ~perpendicular to the trunk --
 * while leaving genuine parallel lanes (e.g. the SI double-track) untouched.
 *
 * @param {Array} features
 * @param {object} [options]
 * @param {number} [options.snapDistM=14] snap an endpoint within this distance of a same-color sibling
 * @param {number} [options.touchingEpsM=1.5] endpoints already this close are considered joined
 * @param {number} [options.convergeSampleM=22] arc distance inward used to test convergence
 * @param {number} [options.convergeMarginM=3] required drop in sibling distance from sample to endpoint
 * @param {number} [options.looseSnapDistM=7] snap a loose-end terminus within this of a same-color sibling
 * @param {number} [options.looseEndM=20] an endpoint with no same-route piece within this is a loose end
 * @param {number} [options.mergeCurveM=90] branch distance replaced with tangent-matched curve when direct snap is kinky
 * @param {number} [options.curveSampleM=5] target spacing for generated merge curve vertices
 * @param {number} [options.curveHandleM=45] maximum Hermite handle length
 * @param {number} [options.maxDirectSnapTangentDeg=25] direct snap only when endpoint tangent is this close to sibling tangent
 * @returns {{ features: Array, snappedCount: number }}
 */
export function snapDanglingSameColorEndpoints(
  features: SnapFeature[],
  options: SnapDanglingOptions = {},
): SnapDanglingResult {
  const {
    snapDistM = 14,
    touchingEpsM = 1.5,
    convergeSampleM = 22,
    convergeMarginM = 3,
    looseSnapDistM = 7,
    looseEndM = 20,
    mergeCurveM = 90,
    curveSampleM = 5,
    curveHandleM = 45,
    maxDirectSnapTangentDeg = 25,
  } = options;
  const lines = features
    .map((f, i) => ({ f, i }))
    .filter(({ f }) => f.geometry?.type === "LineString" && Array.isArray(f.geometry.coordinates) && f.geometry.coordinates.length >= 2);

  const out = features.slice();
  let snappedCount = 0;

  for (const { f, i } of lines) {
    const coords = f.geometry.coordinates.map((p) => p.slice() as Position);
    let changed = false;
    let curvedCount = 0;
    for (const side of ["start", "end"] as const) {
      const endpoint = side === "start" ? coords[0] : coords[coords.length - 1];

      let best: (Projection & { sibling: SnapFeature }) | null = null;
      let touching = false;
      for (const { f: g } of lines) {
        if (g === f) continue;
        if (!sameColor(f, g)) continue;
        const proj = projectToPolyline(g.geometry.coordinates, endpoint);
        if (!proj) continue;
        if (proj.distM <= touchingEpsM) { touching = true; break; }
        if (proj.distM <= snapDistM && (!best || proj.distM < best.distM)) best = { ...proj, sibling: g };
      }
      if (touching || !best) continue;

      // Convergence by distance: the sibling must be closer at the endpoint than
      // a short way inward along this lane. Parallel lanes stay equidistant.
      const inward = pointInwardFrom(coords, side, convergeSampleM);
      const inwardProj = projectToPolyline(best.sibling.geometry.coordinates, inward);
      const converging = inwardProj && inwardProj.distM >= best.distM + convergeMarginM;

      // Loose-end terminus: this route's drawing simply ends here (no same-route
      // piece nearby) right next to a same-color sibling. That is a fragment that
      // should merge onto the trunk. Use a threshold tighter than the lane
      // spacing so genuine parallel lanes (one full lane-width apart) are kept.
      let looseEnd = false;
      if (!converging && best.distM <= looseSnapDistM) {
        let nearestSameRoute = Infinity;
        for (const { f: g } of lines) {
          if (g === f) continue;
          if (!sharesRoute(f, g)) continue;
          const d = minDistToFeature(g.geometry.coordinates, endpoint);
          if (d < nearestSameRoute) nearestSameRoute = d;
          if (nearestSameRoute <= looseEndM) break;
        }
        looseEnd = nearestSameRoute > looseEndM;
      }

      if (!converging && !looseEnd) continue;

      const merge = tangentMatchedEndpointMerge(coords, side, best.point, best, {
        mergeCurveM,
        curveSampleM,
        curveHandleM,
        maxDirectSnapTangentDeg,
      });
      coords.length = 0;
      coords.push(...merge.coords);
      changed = true;
      snappedCount += 1;
      if (merge.curved) curvedCount += 1;
    }
    if (changed) {
      const properties = {
        ...f.properties,
        same_color_endpoint_snapped: true,
      };
      if (curvedCount > 0) {
        properties.same_color_y_join_fabric = true;
        properties.same_color_y_join_fabric_count =
          Number(f.properties?.same_color_y_join_fabric_count ?? 0) + curvedCount;
      }
      out[i] = {
        ...f,
        geometry: { ...f.geometry, coordinates: coords },
        properties,
      };
    }
  }

  return { features: out, snappedCount };
}
