import type { Feature, LineStringGeometry, Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;
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
        point: interpolatePosition(coords[i - 1], coords[i], t),
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

function interpolatePosition(start: Position, end: Position, t: number): Position {
  return [
    start[0] + (end[0] - start[0]) * t,
    start[1] + (end[1] - start[1]) * t,
  ];
}

function midpoint(left: Position, right: Position): Position {
  return [(left[0] + right[0]) / 2, (left[1] + right[1]) / 2];
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

function linearlySampleSegment(start: Position, end: Position, sampleM: number): Position[] {
  const distanceM = haversineM(start, end);
  const steps = Math.max(2, Math.ceil(distanceM / sampleM));
  const out: Position[] = [];
  for (let i = 0; i <= steps; i += 1) {
    out.push(interpolatePosition(start, end, i / steps));
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
    const point: Position = [
      u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
      u * u * start[1] + 2 * u * t * control[1] + t * t * end[1],
    ];
    out.push(point);
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
    const segmentStart = i === 1 ? prev : midpoint(prev, current);
    const segmentEnd = i === points.length - 2 ? next : midpoint(current, next);
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

type BranchCut = {
  point: Position;
  before: Position[];
};

type MottHavenJoinGeometry = {
  orientedBranch: CartographicFeature;
  newBranchCoords: Position[];
  curve: Position[];
};

type MottHavenJoinGates = {
  branchCutBackM: number;
  trunkMergeDownstreamM: number;
  sampleM: number;
  maxEndpointGapM: number;
  schematicPoints: Position[];
  bbox: Bounds;
};

function emptyJunctionResult(features: CartographicFeature[]): CartographicJunctionResult {
  return { features, appliedCount: 0, debugFeatures: [] };
}

function branchCutForMottHaven(
  orientedBranch: CartographicFeature,
  branchLength: number,
  gates: MottHavenJoinGates,
): BranchCut {
  const entryIndex = firstIndexInBBox(orientedBranch.geometry.coordinates, gates.bbox);
  if (entryIndex >= 0) {
    return {
      point: orientedBranch.geometry.coordinates[entryIndex],
      before: orientedBranch.geometry.coordinates.slice(0, entryIndex + 1),
    };
  }
  return splitAtDistance(
    orientedBranch.geometry.coordinates,
    Math.max(0, branchLength - gates.branchCutBackM),
  );
}

function mottHavenJoinGeometry(
  branch: CartographicFeature,
  trunk: CartographicFeature,
  gates: MottHavenJoinGates,
): MottHavenJoinGeometry | null {
  const orientedBranch = orientedTowardMottHavenEndpoint(branch);
  const orientedTrunk = orientedFromMottHavenEndpoint(trunk);
  const branchEndpoint = orientedBranch.geometry.coordinates[orientedBranch.geometry.coordinates.length - 1];
  const trunkEndpoint = orientedTrunk.geometry.coordinates[0];
  if (haversineM(branchEndpoint, trunkEndpoint) > gates.maxEndpointGapM) return null;

  const branchLength = polylineLengthM(orientedBranch.geometry.coordinates);
  const trunkLength = polylineLengthM(orientedTrunk.geometry.coordinates);
  if (branchLength <= gates.branchCutBackM + 20 || trunkLength <= gates.trunkMergeDownstreamM + 20) {
    return null;
  }

  const branchCut = branchCutForMottHaven(orientedBranch, branchLength, gates);
  const trunkMerge = splitAtDistance(orientedTrunk.geometry.coordinates, gates.trunkMergeDownstreamM);
  const approach = linearlySampleSegment(branchCut.point, trunkEndpoint, gates.sampleM);
  const loop = schematicCurveThrough(trunkEndpoint, trunkMerge.point, gates.schematicPoints, gates.sampleM);
  const curve = [...approach, ...loop.slice(1)];
  return {
    orientedBranch,
    newBranchCoords: [...branchCut.before.slice(0, -1), ...curve],
    curve,
  };
}

export function applyCartographicJunctionOverrides(
  features: CartographicFeature[],
  options: CartographicJunctionOptions = {},
): CartographicJunctionResult {
  const gates: MottHavenJoinGates = {
    branchCutBackM: options.branchCutBackM ?? DEFAULT_BRANCH_CUT_BACK_M,
    trunkMergeDownstreamM: options.trunkMergeDownstreamM ?? DEFAULT_TRUNK_MERGE_DOWNSTREAM_M,
    sampleM: options.sampleM ?? 8,
    maxEndpointGapM: options.maxEndpointGapM ?? 95,
    schematicPoints: options.schematicPoints ?? MOTT_HAVEN_SCHEMATIC_POINTS,
    bbox: options.bbox ?? MOTT_HAVEN_BBOX,
  };

  const branch = findMottHavenBranch(features);
  const trunk = findMottHavenTrunk(features);
  if (!branch || !trunk) return emptyJunctionResult(features);

  const join = mottHavenJoinGeometry(branch, trunk, gates);
  if (!join) return emptyJunctionResult(features);

  const repairedBranch = replaceFeaturePreservingOriginalDirection(
    branch,
    join.orientedBranch,
    join.newBranchCoords,
    {
      cartographic_junction_override: "mott_haven_5",
      cartographic_junction_override_applied: true,
      cartographic_junction_branch_cut_back_m: gates.branchCutBackM,
      cartographic_junction_trunk_merge_downstream_m: gates.trunkMergeDownstreamM,
    },
  );

  return {
    features: features.map((feature) => (feature === branch ? repairedBranch : feature)),
    appliedCount: 1,
    debugFeatures: [({
    type: "Feature",
    geometry: { type: "LineString", coordinates: (join.curve) },
    properties: {
        visual_feature_type: "cartographic_junction_override",
        cartographic_junction_override: "mott_haven_5",
        route_ids: ["5"],
        color: GREEN,
        branch_cut_back_m: (gates).branchCutBackM,
        trunk_merge_downstream_m: (gates).trunkMergeDownstreamM,
        length_m: Number(polylineLengthM((join.curve)).toFixed(2)),
    },
})],
  };
}
