import type { Feature, LineStringGeometry, Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 110574;
const GREEN = "#00933C";

type Bounds = {
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
};

type CartographicFeatureProperties = {
  route_ids?: string[];
  color_route_ids?: string[];
  color?: string;
  length_m?: number;
  corridor_id?: string;
  cartographic_junction_override?: string;
  cartographic_junction_override_applied?: boolean;
  cartographic_junction_branch_cut_back_m?: number;
  cartographic_junction_trunk_merge_downstream_m?: number;
  visual_feature_type?: string;
  branch_cut_back_m?: number;
  trunk_merge_downstream_m?: number;
  [key: string]: unknown;
};

type CartographicFeature = Feature<LineStringGeometry, CartographicFeatureProperties>;

type SplitPoint = {
  point: Position;
  before: Position[];
  after: Position[];
  index: number;
  t: number;
};

type CartographicJunctionOptions = {
  branchCutBackM?: number;
  trunkMergeDownstreamM?: number;
  sampleM?: number;
  maxEndpointGapM?: number;
  schematicPoints?: Position[];
  bbox?: Bounds;
};

type CartographicJunctionResult = {
  features: CartographicFeature[];
  appliedCount: number;
  debugFeatures: CartographicFeature[];
};

const MOTT_HAVEN_CENTER: Position = [-73.92825, 40.8166];
const MOTT_HAVEN_BBOX: Bounds = {
  minLon: -73.9335,
  maxLon: -73.9230,
  minLat: 40.8130,
  maxLat: 40.8230,
};
const MOTT_HAVEN_SCHEMATIC_POINTS: Position[] = [
  // Apple/Transit-style local loop after the E 149 St approach reaches the
  // station area. The approach itself is handled separately as a straight
  // street-aligned run so the line does not become a giant neighborhood-scale
  // chord.
  [-73.92985, 40.81695],
  [-73.93058, 40.81610],
  [-73.93022, 40.81512],
  [-73.92950, 40.81468],
];
const DEFAULT_BRANCH_CUT_BACK_M = 450;
const DEFAULT_TRUNK_MERGE_DOWNSTREAM_M = 300;

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function routeIdsOf(feature: CartographicFeature): string[] {
  const p = feature.properties ?? {};
  if (Array.isArray(p.color_route_ids)) return p.color_route_ids;
  if (Array.isArray(p.route_ids)) return p.route_ids;
  return [];
}

function sameRouteSet(routeIds: string[], expected: string[]): boolean {
  const left = [...new Set(routeIds)].sort().join("|");
  const right = [...expected].sort().join("|");
  return left === right;
}

function includesRoutes(routeIds: string[], expected: string[]): boolean {
  const set = new Set(routeIds);
  return expected.every((routeId) => set.has(routeId));
}

function polylineLengthM(coords: Position[]): number {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) total += haversineM(coords[i - 1], coords[i]);
  return total;
}

function pointAlong(coords: Position[], distanceM: number): Omit<SplitPoint, "before" | "after"> {
  let walked = 0;
  for (let i = 1; i < coords.length; i += 1) {
    const seg = haversineM(coords[i - 1], coords[i]);
    if (walked + seg >= distanceM) {
      const t = seg === 0 ? 0 : (distanceM - walked) / seg;
      return {
        index: i - 1,
        t,
        point: [
          coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t,
          coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t,
        ],
      };
    }
    walked += seg;
  }
  return { index: Math.max(0, coords.length - 2), t: 1, point: coords[coords.length - 1] };
}

function splitAtDistance(coords: Position[], distanceM: number): SplitPoint {
  const split = pointAlong(coords, distanceM);
  return {
    point: split.point,
    before: [...coords.slice(0, split.index + 1), split.point],
    after: [split.point, ...coords.slice(split.index + 1)],
    index: split.index,
    t: split.t,
  };
}

function reverseFeatureDirection(feature: CartographicFeature): CartographicFeature {
  return {
    ...feature,
    geometry: {
      ...feature.geometry,
      coordinates: feature.geometry.coordinates.slice().reverse(),
    },
  };
}

function closestEndpointIndex(coords: Position[], point: Position): number {
  return haversineM(coords[0], point) <= haversineM(coords[coords.length - 1], point) ? 0 : coords.length - 1;
}

function orientedTowardMottHavenEndpoint(feature: CartographicFeature): CartographicFeature {
  const coords = feature.geometry.coordinates;
  return closestEndpointIndex(coords, MOTT_HAVEN_CENTER) === coords.length - 1
    ? feature
    : reverseFeatureDirection(feature);
}

function orientedFromMottHavenEndpoint(feature: CartographicFeature): CartographicFeature {
  const coords = feature.geometry.coordinates;
  return closestEndpointIndex(coords, MOTT_HAVEN_CENTER) === 0
    ? feature
    : reverseFeatureDirection(feature);
}

function vectorMeters(from: Position, to: Position): Position {
  const lat = (from[1] + to[1]) / 2;
  return [
    (to[0] - from[0]) * metersPerDegLng(lat),
    (to[1] - from[1]) * M_PER_DEG_LAT,
  ];
}

function normalize(v: Position): Position {
  const len = Math.hypot(v[0], v[1]);
  return len < 1e-9 ? [0, 0] : [v[0] / len, v[1] / len];
}

function projectAtLat(point: Position, originLat: number): Position {
  return [point[0] * metersPerDegLng(originLat), point[1] * M_PER_DEG_LAT];
}

function unprojectAtLat(point: Position, originLat: number): Position {
  return [point[0] / metersPerDegLng(originLat), point[1] / M_PER_DEG_LAT];
}

function hermiteCurve(
  start: Position,
  end: Position,
  startTangent: Position,
  endTangent: Position,
  sampleM: number,
): Position[] {
  const originLat = (start[1] + end[1]) / 2;
  const p0 = projectAtLat(start, originLat);
  const p1 = projectAtLat(end, originLat);
  const distanceM = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
  const handleM = Math.max(65, Math.min(230, distanceM * 0.62));
  const m0 = [startTangent[0] * handleM, startTangent[1] * handleM];
  const m1 = [endTangent[0] * handleM, endTangent[1] * handleM];
  const steps = Math.max(8, Math.ceil(distanceM / sampleM));
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
    ] as Position, originLat));
  }
  return out;
}

function linearlySampleSegment(start: Position, end: Position, sampleM: number): Position[] {
  const distanceM = haversineM(start, end);
  const steps = Math.max(2, Math.ceil(distanceM / sampleM));
  const out = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    out.push([
      start[0] + (end[0] - start[0]) * t,
      start[1] + (end[1] - start[1]) * t,
    ] as Position);
  }
  return out;
}

function quadraticCurve(start: Position, control: Position, end: Position, sampleM: number): Position[] {
  const distanceM = haversineM(start, control) + haversineM(control, end);
  const steps = Math.max(12, Math.ceil(distanceM / sampleM));
  const out: Position[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const u = 1 - t;
    out.push([
      u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
      u * u * start[1] + 2 * u * t * control[1] + t * t * end[1],
    ] as Position);
  }
  return out;
}

function schematicCurveThrough(
  start: Position,
  end: Position,
  interiorPoints: Position[],
  sampleM: number,
): Position[] {
  const points = [start, ...interiorPoints, end];
  if (points.length < 3) return linearlySampleSegment(start, end, sampleM);

  const output = [start];
  for (let i = 1; i < points.length - 1; i += 1) {
    const prev = points[i - 1];
    const current = points[i];
    const next = points[i + 1];
    if (!prev || !current || !next) continue;
    const segmentStart: Position = i === 1
      ? prev
      : [(prev[0] + current[0]) / 2, (prev[1] + current[1]) / 2] as Position;
    const segmentEnd: Position = i === points.length - 2
      ? next
      : [(current[0] + next[0]) / 2, (current[1] + next[1]) / 2] as Position;
    const segment = quadraticCurve(segmentStart, current, segmentEnd, sampleM);
    output.push(...segment.slice(1));
  }
  return output;
}

function inBBox(point: Position, bbox: Bounds): boolean {
  return (
    point[0] >= bbox.minLon &&
    point[0] <= bbox.maxLon &&
    point[1] >= bbox.minLat &&
    point[1] <= bbox.maxLat
  );
}

function firstIndexInBBox(coords: Position[], bbox: Bounds): number {
  return coords.findIndex((coord) => inBBox(coord, bbox));
}

function findMottHavenBranch(features: CartographicFeature[]): CartographicFeature | undefined {
  return features.find((feature) => (
    feature.geometry?.type === "LineString" &&
    String(feature.properties?.color ?? "").toUpperCase() === GREEN &&
    sameRouteSet(routeIdsOf(feature), ["5"]) &&
    feature.geometry.coordinates.some((coord) => haversineM(coord, MOTT_HAVEN_CENTER) <= 700)
  ));
}

function findMottHavenTrunk(features: CartographicFeature[]): CartographicFeature | undefined {
  return features.find((feature) => (
    feature.geometry?.type === "LineString" &&
    String(feature.properties?.color ?? "").toUpperCase() === GREEN &&
    includesRoutes(routeIdsOf(feature), ["4", "5"]) &&
    feature.geometry.coordinates.some((coord) => haversineM(coord, MOTT_HAVEN_CENTER) <= 700)
  ));
}

function replaceFeaturePreservingOriginalDirection(
  originalFeature: CartographicFeature,
  orientedFeature: CartographicFeature,
  newOrientedCoordinates: Position[],
  properties: Partial<CartographicFeatureProperties>,
): CartographicFeature {
  const originalStartsAtOrientedStart =
    haversineM(originalFeature.geometry.coordinates[0], orientedFeature.geometry.coordinates[0]) <=
    haversineM(originalFeature.geometry.coordinates[0], orientedFeature.geometry.coordinates[orientedFeature.geometry.coordinates.length - 1]);
  const coordinates = originalStartsAtOrientedStart
    ? newOrientedCoordinates
    : newOrientedCoordinates.slice().reverse();

  return {
    ...originalFeature,
    geometry: {
      ...originalFeature.geometry,
      coordinates,
    },
    properties: {
      ...originalFeature.properties,
      ...properties,
      length_m: Number(polylineLengthM(coordinates).toFixed(2)),
    },
  };
}

export function applyCartographicJunctionOverrides(
  features: CartographicFeature[],
  options: CartographicJunctionOptions = {},
): CartographicJunctionResult {
  const {
    branchCutBackM = DEFAULT_BRANCH_CUT_BACK_M,
    trunkMergeDownstreamM = DEFAULT_TRUNK_MERGE_DOWNSTREAM_M,
    sampleM = 8,
    maxEndpointGapM = 95,
    schematicPoints = MOTT_HAVEN_SCHEMATIC_POINTS,
    bbox = MOTT_HAVEN_BBOX,
  } = options;

  const branch = findMottHavenBranch(features);
  const trunk = findMottHavenTrunk(features);
  if (!branch || !trunk) return { features, appliedCount: 0, debugFeatures: [] };

  const orientedBranch = orientedTowardMottHavenEndpoint(branch);
  const orientedTrunk = orientedFromMottHavenEndpoint(trunk);
  const branchEndpoint = orientedBranch.geometry.coordinates[orientedBranch.geometry.coordinates.length - 1];
  const trunkEndpoint = orientedTrunk.geometry.coordinates[0];
  if (haversineM(branchEndpoint, trunkEndpoint) > maxEndpointGapM) {
    return { features, appliedCount: 0, debugFeatures: [] };
  }

  const branchLength = polylineLengthM(orientedBranch.geometry.coordinates);
  const trunkLength = polylineLengthM(orientedTrunk.geometry.coordinates);
  if (branchLength <= branchCutBackM + 20 || trunkLength <= trunkMergeDownstreamM + 20) {
    return { features, appliedCount: 0, debugFeatures: [] };
  }

  const entryIndex = firstIndexInBBox(orientedBranch.geometry.coordinates, bbox);
  const branchCut = entryIndex >= 0
    ? {
        point: orientedBranch.geometry.coordinates[entryIndex],
        before: orientedBranch.geometry.coordinates.slice(0, entryIndex + 1),
      }
    : splitAtDistance(
        orientedBranch.geometry.coordinates,
        Math.max(0, branchLength - branchCutBackM),
      );
  const trunkMerge = splitAtDistance(orientedTrunk.geometry.coordinates, trunkMergeDownstreamM);
  const branchStem = branchCut.before;
  const approach = linearlySampleSegment(branchCut.point, trunkEndpoint, sampleM);
  const loop = schematicCurveThrough(trunkEndpoint, trunkMerge.point, schematicPoints, sampleM);
  const curve = [...approach, ...loop.slice(1)];
  const newBranchCoords = [
    ...branchStem.slice(0, -1),
    ...curve,
  ];

  const repairedBranch = replaceFeaturePreservingOriginalDirection(branch, orientedBranch, newBranchCoords, {
    cartographic_junction_override: "mott_haven_5",
    cartographic_junction_override_applied: true,
    cartographic_junction_branch_cut_back_m: branchCutBackM,
    cartographic_junction_trunk_merge_downstream_m: trunkMergeDownstreamM,
  });

  const featuresOut = features.map((feature) => (feature === branch ? repairedBranch : feature));
  return {
    features: featuresOut,
    appliedCount: 1,
    debugFeatures: [{
      type: "Feature",
      geometry: { type: "LineString", coordinates: curve },
      properties: {
        visual_feature_type: "cartographic_junction_override",
        cartographic_junction_override: "mott_haven_5",
        route_ids: ["5"],
        color: GREEN,
        branch_cut_back_m: branchCutBackM,
        trunk_merge_downstream_m: trunkMergeDownstreamM,
        length_m: Number(polylineLengthM(curve).toFixed(2)),
      },
    }],
  };
}
