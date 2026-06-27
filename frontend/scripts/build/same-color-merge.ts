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
import type { Spine } from "./physical-bundle.ts";
import type { LineStringGeometry, Position, RouteId } from "./types.ts";

const EARTH_RADIUS_M = 6371000;

export type SameColorCorridor = {
  corridor_id: string;
  color?: string;
  route_ids?: RouteId[];
  geometry: LineStringGeometry;
  length_m?: number | null;
  [key: string]: unknown;
};

export type SameColorOverlapOptions = {
  sharedFractionMin?: number;
  sharedLenMinM?: number;
  avgDistMaxM?: number;
  tangentMaxDeg?: number;
  resampleM?: number;
};

export type SameColorMergeGroup = {
  color: string;
  member_corridor_ids: string[];
  trunk_corridor_id: string;
  member_route_ids_union: RouteId[];
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
    { endpoint_kind: "start", coordinate: clippedCoords[0], tangent: vectorMeters(clippedCoords[1], clippedCoords[0]) },
    { endpoint_kind: "end", coordinate: clippedCoords[1], tangent: vectorMeters(clippedCoords[0], clippedCoords[1]) },
  ] satisfies Array<{ endpoint_kind: "start" | "end"; coordinate: Position; tangent: Vec2 }>;

  let best: { projection: PolylineProjection; tangentDeltaDeg: number } | null = null;
  for (const endpoint of endpoints) {
    const projection = projectPointToPolyline(endpoint.coordinate, trunkCoords);
    if (!projection || projection.distance_m > connectorMaxM) continue;
    const trunkTangent = trunkTangentAtProjection(trunkCoords, projection);
    if (!trunkTangent) continue;
    const tangentDeltaDeg = minUndirectedAngleDeg(endpoint.tangent, trunkTangent);
    if (!best || tangentDeltaDeg < best.tangentDeltaDeg) {
      best = { ...endpoint, projection, tangentDeltaDeg };
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
    { endpoint_kind: "start", coordinate: clippedCoords[0] },
    { endpoint_kind: "end", coordinate: clippedCoords[clippedCoords.length - 1] },
  ] satisfies Array<{ endpoint_kind: "start" | "end"; coordinate: Position }>;
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

/**
 * Group corridors by color and physical overlap.
 *
 * @param {Array} corridors  Array of corridor objects. Each must have
 *   { corridor_id, color, route_ids, geometry: { coordinates }, length_m }.
 * @param {object} options
 * @param {number} [options.sharedFractionMin=0.55]
 * @param {number} [options.sharedLenMinM=100]
 * @param {number} [options.avgDistMaxM=15]
 * @param {number} [options.tangentMaxDeg=30]
 * @param {number} [options.resampleM=25]
 * @returns {{ groups: Array, rejects: Array }}
 *   Each group: { color, member_corridor_ids, trunk_corridor_id, member_route_ids_union }
 */
export function groupCorridorsByColorAndOverlap(
  corridors: SameColorCorridor[],
  options: SameColorOverlapOptions = {},
): SameColorOverlapResult {
  const {
    sharedFractionMin = 0.55,
    sharedLenMinM = 100,
    avgDistMaxM = 15,
    tangentMaxDeg = 30,
    resampleM = 25,
  } = options;

  // Bucket corridors by color (skip corridors without a color).
  const byColor = new Map<string, SameColorCorridor[]>();
  for (const corridor of corridors) {
    const color = corridor.color;
    if (!color) continue;
    if (!byColor.has(color)) byColor.set(color, []);
    byColor.get(color)!.push(corridor);
  }

  const allGroups: SameColorMergeGroup[] = [];
  const allRejects: SameColorReject[] = [];

  for (const [color, colorCorridors] of byColor) {
    if (colorCorridors.length < 2) continue;

    // Map corridors to spine-like input for computePairOverlap (which expects
    // { spine_id, geometry, length_m }). spine_id == corridor_id in our mapping.
    const spines: Spine[] = colorCorridors.map((c) => ({
      spine_id: c.corridor_id,
      geometry: c.geometry,
      length_m: c.length_m ?? haversinePolylineM(c.geometry.coordinates),
      route_ids: c.route_ids ?? [],
    }));

    // Pairwise overlap test + union-find.
    //
    // Important: we do NOT use groupSpinesIntoPhysicalBundles' avgDistM gate
    // here because for a branch-into-trunk pattern the diverging tail inflates
    // avgDist past any reasonable threshold even when sharedFraction is huge.
    // Instead we gate on sharedFractionShorter + sharedLenM + tangentDelta only.
    // tangentDeltaAvgDeg from computePairOverlap is already measured over the
    // in-shared portion, so it correctly rejects perpendicular crossings.
    const n = spines.length;
    const parent = Array.from({ length: n }, (_, i) => i);
    const rank = new Array<number>(n).fill(0);
    const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i])));
    const union = (i: number, j: number): void => {
      const ri = find(i), rj = find(j);
      if (ri === rj) return;
      if (rank[ri] < rank[rj]) parent[ri] = rj;
      else if (rank[ri] > rank[rj]) parent[rj] = ri;
      else { parent[rj] = ri; rank[ri]++; }
    };

    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const overlap = computePairOverlap(spines[i], spines[j], {
          resampleM,
          distMaxM: avgDistMaxM,
        });
        let reason: string | null = null;
        if (overlap.sharedFractionShorter < sharedFractionMin) reason = "shared_fraction_too_low";
        else if (overlap.sharedLenM < sharedLenMinM) reason = "shared_len_too_short";
        else if (overlap.tangentDeltaAvgDeg > tangentMaxDeg) reason = "tangent_delta_too_large";

        if (reason) {
          allRejects.push({
            color,
            corridor_id_a: spines[i].spine_id,
            corridor_id_b: spines[j].spine_id,
            avgDistM: overlap.avgDistM,
            sharedFractionShorter: overlap.sharedFractionShorter,
            sharedLenM: overlap.sharedLenM,
            tangentDeltaAvgDeg: overlap.tangentDeltaAvgDeg,
            reject_reason: reason,
          });
        } else {
          union(i, j);
        }
      }
    }

    // Collect groups by root.
    const groupsByRoot = new Map<number, Spine[]>();
    for (let i = 0; i < n; i++) {
      const r = find(i);
      if (!groupsByRoot.has(r)) groupsByRoot.set(r, []);
      groupsByRoot.get(r)!.push(spines[i]);
    }

    for (const members of groupsByRoot.values()) {
      if (members.length < 2) continue;

      // Find the longest member -> trunk.
      let trunk = members[0];
      for (const m of members) {
        if ((m.length_m ?? 0) > (trunk.length_m ?? 0)) trunk = m;
      }

      const memberCorridorIds = members.map((m) => m.spine_id);

      // Union of all route_ids across members.
      const routeIdSet = new Set<RouteId>();
      for (const m of members) {
        for (const rid of m.route_ids ?? []) routeIdSet.add(rid);
      }
      const memberRouteIdsUnion = [...routeIdSet].sort();

      allGroups.push({
        color,
        member_corridor_ids: memberCorridorIds,
        trunk_corridor_id: trunk.spine_id,
        member_route_ids_union: memberRouteIdsUnion,
      });
    }
  }

  return { groups: allGroups, rejects: allRejects };
}

/**
 * Merge a same-color group: trunk gains union of route_ids; shorter branches
 * are clipped to their non-overlapping divergence portion.
 *
 * @param {object} group  One group from groupCorridorsByColorAndOverlap.
 * @param {Map<string, object>} corridorsById  Map from corridor_id to corridor objects.
 * @param {object} options
 * @param {number} [options.minBranchLenM=30]
 * @param {number} [options.resampleM=25]
 * @param {number} [options.avgDistMaxM=15]
 * @param {Map<string, number>} [options.routeCoverageMap]  Per-route corridor count.
 * @returns {{
 *   trunkUpdates: { corridor_id, route_ids, color_route_ids, merged_from_corridor_ids },
 *   branchUpdates: Array<{ corridor_id, newCoords?, drop?, reason? }>,
 *   skipped?: { reason: string }
 * }}
 */
export function mergeSameColorGroup(
  group: SameColorMergeGroup,
  corridorsById: Map<string, SameColorCorridor>,
  options: SameColorMergeOptions = {},
): SameColorMergeResult {
  const {
    minBranchLenM = 30,
    resampleM = 25,
    avgDistMaxM = 15,
    routeCoverageMap = null,
    connectorMaxM = 35,
    maxTwoPointBranchLenM = 250,
    longStraightBranchTangentMaxDeg = 25,
  } = options;

  const { color, member_corridor_ids, trunk_corridor_id, member_route_ids_union } = group;
  const trunk = corridorsById.get(trunk_corridor_id);
  if (!trunk) {
    return { skipped: { reason: "trunk_not_found" } };
  }

  const branchIds = member_corridor_ids.filter((id) => id !== trunk_corridor_id);

  // Resample trunk for distance checks.
  const trunkResampled = resamplePolyline(trunk.geometry.coordinates, resampleM);
  const branchUpdates: SameColorBranchUpdate[] = [];

  // Connectivity-preservation check: collect routes that would be fully consumed
  // by the merge if we drop/clip their branch features.
  // We check BEFORE processing to decide whether to skip the entire group.
  if (routeCoverageMap) {
    // Routes that are NEW in the branches (not already in trunk's own route_ids).
    const trunkRouteSet = new Set(trunk.route_ids ?? []);
    for (const branchId of branchIds) {
      const branch = corridorsById.get(branchId);
      if (!branch) continue;
      for (const r of branch.route_ids ?? []) {
        if (!trunkRouteSet.has(r)) {
          // This route appears in the branch but not in the trunk.
          // If the branch is the ONLY corridor for this route (coverage == 1),
          // dropping it entirely would break connectivity. We need to check
          // how much of the branch is non-overlapping; if fully overlapping (would drop),
          // the merge would break connectivity.
          const coverage = routeCoverageMap.get(r) ?? 0;
          if (coverage <= 1) {
            // The branch is the sole feature; test whether it would be dropped.
            // Compute non-overlapping portion of the branch.
            const branchResampled = resamplePolyline(branch.geometry.coordinates, resampleM);
            let nonOverlapSampleCount = 0;
            for (const pt of branchResampled) {
              const d = pointToPolylineMinDistM(pt, trunkResampled);
              if (d > avgDistMaxM) nonOverlapSampleCount++;
            }
            const branchLen = branch.length_m ?? haversinePolylineM(branch.geometry.coordinates);
            const nonOverlapLen = (nonOverlapSampleCount / Math.max(1, branchResampled.length)) * branchLen;
            if (nonOverlapLen < minBranchLenM) {
              // Would be dropped; but it's the sole coverage for this route.
              return { skipped: { reason: "would_break_route_connectivity" } };
            }
          }
        }
      }
    }
  }

  // Process each branch.
  for (const branchId of branchIds) {
    const branch = corridorsById.get(branchId);
    if (!branch) {
      branchUpdates.push({ corridor_id: branchId, drop: true, reason: "branch_not_found" });
      continue;
    }

    const branchCoords = branch.geometry.coordinates;
    const branchResampled = resamplePolyline(branchCoords, resampleM);

    if (branchResampled.length < 2) {
      branchUpdates.push({ corridor_id: branchId, drop: true, reason: "degenerate_branch" });
      continue;
    }

    // For each sample on the branch, determine if it's "in-overlap" (within avgDistMaxM of trunk)
    // or "out-of-overlap".
    const inOverlap = branchResampled.map((pt) => {
      const d = pointToPolylineMinDistM(pt, trunkResampled);
      return d <= avgDistMaxM;
    });

    // Partition into runs of out-of-overlap samples.
    // Find the longest contiguous run of out-of-overlap samples.
    let longestRun = { start: -1, len: 0 };
    let currentRun = { start: -1, len: 0 };
    for (let i = 0; i < inOverlap.length; i++) {
      if (!inOverlap[i]) {
        if (currentRun.start === -1) {
          currentRun = { start: i, len: 1 };
        } else {
          currentRun.len++;
        }
        if (currentRun.len > longestRun.len) {
          longestRun = { ...currentRun };
        }
      } else {
        currentRun = { start: -1, len: 0 };
      }
    }

    if (longestRun.start === -1 || longestRun.len < 2) {
      // Entire branch is in-overlap (or degenerate): drop it.
      branchUpdates.push({ corridor_id: branchId, drop: true, reason: "fully_contained" });
      continue;
    }

    // Endpoint-anchored check: the surviving non-overlap slice must include
    // either the branch's first sample or its last sample. A middle slice
    // (both endpoints surrounded by in-overlap samples) renders as a
    // free-floating tick with no connection to anything visible -- drop it.
    // The trunk already carries this geometry via the union of route_ids, so
    // dropping it loses no information.
    {
      const _runStart = longestRun.start;
      const _runEnd = longestRun.start + longestRun.len - 1;
      const anchoredAtStart = _runStart === 0;
      const anchoredAtEnd = _runEnd === inOverlap.length - 1;
      if (!anchoredAtStart && !anchoredAtEnd) {
        branchUpdates.push({ corridor_id: branchId, drop: true, reason: "middle_floater_dropped" });
        continue;
      }
    }

    // The non-overlapping run covers samples [longestRun.start .. longestRun.start + longestRun.len - 1].
    // Map those back to the original branch coordinates.
    // We use the sampled indices to determine the arc-length range, then extract
    // original vertices within that range.

    // Compute cumulative arc length on branch coords.
    const origArcLen = [0];
    for (let i = 1; i < branchCoords.length; i++) {
      origArcLen.push(origArcLen[i - 1] + haversineM(branchCoords[i - 1], branchCoords[i]));
    }
    const totalBranchLen = origArcLen[origArcLen.length - 1];

    // Compute cumulative arc length on resampled branch.
    const sampledArcLen = [0];
    for (let i = 1; i < branchResampled.length; i++) {
      sampledArcLen.push(sampledArcLen[i - 1] + haversineM(branchResampled[i - 1], branchResampled[i]));
    }
    const sampledTotal = sampledArcLen[sampledArcLen.length - 1];

    const runStart = longestRun.start;
    const runEnd = longestRun.start + longestRun.len - 1;

    // Arc-length fractions at the run boundaries.
    const startFrac = sampledTotal > 0 ? sampledArcLen[runStart] / sampledTotal : 0;
    const endFrac = sampledTotal > 0 ? sampledArcLen[runEnd] / sampledTotal : 1;

    const startArc = startFrac * totalBranchLen;
    const endArc = endFrac * totalBranchLen;

    // Extract original vertices between startArc and endArc.
    // Helper: interpolate point at arc length target on original coords.
    function interpolateAtArc(arcTarget: number): Position {
      if (arcTarget <= origArcLen[0]) return branchCoords[0];
      if (arcTarget >= origArcLen[origArcLen.length - 1]) return branchCoords[branchCoords.length - 1];
      for (let i = 1; i < branchCoords.length; i++) {
        if (origArcLen[i] >= arcTarget) {
          const segLen = origArcLen[i] - origArcLen[i - 1];
          if (segLen === 0) return branchCoords[i];
          const t = (arcTarget - origArcLen[i - 1]) / segLen;
          const a = branchCoords[i - 1];
          const b = branchCoords[i];
          return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
        }
      }
      return branchCoords[branchCoords.length - 1];
    }

    const clippedCoords: Position[] = [];
    const startPt = interpolateAtArc(startArc);
    clippedCoords.push(startPt);

    for (let i = 0; i < branchCoords.length; i++) {
      const arc = origArcLen[i];
      if (arc > startArc && arc < endArc) {
        clippedCoords.push(branchCoords[i]);
      }
    }

    const endPt = interpolateAtArc(endArc);
    const lastPt = clippedCoords[clippedCoords.length - 1];
    if (haversineM(endPt, lastPt) > 0.01) {
      clippedCoords.push(endPt);
    }

    if (clippedCoords.length < 2) {
      branchUpdates.push({ corridor_id: branchId, drop: true, reason: "fully_contained" });
      continue;
    }

    // Check remaining length.
    const remainingLen = haversinePolylineM(clippedCoords);
    if (remainingLen < minBranchLenM) {
      branchUpdates.push({ corridor_id: branchId, drop: true, reason: "fully_contained" });
      continue;
    }
    if (
      clippedCoords.length <= 2 &&
      remainingLen > maxTwoPointBranchLenM &&
      !alignedLongStraightBranchIsSafe(clippedCoords, trunk.geometry.coordinates, {
        connectorMaxM,
        tangentMaxDeg: longStraightBranchTangentMaxDeg,
      })
    ) {
      branchUpdates.push({
        corridor_id: branchId,
        drop: true,
        reason: "low_detail_long_chord_dropped",
      });
      continue;
    }

    branchUpdates.push({
      corridor_id: branchId,
      newCoords: clippedCoords,
      connector: connectorForClippedBranch({
        clippedCoords,
        trunkCoords: trunk.geometry.coordinates,
        branch,
        color,
        connectorMaxM,
      }),
    });
  }

  // Build trunk updates.
  const trunkRouteIds = [...new Set<RouteId>(member_route_ids_union ?? trunk.route_ids ?? [])].sort();

  // color_route_ids: filter union to just routes that match this color.
  // We do a simple lookup: include route if its color matches group.color.
  // Since we don't have ROUTE_COLORS here, we instead include all route_ids
  // that were present in any member (i.e., the full union). The caller in the
  // build script that has routeColorFor can recompute this if needed; we store
  // the full union as color_route_ids for now.
  // Actually: color_route_ids should be { [color]: [routeIds] } per the spec,
  // but looking at how the build script uses it (tp.color_route_ids = routesForColor(...)),
  // it's just an array. We'll return an object { [color]: unionRouteIds } since
  // the spec says "{ [color]: union }".
  const colorRouteIds: Record<string, RouteId[]> = { [color]: trunkRouteIds };

  const trunkUpdates = {
    corridor_id: trunk_corridor_id,
    route_ids: trunkRouteIds,
    color_route_ids: colorRouteIds,
    merged_from_corridor_ids: [...member_corridor_ids],
  };

  return { trunkUpdates, branchUpdates };
}
