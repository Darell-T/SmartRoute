// frontend/scripts/build/same-color-merge.ts
// Phase 3d: color-scoped merge pass that unifies same-color OpenData polyline
// overlaps into a single trunk + clipped branches.
//
// Pure module -- no fs, process, globalThis, require.
// Imports only from ./spine.ts and ./physical-bundle.ts.

import {
  computePairOverlap,
  resamplePolyline,
  pointToPolylineMinDistM,
} from "./physical-bundle.ts";
import type { PairOverlapResult, Spine } from "./physical-bundle.ts";
import type { LineStringGeometry, Position, RouteId } from "./types.ts";

const EARTH_RADIUS_M = 6371000;

export type SameColorCorridor = {
  corridor_id: string;
  color?: string;
  route_ids?: RouteId[];
  geometry: LineStringGeometry;
  length_m?: number | null;
};

export type SameColorOverlapOptions = {
  sharedFractionMin?: number;
  sharedLenMinM?: number;
  avgDistMaxM?: number;
  tangentMaxDeg?: number;
  resampleM?: number;
};

type SameColorOverlapGates = {
  sharedFractionMin: number;
  sharedLenMinM: number;
  avgDistMaxM: number;
  tangentMaxDeg: number;
  resampleM: number;
};

export type SameColorMergeGroup = {
  color: string;
  member_corridor_ids: string[];
  trunk_corridor_id: string;
  member_route_ids_union?: RouteId[];
};

export type SameColorReject = {
  color: string;
  corridor_id_a: string;
  corridor_id_b: string;
  avgDistM: number;
  sharedFractionShorter: number;
  sharedLenM: number;
  tangentDeltaAvgDeg: number;
  reject_reason: string;
};

export type SameColorOverlapResult = {
  groups: SameColorMergeGroup[];
  rejects: SameColorReject[];
};

export type SameColorMergeOptions = {
  minBranchLenM?: number;
  resampleM?: number;
  avgDistMaxM?: number;
  routeCoverageMap?: Map<RouteId, number> | null;
  connectorMaxM?: number;
  maxTwoPointBranchLenM?: number;
  longStraightBranchTangentMaxDeg?: number;
};

type SameColorMergeGates = {
  minBranchLenM: number;
  resampleM: number;
  avgDistMaxM: number;
  routeCoverageMap: Map<RouteId, number> | null;
  connectorMaxM: number;
  maxTwoPointBranchLenM: number;
  longStraightBranchTangentMaxDeg: number;
};

export type SameColorConnector = {
  endpoint_kind: "start" | "end";
  branch_coordinate: Position;
  trunk_coordinate: Position;
  distance_m: number;
  coordinates: [Position, Position];
  route_ids: RouteId[];
  color: string;
};

export type SameColorTrunkUpdate = {
  corridor_id: string;
  route_ids: RouteId[];
  color_route_ids: Record<string, RouteId[]>;
  merged_from_corridor_ids: string[];
};

export type SameColorBranchUpdate =
  | {
      corridor_id: string;
      drop: true;
      reason: string;
      newCoords?: never;
      connector?: never;
    }
  | {
      corridor_id: string;
      newCoords: Position[];
      connector: SameColorConnector | null;
      drop?: undefined;
      reason?: undefined;
    };

export type SameColorMergeSkippedResult = {
  skipped: { reason: string };
  trunkUpdates?: never;
  branchUpdates?: never;
};

export type SameColorMergeAppliedResult = {
  trunkUpdates: SameColorTrunkUpdate;
  branchUpdates: SameColorBranchUpdate[];
  skipped?: undefined;
};

export type SameColorMergeResult = SameColorMergeSkippedResult | SameColorMergeAppliedResult;

type Vec2 = [number, number];

type SegmentProjection = {
  coordinate: Position;
  distance_m: number;
  t: number;
};

type PolylineProjection = SegmentProjection & {
  segment_index: number;
  arc_m: number;
};

type SampleRun = {
  start: number;
  len: number;
};

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number): number => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function haversinePolylineM(coords: Position[]): number {
  let total = 0;
  for (let i = 1; i < coords.length; i++) {
    total += haversineM(coords[i - 1], coords[i]);
  }
  return total;
}

function cumulativeArcLengths(coords: Position[]): number[] {
  const arcs = [0];
  for (let index = 1; index < coords.length; index += 1) {
    arcs.push(arcs[index - 1] + haversineM(coords[index - 1], coords[index]));
  }
  return arcs;
}

function interpolateAtArc(coords: Position[], arcs: number[], targetArc: number): Position {
  if (targetArc <= 0) return coords[0];
  const total = arcs[arcs.length - 1];
  if (targetArc >= total) return coords[coords.length - 1];
  for (let index = 1; index < coords.length; index += 1) {
    if (arcs[index] >= targetArc) {
      const segmentLength = arcs[index] - arcs[index - 1];
      if (segmentLength === 0) return coords[index];
      const t = (targetArc - arcs[index - 1]) / segmentLength;
      const from = coords[index - 1];
      const to = coords[index];
      return [
        from[0] + (to[0] - from[0]) * t,
        from[1] + (to[1] - from[1]) * t,
      ];
    }
  }
  return coords[coords.length - 1];
}

function slicePolylineByArc(coords: Position[], startArc: number, endArc: number): Position[] {
  const arcs = cumulativeArcLengths(coords);
  const total = arcs[arcs.length - 1];
  const start = Math.max(0, Math.min(total, startArc));
  const end = Math.max(0, Math.min(total, endArc));
  if (end - start <= 0.5) return [];
  const out = [interpolateAtArc(coords, arcs, start)];
  for (let index = 0; index < coords.length; index += 1) {
    const arc = arcs[index];
    if (arc > start && arc < end) out.push(coords[index]);
  }
  const endPoint = interpolateAtArc(coords, arcs, end);
  if (haversineM(out[out.length - 1], endPoint) > 0.01) out.push(endPoint);
  return out.length >= 2 ? out : [];
}

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function projectPointToSegment(point: Position, a: Position, b: Position): SegmentProjection {
  const meanLat = (point[1] + a[1] + b[1]) / 3;
  const mx = Math.max(1, metersPerDegLng(meanLat));
  const my = 111320;
  const px = point[0] * mx;
  const py = point[1] * my;
  const ax = a[0] * mx;
  const ay = a[1] * my;
  const bx = b[0] * mx;
  const by = b[1] * my;
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  const projected: Position = [(ax + t * dx) / mx, (ay + t * dy) / my];
  return {
    coordinate: projected,
    distance_m: haversineM(point, projected),
    t,
  };
}

function projectPointToPolyline(point: Position, coords: Position[]): PolylineProjection | null {
  let best: PolylineProjection | null = null;
  const arcs = cumulativeArcLengths(coords);
  for (let i = 1; i < coords.length; i += 1) {
    const projection = projectPointToSegment(point, coords[i - 1], coords[i]);
    if (!best || projection.distance_m < best.distance_m) {
      best = {
        ...projection,
        segment_index: i,
        arc_m: arcs[i - 1] + (arcs[i] - arcs[i - 1]) * projection.t,
      };
    }
  }
  return best;
}

function vectorMeters(from: Position, to: Position): Vec2 {
  const meanLat = (from[1] + to[1]) / 2;
  const mx = Math.max(1, metersPerDegLng(meanLat));
  return [(to[0] - from[0]) * mx, (to[1] - from[1]) * 111320];
}

function angleBetweenDeg(a: Vec2, b: Vec2): number {
  const aLen = Math.hypot(a[0], a[1]);
  const bLen = Math.hypot(b[0], b[1]);
  if (aLen < 1e-9 || bLen < 1e-9) return 180;
  const dot = (a[0] * b[0] + a[1] * b[1]) / (aLen * bLen);
  return (Math.acos(Math.max(-1, Math.min(1, dot))) * 180) / Math.PI;
}

function minUndirectedAngleDeg(a: Vec2, b: Vec2): number {
  return Math.min(angleBetweenDeg(a, b), angleBetweenDeg(a, [-b[0], -b[1]]));
}

function trunkTangentAtProjection(trunkCoords: Position[], projection: PolylineProjection | null): Vec2 | null {
  if (!projection || !Number.isFinite(projection.segment_index)) return null;
  const index = Math.max(1, Math.min(trunkCoords.length - 1, projection.segment_index));
  return vectorMeters(trunkCoords[index - 1], trunkCoords[index]);
}

function alignedLongStraightBranchIsSafe(
  clippedCoords: Position[],
  trunkCoords: Position[],
  {
    connectorMaxM,
    tangentMaxDeg,
  }: { connectorMaxM: number; tangentMaxDeg: number },
): boolean {
  if (!Array.isArray(clippedCoords) || clippedCoords.length !== 2) return false;
  const endpoints = [
    { endpoint_kind: "start" as const, coordinate: clippedCoords[0], tangent: vectorMeters(clippedCoords[1], clippedCoords[0]) },
    { endpoint_kind: "end" as const, coordinate: clippedCoords[1], tangent: vectorMeters(clippedCoords[0], clippedCoords[1]) },
  ];

  let best: { projection: PolylineProjection; tangentDeltaDeg: number } | null = null;
  for (const endpoint of endpoints) {
    const projection = projectPointToPolyline(endpoint.coordinate, trunkCoords);
    if (!projection || projection.distance_m > connectorMaxM) continue;
    const trunkTangent = trunkTangentAtProjection(trunkCoords, projection);
    if (!trunkTangent) continue;
    const tangentDeltaDeg = minUndirectedAngleDeg(endpoint.tangent, trunkTangent);
    if (!best || tangentDeltaDeg < best.tangentDeltaDeg) {
      best = { projection, tangentDeltaDeg };
    }
  }

  return Boolean(best && best.tangentDeltaDeg <= tangentMaxDeg);
}

function connectorForClippedBranch({
  clippedCoords,
  trunkCoords,
  branch,
  color,
  connectorMaxM,
}: {
  clippedCoords: Position[];
  trunkCoords: Position[];
  branch: SameColorCorridor;
  color: string;
  connectorMaxM: number;
}): SameColorConnector | null {
  if (!Array.isArray(clippedCoords) || clippedCoords.length < 2) return null;
  if (!Array.isArray(trunkCoords) || trunkCoords.length < 2) return null;
  const endpoints = [
    { endpoint_kind: "start" as const, coordinate: clippedCoords[0] },
    { endpoint_kind: "end" as const, coordinate: clippedCoords[clippedCoords.length - 1] },
  ];
  let best: Omit<SameColorConnector, "coordinates" | "route_ids" | "color"> | null = null;
  for (const endpoint of endpoints) {
    const projection = projectPointToPolyline(endpoint.coordinate, trunkCoords);
    if (!projection) continue;
    if (!best || projection.distance_m < best.distance_m) {
      best = {
        endpoint_kind: endpoint.endpoint_kind,
        branch_coordinate: endpoint.coordinate,
        trunk_coordinate: projection.coordinate,
        distance_m: projection.distance_m,
      };
    }
  }
  if (!best || best.distance_m > connectorMaxM) return null;
  return {
    ...best,
    coordinates: [best.branch_coordinate, best.trunk_coordinate],
    route_ids: [...(branch.route_ids ?? [])].sort(),
    color,
    distance_m: Number(best.distance_m.toFixed(2)),
  };
}

function overlapRejectReason(overlap: PairOverlapResult, gates: SameColorOverlapGates): string | null {
  if (overlap.sharedFractionShorter < gates.sharedFractionMin) return "shared_fraction_too_low";
  if (overlap.sharedLenM < gates.sharedLenMinM) return "shared_len_too_short";
  if (overlap.tangentDeltaAvgDeg > gates.tangentMaxDeg) return "tangent_delta_too_large";
  return null;
}

function mergeGroupFromSpines(color: string, members: Spine[]): SameColorMergeGroup {
  let trunk = members[0];
  const routeIdSet = new Set<RouteId>();
  for (const member of members) {
    if ((member.length_m ?? 0) > (trunk.length_m ?? 0)) trunk = member;
    for (const routeId of member.route_ids ?? []) routeIdSet.add(routeId);
  }
  return {
    color,
    member_corridor_ids: members.map((member) => member.spine_id),
    trunk_corridor_id: trunk.spine_id,
    member_route_ids_union: [...routeIdSet].sort(),
  };
}

function collectOverlapGroupsForColor(
  color: string,
  colorCorridors: SameColorCorridor[],
  gates: SameColorOverlapGates,
): SameColorOverlapResult {
  if (colorCorridors.length < 2) return { groups: [], rejects: [] };

  const spines = colorCorridors.map((corridor) => ({
    spine_id: corridor.corridor_id,
    geometry: corridor.geometry,
    length_m: corridor.length_m ?? haversinePolylineM(corridor.geometry.coordinates),
    route_ids: corridor.route_ids ?? [],
  }));
  const parent = Array.from({ length: spines.length }, (_, index) => index);
  const rank = Array.from({ length: spines.length }, () => 0);
  const find = (index: number): number =>
    parent[index] === index ? index : (parent[index] = find(parent[index]));
  const union = (left: number, right: number): void => {
    const leftRoot = find(left);
    const rightRoot = find(right);
    if (leftRoot === rightRoot) return;
    if (rank[leftRoot] < rank[rightRoot]) parent[leftRoot] = rightRoot;
    else if (rank[leftRoot] > rank[rightRoot]) parent[rightRoot] = leftRoot;
    else {
      parent[rightRoot] = leftRoot;
      rank[leftRoot] += 1;
    }
  };
  const rejects: SameColorReject[] = [];

  for (let i = 0; i < spines.length; i += 1) {
    for (let j = i + 1; j < spines.length; j += 1) {
      const overlap = computePairOverlap(spines[i], spines[j], {
        resampleM: gates.resampleM,
        distMaxM: gates.avgDistMaxM,
      });
      const reason = overlapRejectReason(overlap, gates);
      if (reason) {
        rejects.push({
          color,
          corridor_id_a: spines[i].spine_id,
          corridor_id_b: spines[j].spine_id,
          avgDistM: overlap.avgDistM,
          sharedFractionShorter: overlap.sharedFractionShorter,
          sharedLenM: overlap.sharedLenM,
          tangentDeltaAvgDeg: overlap.tangentDeltaAvgDeg,
          reject_reason: reason,
        });
      }
      else union(i, j);
    }
  }

  const memberIndicesByRoot = new Map<number, number[]>();
  for (let index = 0; index < spines.length; index += 1) {
    const root = find(index);
    const members = memberIndicesByRoot.get(root);
    if (members) members.push(index);
    else memberIndicesByRoot.set(root, [index]);
  }
  const groups: SameColorMergeGroup[] = [];
  for (const memberIndices of memberIndicesByRoot.values()) {
    if (memberIndices.length < 2) continue;
    const members = memberIndices.map((index) => spines[index]);
    groups.push(mergeGroupFromSpines(color, members));
  }
  return { groups, rejects };
}

/**
 * Group corridors by color and physical overlap.
 *
 * We do not use groupSpinesIntoPhysicalBundles' avgDistM gate here because a
 * branch-into-trunk pattern's diverging tail inflates avgDist even when
 * sharedFraction is huge. Gate on sharedFractionShorter + sharedLenM +
 * in-shared tangentDelta only.
 */
export function groupCorridorsByColorAndOverlap(
  corridors: SameColorCorridor[],
  options: SameColorOverlapOptions = {},
): SameColorOverlapResult {
  const gates: SameColorOverlapGates = {
    sharedFractionMin: options.sharedFractionMin ?? 0.55,
    sharedLenMinM: options.sharedLenMinM ?? 100,
    avgDistMaxM: options.avgDistMaxM ?? 15,
    tangentMaxDeg: options.tangentMaxDeg ?? 30,
    resampleM: options.resampleM ?? 25,
  };

  const groups: SameColorMergeGroup[] = [];
  const rejects: SameColorReject[] = [];
  const corridorsByColor = new Map<string, SameColorCorridor[]>();
  for (const corridor of corridors) {
    if (!corridor.color) continue;
    const bucket = corridorsByColor.get(corridor.color);
    if (bucket) bucket.push(corridor);
    else corridorsByColor.set(corridor.color, [corridor]);
  }
  for (const [color, colorCorridors] of corridorsByColor) {
    const collected = collectOverlapGroupsForColor(color, colorCorridors, gates);
    groups.push(...collected.groups);
    rejects.push(...collected.rejects);
  }
  return { groups, rejects };
}

function branchNonOverlapLengthM(
  branch: SameColorCorridor,
  trunkResampled: Position[],
  gates: SameColorMergeGates,
): number {
  const branchResampled = resamplePolyline(branch.geometry.coordinates, gates.resampleM);
  let nonOverlapSampleCount = 0;
  for (const point of branchResampled) {
    if (pointToPolylineMinDistM(point, trunkResampled) > gates.avgDistMaxM) nonOverlapSampleCount += 1;
  }
  const branchLen = branch.length_m ?? haversinePolylineM(branch.geometry.coordinates);
  return (nonOverlapSampleCount / Math.max(1, branchResampled.length)) * branchLen;
}

function wouldBreakRouteConnectivity(
  branchIds: string[],
  trunk: SameColorCorridor,
  corridorsById: Map<string, SameColorCorridor>,
  trunkResampled: Position[],
  gates: SameColorMergeGates,
): boolean {
  if (!gates.routeCoverageMap) return false;
  const trunkRouteSet = new Set(trunk.route_ids ?? []);
  for (const branchId of branchIds) {
    const branch = corridorsById.get(branchId);
    if (!branch) continue;
    for (const routeId of branch.route_ids ?? []) {
      if (trunkRouteSet.has(routeId)) continue;
      const coverage = gates.routeCoverageMap.get(routeId) ?? 0;
      if (coverage > 1) continue;
      if (branchNonOverlapLengthM(branch, trunkResampled, gates) < gates.minBranchLenM) return true;
    }
  }
  return false;
}

function longestOutOfOverlapRun(inOverlap: boolean[]): SampleRun {
  let longest: SampleRun = { start: -1, len: 0 };
  let current: SampleRun = { start: -1, len: 0 };
  for (let index = 0; index < inOverlap.length; index += 1) {
    if (!inOverlap[index]) {
      if (current.start === -1) current = { start: index, len: 1 };
      else current.len += 1;
      if (current.len > longest.len) longest = { ...current };
    } else {
      current = { start: -1, len: 0 };
    }
  }
  return longest;
}

function clipBranchToOutOfOverlapRun(
  branchCoords: Position[],
  branchResampled: Position[],
  run: SampleRun,
): Position[] {
  const originalArcs = cumulativeArcLengths(branchCoords);
  const sampledArcs = cumulativeArcLengths(branchResampled);
  const totalBranchLen = originalArcs[originalArcs.length - 1];
  const sampledTotal = sampledArcs[sampledArcs.length - 1];
  const runEnd = run.start + run.len - 1;
  const startFrac = sampledTotal > 0 ? sampledArcs[run.start] / sampledTotal : 0;
  const endFrac = sampledTotal > 0 ? sampledArcs[runEnd] / sampledTotal : 1;
  return slicePolylineByArc(branchCoords, startFrac * totalBranchLen, endFrac * totalBranchLen);
}

function shouldDropLongChord(
  clippedCoords: Position[],
  remainingLen: number,
  trunkCoords: Position[],
  gates: SameColorMergeGates,
): boolean {
  if (clippedCoords.length > 2) return false;
  if (remainingLen <= gates.maxTwoPointBranchLenM) return false;
  return !alignedLongStraightBranchIsSafe(clippedCoords, trunkCoords, {
    connectorMaxM: gates.connectorMaxM,
    tangentMaxDeg: gates.longStraightBranchTangentMaxDeg,
  });
}

function droppedBranch(corridorId: string, reason: string): SameColorBranchUpdate {
  return { corridor_id: corridorId, drop: true, reason };
}

function branchUpdateForMember(
  branchId: string,
  trunk: SameColorCorridor,
  color: string,
  corridorsById: Map<string, SameColorCorridor>,
  trunkResampled: Position[],
  gates: SameColorMergeGates,
): SameColorBranchUpdate {
  const branch = corridorsById.get(branchId);
  if (!branch) return droppedBranch(branchId, "branch_not_found");
  const branchCoords = branch.geometry.coordinates;
  const branchResampled = resamplePolyline(branchCoords, gates.resampleM);
  if (branchResampled.length < 2) return droppedBranch(branchId, "degenerate_branch");

  const inOverlap = branchResampled.map(
    (point) => pointToPolylineMinDistM(point, trunkResampled) <= gates.avgDistMaxM,
  );
  const longestRun = longestOutOfOverlapRun(inOverlap);
  if (longestRun.start === -1 || longestRun.len < 2) return droppedBranch(branchId, "fully_contained");
  const runEnd = longestRun.start + longestRun.len - 1;
  if (longestRun.start !== 0 && runEnd !== inOverlap.length - 1) {
    return droppedBranch(branchId, "middle_floater_dropped");
  }

  const clippedCoords = clipBranchToOutOfOverlapRun(branchCoords, branchResampled, longestRun);
  if (clippedCoords.length < 2) return droppedBranch(branchId, "fully_contained");
  const remainingLen = haversinePolylineM(clippedCoords);
  if (remainingLen < gates.minBranchLenM) return droppedBranch(branchId, "fully_contained");
  if (shouldDropLongChord(clippedCoords, remainingLen, trunk.geometry.coordinates, gates)) {
    return droppedBranch(branchId, "low_detail_long_chord_dropped");
  }

  return {
    corridor_id: branchId,
    newCoords: clippedCoords,
    connector: connectorForClippedBranch({
      clippedCoords,
      trunkCoords: trunk.geometry.coordinates,
      branch,
      color,
      connectorMaxM: gates.connectorMaxM,
    }),
  };
}

function resolveMergeGates(options: SameColorMergeOptions): SameColorMergeGates {
  return {
    minBranchLenM: options.minBranchLenM ?? 30,
    resampleM: options.resampleM ?? 25,
    avgDistMaxM: options.avgDistMaxM ?? 15,
    routeCoverageMap: options.routeCoverageMap ?? null,
    connectorMaxM: options.connectorMaxM ?? 35,
    maxTwoPointBranchLenM: options.maxTwoPointBranchLenM ?? 250,
    longStraightBranchTangentMaxDeg: options.longStraightBranchTangentMaxDeg ?? 25,
  };
}

/**
 * Merge a same-color group: trunk gains union of route_ids; shorter branches
 * are clipped to their non-overlapping divergence portion.
 */
export function mergeSameColorGroup(
  group: SameColorMergeGroup,
  corridorsById: Map<string, SameColorCorridor>,
  options: SameColorMergeOptions = {},
): SameColorMergeResult {
  const gates = resolveMergeGates(options);
  const trunk = corridorsById.get(group.trunk_corridor_id);
  if (!trunk) return { skipped: { reason: "trunk_not_found" } };

  const branchIds = group.member_corridor_ids.filter((id) => id !== group.trunk_corridor_id);
  const trunkResampled = resamplePolyline(trunk.geometry.coordinates, gates.resampleM);
  if (wouldBreakRouteConnectivity(branchIds, trunk, corridorsById, trunkResampled, gates)) {
    return { skipped: { reason: "would_break_route_connectivity" } };
  }

  const trunkRouteIds = [...new Set<RouteId>(group.member_route_ids_union ?? trunk.route_ids ?? [])].sort();
  return {
    trunkUpdates: {
      corridor_id: group.trunk_corridor_id,
      route_ids: trunkRouteIds,
      color_route_ids: { [group.color]: trunkRouteIds },
      merged_from_corridor_ids: [...group.member_corridor_ids],
    },
    branchUpdates: branchIds.map((branchId) =>
      branchUpdateForMember(branchId, trunk, group.color, corridorsById, trunkResampled, gates),
    ),
  };
}
