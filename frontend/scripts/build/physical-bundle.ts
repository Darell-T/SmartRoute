// frontend/scripts/build/physical-bundle.ts
// Cross-corridor physical bundle grouping helpers for Phase 1.5.
// Pure helpers -- no fs, no globals.

import { computeBaseSpineHash } from "./spine.ts";
import type { BBox, LineStringGeometry, Position } from "./types.ts";

const EARTH_RADIUS_M = 6371000;

// A spine is a named polyline with an optional precomputed length and route set.
// computePairOverlap/resamplePolyline/pointToPolylineMinDistM only read
// spine_id/geometry/length_m; the grouping helpers also read route_ids.
export type Spine = {
  spine_id: string;
  geometry: LineStringGeometry;
  length_m?: number | null;
  route_ids?: string[];
};

export type PairOverlapOptions = {
  resampleM?: number;
  distMaxM?: number;
};

export type PairOverlapResult = {
  avgDistM: number;
  sharedFractionShorter: number;
  sharedLenM: number;
  tangentDeltaAvgDeg: number;
  shorterSpineId: string;
  longerSpineId: string;
};

type ArcSample = { coordinate: Position; arc: number };

type OverlapRun = {
  startArc: number;
  endArc: number;
  sharedLenM: number;
  sampleCount: number;
};

export type GroupSpinesOptions = {
  avgDistMaxM?: number;
  sharedFractionMin?: number;
  sharedLenMinM?: number;
  tangentMaxDeg?: number;
  resampleM?: number;
};

type AcceptedPair = { i: number; j: number; overlap: PairOverlapResult };

type IntervalEntry = {
  memberIndex: number;
  startArc: number;
  endArc: number;
  sharedLenM: number;
  sharedFractionShorter: number;
  pair: AcceptedPair;
};

type Cluster = { startArc: number; endArc: number; intervals: IntervalEntry[] };

type GroupEntry = {
  memberIndices: number[];
  startArc: number;
  endArc: number;
  intervals: IntervalEntry[];
};

export type PhysicalBundleGroup = {
  physical_bundle_id: string;
  spine_ids: string[];
  member_count: number;
  confidence: number;
  base_spine_id: string;
  base_corridor_id: string;
  active_member_corridor_ids: string[];
  shared_extent_start_m: number;
  shared_extent_end_m: number;
  route_ids: string[];
  reason: string;
};

export type PhysicalBundleReject = {
  spine_id_a: string;
  spine_id_b: string;
  avgDistM: number;
  sharedFractionShorter: number;
  sharedLenM: number;
  tangentDeltaAvgDeg: number;
  reject_reason: string;
};

export type TransitiveDiagnostic = {
  reason: string;
  base_spine_id: string;
  member_spine_ids: string[];
  overlap_intervals: Array<{
    start_m: number;
    end_m: number;
    member_spine_ids: string[];
  }>;
};

export type GroupSpinesResult = {
  groups: PhysicalBundleGroup[];
  rejects: PhysicalBundleReject[];
  transitiveDiagnostics: TransitiveDiagnostic[];
};

// Group passed to selectPhysicalBundleSpine -- only these fields are read.
export type SelectableBundleGroup = {
  physical_bundle_id: string;
  spine_ids: string[];
  base_spine_id?: string;
  route_ids?: string[];
};

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

/**
 * Compute bearing in degrees [0, 360) between two [lon, lat] points.
 */
function bearingDeg([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const toDeg = (r: number) => (r * 180) / Math.PI;
  const dLon = toRad(lon2 - lon1);
  const lat1r = toRad(lat1);
  const lat2r = toRad(lat2);
  const y = Math.sin(dLon) * Math.cos(lat2r);
  const x = Math.cos(lat1r) * Math.sin(lat2r) - Math.sin(lat1r) * Math.cos(lat2r) * Math.cos(dLon);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

/**
 * Resample a polyline at approximately stepM meter intervals.
 * Exported for use in tests and external callers.
 */
export function resamplePolyline(coords: Position[], stepM: number): Position[] {
  if (coords.length < 2) return coords.slice();
  const out: Position[] = [coords[0]];
  let carry = 0;
  for (let i = 1; i < coords.length; i++) {
    const a = coords[i - 1];
    const b = coords[i];
    const segLen = haversineM(a, b);
    let consumed = -carry;
    while (consumed + stepM <= segLen) {
      consumed += stepM;
      const t = consumed / segLen;
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    }
    carry = segLen - consumed;
  }
  const last = coords[coords.length - 1];
  const lastOut = out[out.length - 1];
  const dx = lastOut[0] - last[0];
  const dy = lastOut[1] - last[1];
  if (dx * dx + dy * dy > 1e-18) out.push(last);
  return out;
}

/**
 * Returns the minimum distance in meters from `point` to the nearest vertex
 * in `polyline` (acceptable approximation when polyline is densely resampled).
 */
export function pointToPolylineMinDistM(point: Position, polyline: Position[]): number {
  let best = Infinity;
  for (const p of polyline) {
    const d = haversineM(point, p);
    if (d < best) best = d;
  }
  return best;
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

function resamplePolylineWithArc(coords: Position[], stepM: number): ArcSample[] {
  if (!Array.isArray(coords) || coords.length < 2) return [];
  const arcs = cumulativeArcLengths(coords);
  const total = arcs[arcs.length - 1];
  const out: ArcSample[] = [];
  for (let arc = 0; arc < total; arc += stepM) {
    out.push({ coordinate: interpolateAtArc(coords, arcs, arc), arc });
  }
  out.push({ coordinate: coords[coords.length - 1], arc: total });
  return out;
}

function longestOverlapRunOnBase(
  baseSpine: Spine,
  memberSpine: Spine,
  { resampleM, distMaxM }: { resampleM: number; distMaxM: number },
): OverlapRun | null {
  const baseSamples = resamplePolylineWithArc(baseSpine.geometry.coordinates, resampleM);
  const memberSamples = resamplePolyline(memberSpine.geometry.coordinates, resampleM);
  let best: { startIndex: number; endIndex: number } | null = null;
  let current: { startIndex: number; endIndex: number } | null = null;

  for (let index = 0; index < baseSamples.length; index += 1) {
    const sample = baseSamples[index];
    const isShared = pointToPolylineMinDistM(sample.coordinate, memberSamples) <= distMaxM;
    if (isShared) {
      if (!current) current = { startIndex: index, endIndex: index };
      else current.endIndex = index;
      continue;
    }
    if (current && (!best || current.endIndex - current.startIndex > best.endIndex - best.startIndex)) {
      best = current;
    }
    current = null;
  }

  if (current && (!best || current.endIndex - current.startIndex > best.endIndex - best.startIndex)) {
    best = current;
  }
  if (!best) return null;

  const startArc = baseSamples[best.startIndex].arc;
  const endArc = baseSamples[best.endIndex].arc;
  return {
    startArc,
    endArc,
    sharedLenM: Math.max(0, endArc - startArc),
    sampleCount: best.endIndex - best.startIndex + 1,
  };
}

/**
 * Compute the bbox of coords expanded by expandM meters.
 * Returns [minLon, minLat, maxLon, maxLat].
 */
function bboxExpandedDeg(coords: Position[], expandM: number): BBox {
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  for (const [lon, lat] of coords) {
    if (lon < minLon) minLon = lon;
    if (lat < minLat) minLat = lat;
    if (lon > maxLon) maxLon = lon;
    if (lat > maxLat) maxLat = lat;
  }
  const midLat = (minLat + maxLat) / 2;
  const latDeg = expandM / 111320;
  const lonDeg = expandM / Math.max(1, 111320 * Math.cos((midLat * Math.PI) / 180));
  return [minLon - lonDeg, minLat - latDeg, maxLon + lonDeg, maxLat + latDeg];
}

/**
 * Test whether two bboxes overlap.
 */
function bboxOverlap(a: BBox, b: BBox): boolean {
  return !(a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1]);
}

/**
 * Compute overlap metrics between two spines.
 * Each spine is `{ spine_id, geometry: { type, coordinates }, length_m }`.
 *
 * Returns:
 *   { avgDistM, sharedFractionShorter, sharedLenM, tangentDeltaAvgDeg,
 *     shorterSpineId, longerSpineId }
 */
export function computePairOverlap(
  spineA: Spine,
  spineB: Spine,
  { resampleM = 25, distMaxM = 15 }: PairOverlapOptions = {},
): PairOverlapResult {
  const coordsA = spineA.geometry.coordinates;
  const coordsB = spineB.geometry.coordinates;
  const lenA = spineA.length_m ?? haversinePolylineM(coordsA);
  const lenB = spineB.length_m ?? haversinePolylineM(coordsB);

  // Assign shorter/longer based on length_m.
  const [shorter, longer, shorterLen]: [Spine, Spine, number] = lenA <= lenB
    ? [spineA, spineB, lenA]
    : [spineB, spineA, lenB];

  const sampledShorter = resamplePolyline(shorter.geometry.coordinates, resampleM);
  const sampledLonger = resamplePolyline(longer.geometry.coordinates, resampleM);

  // Guard against degenerate input: fewer than 3 samples means we cannot
  // compute meaningful tangents. Treat as a non-matching pair.
  if (sampledShorter.length < 3 || sampledLonger.length < 3) {
    return {
      avgDistM: Infinity,
      sharedFractionShorter: 0,
      sharedLenM: 0,
      tangentDeltaAvgDeg: 180,
      shorterSpineId: shorter.spine_id,
      longerSpineId: longer.spine_id,
    };
  }

  let distSum = 0;
  let inSharedCount = 0;
  let tangentDeltaSum = 0;

  for (let i = 0; i < sampledShorter.length; i++) {
    const pt = sampledShorter[i];
    const dist = pointToPolylineMinDistM(pt, sampledLonger);
    distSum += dist;
    if (dist <= distMaxM) {
      inSharedCount++;
      // Compute tangent on shorter spine at sample i.
      const prevS = sampledShorter[Math.max(0, i - 1)];
      const nextS = sampledShorter[Math.min(sampledShorter.length - 1, i + 1)];
      const bearingS = bearingDeg(prevS, nextS);

      // Find nearest vertex on longer spine and compute tangent there.
      let nearestIdx = 0;
      let nearestDist = Infinity;
      for (let j = 0; j < sampledLonger.length; j++) {
        const d = haversineM(pt, sampledLonger[j]);
        if (d < nearestDist) { nearestDist = d; nearestIdx = j; }
      }
      const prevL = sampledLonger[Math.max(0, nearestIdx - 1)];
      const nextL = sampledLonger[Math.min(sampledLonger.length - 1, nearestIdx + 1)];
      const bearingL = bearingDeg(prevL, nextL);

      // Absolute angular difference mod 180 (reverse direction counts as same).
      let delta = Math.abs(bearingS - bearingL);
      if (delta > 180) delta = 360 - delta;
      if (delta > 90) delta = 180 - delta; // mod 180
      tangentDeltaSum += delta;
    }
  }

  const totalSamples = sampledShorter.length;
  if (totalSamples === 0) {
    return {
      avgDistM: Infinity,
      sharedFractionShorter: 0,
      sharedLenM: 0,
      tangentDeltaAvgDeg: 180,
      shorterSpineId: shorter.spine_id,
      longerSpineId: longer.spine_id,
    };
  }

  const avgDistM = distSum / totalSamples;
  const sharedFractionShorter = inSharedCount / totalSamples;
  const sharedLenM = sharedFractionShorter * shorterLen;
  const tangentDeltaAvgDeg = inSharedCount > 0 ? tangentDeltaSum / inSharedCount : 180;

  return {
    avgDistM,
    sharedFractionShorter,
    sharedLenM,
    tangentDeltaAvgDeg,
    shorterSpineId: shorter.spine_id,
    longerSpineId: longer.spine_id,
  };
}

/**
 * Compute total arc length of a polyline in meters.
 */
function haversinePolylineM(coords: Position[]): number {
  let total = 0;
  for (let i = 1; i < coords.length; i++) {
    total += haversineM(coords[i - 1], coords[i]);
  }
  return total;
}

/**
 * Union-find helpers.
 */
function makeUnionFind(n: number) {
  const parent = Array.from({ length: n }, (_, i) => i);
  const rank = new Array<number>(n).fill(0);
  function find(i: number): number {
    if (parent[i] !== i) parent[i] = find(parent[i]);
    return parent[i];
  }
  function union(i: number, j: number): void {
    const ri = find(i), rj = find(j);
    if (ri === rj) return;
    if (rank[ri] < rank[rj]) { parent[ri] = rj; }
    else if (rank[ri] > rank[rj]) { parent[rj] = ri; }
    else { parent[rj] = ri; rank[ri]++; }
  }
  return { find, union };
}

/**
 * Group spines into physical bundles based on overlap metrics.
 *
 * @param spines
 * @param options
 * @returns {{ groups, rejects, transitiveDiagnostics }}
 */
export function groupSpinesIntoPhysicalBundles(
  spines: Spine[],
  options: GroupSpinesOptions = {},
): GroupSpinesResult {
  const {
    avgDistMaxM = 15,
    sharedFractionMin = 0.6,
    sharedLenMinM = 250,
    tangentMaxDeg = 30,
    resampleM = 25,
  } = options;

  const n = spines.length;

  // Precompute bboxes expanded by avgDistMaxM for prefiltering.
  const bboxes = spines.map((s) => bboxExpandedDeg(s.geometry.coordinates, avgDistMaxM + resampleM));

  const allRejects: PhysicalBundleReject[] = [];
  const acceptedPairs: AcceptedPair[] = [];

  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (!bboxOverlap(bboxes[i], bboxes[j])) continue;

      const overlap = computePairOverlap(spines[i], spines[j], { resampleM, distMaxM: avgDistMaxM });

      // Apply four gates.
      let rejectReason: string | null = null;
      if (overlap.avgDistM > avgDistMaxM) rejectReason = "avg_dist_too_large";
      else if (overlap.sharedFractionShorter < sharedFractionMin) rejectReason = "shared_fraction_too_low";
      else if (overlap.sharedLenM < sharedLenMinM) rejectReason = "shared_len_too_short";
      else if (overlap.tangentDeltaAvgDeg > tangentMaxDeg) rejectReason = "tangent_delta_too_large";

      if (rejectReason) {
        allRejects.push({
          spine_id_a: spines[i].spine_id,
          spine_id_b: spines[j].spine_id,
          avgDistM: overlap.avgDistM,
          sharedFractionShorter: overlap.sharedFractionShorter,
          sharedLenM: overlap.sharedLenM,
          tangentDeltaAvgDeg: overlap.tangentDeltaAvgDeg,
          reject_reason: rejectReason,
        });
      } else {
        acceptedPairs.push({ i, j, overlap });
      }
    }
  }

  const intervalsByBaseIndex = new Map<number, IntervalEntry[]>();
  for (const pair of acceptedPairs) {
    const left = spines[pair.i];
    const right = spines[pair.j];
    const leftLength = left.length_m ?? haversinePolylineM(left.geometry.coordinates);
    const rightLength = right.length_m ?? haversinePolylineM(right.geometry.coordinates);
    const baseIndex =
      leftLength > rightLength
        ? pair.i
        : rightLength > leftLength
          ? pair.j
          : left.spine_id.localeCompare(right.spine_id) <= 0
            ? pair.i
            : pair.j;
    const memberIndex = baseIndex === pair.i ? pair.j : pair.i;
    const run = longestOverlapRunOnBase(spines[baseIndex], spines[memberIndex], {
      resampleM,
      distMaxM: avgDistMaxM,
    });
    if (!run || run.sharedLenM < sharedLenMinM) {
      allRejects.push({
        spine_id_a: left.spine_id,
        spine_id_b: right.spine_id,
        avgDistM: pair.overlap.avgDistM,
        sharedFractionShorter: pair.overlap.sharedFractionShorter,
        sharedLenM: run?.sharedLenM ?? 0,
        tangentDeltaAvgDeg: pair.overlap.tangentDeltaAvgDeg,
        reject_reason: "common_run_too_short",
      });
      continue;
    }
    if (!intervalsByBaseIndex.has(baseIndex)) intervalsByBaseIndex.set(baseIndex, []);
    intervalsByBaseIndex.get(baseIndex)!.push({
      memberIndex,
      startArc: run.startArc,
      endArc: run.endArc,
      sharedLenM: run.sharedLenM,
      sharedFractionShorter: pair.overlap.sharedFractionShorter,
      pair,
    });
  }

  const groups: PhysicalBundleGroup[] = [];
  const transitiveDiagnostics: TransitiveDiagnostic[] = [];

  for (const [baseIndex, intervals] of intervalsByBaseIndex) {
    intervals.sort((a, b) => a.startArc - b.startArc || a.endArc - b.endArc);
    const clusters: Cluster[] = [];
    for (const interval of intervals) {
      const last = clusters[clusters.length - 1];
      if (last && interval.startArc <= last.endArc + resampleM) {
        last.intervals.push(interval);
        last.endArc = Math.max(last.endArc, interval.endArc);
      } else {
        clusters.push({
          startArc: interval.startArc,
          endArc: interval.endArc,
          intervals: [interval],
        });
      }
    }

    if (clusters.length > 1) {
      transitiveDiagnostics.push({
        reason: "transitive_disjoint_overlap",
        base_spine_id: spines[baseIndex].spine_id,
        member_spine_ids: [...new Set(intervals.map((interval) => spines[interval.memberIndex].spine_id))],
        overlap_intervals: clusters.map((cluster) => ({
          start_m: Number(cluster.startArc.toFixed(2)),
          end_m: Number(cluster.endArc.toFixed(2)),
          member_spine_ids: [...new Set(cluster.intervals.map((interval) => spines[interval.memberIndex].spine_id))],
        })),
      });
    }

    for (const cluster of clusters) {
      const memberIndices = [...new Set(cluster.intervals.map((interval) => interval.memberIndex))];
      const commonStart = Math.max(...cluster.intervals.map((interval) => interval.startArc));
      const commonEnd = Math.min(...cluster.intervals.map((interval) => interval.endArc));
      const commonLen = commonEnd - commonStart;
      const entries: GroupEntry[] =
        memberIndices.length > 1 && commonLen >= sharedLenMinM
          ? [{
              memberIndices,
              startArc: commonStart,
              endArc: commonEnd,
              intervals: cluster.intervals,
            }]
          : cluster.intervals.map((interval) => ({
              memberIndices: [interval.memberIndex],
              startArc: interval.startArc,
              endArc: interval.endArc,
              intervals: [interval],
            }));

      for (const entry of entries) {
        const spineIds = [baseIndex, ...entry.memberIndices]
          .map((index) => spines[index].spine_id);
        const uniqueSpineIds = [...new Set(spineIds)];
        if (uniqueSpineIds.length < 2) continue;
        groups.push({
          physical_bundle_id: "",
          spine_ids: uniqueSpineIds,
          member_count: uniqueSpineIds.length,
          confidence: Math.min(...entry.intervals.map((interval) => interval.sharedFractionShorter)),
          base_spine_id: spines[baseIndex].spine_id,
          base_corridor_id: String(spines[baseIndex].spine_id).startsWith("spine-")
            ? String(spines[baseIndex].spine_id).slice("spine-".length)
            : spines[baseIndex].spine_id,
          active_member_corridor_ids: entry.memberIndices.map((index) =>
            String(spines[index].spine_id).startsWith("spine-")
              ? String(spines[index].spine_id).slice("spine-".length)
              : spines[index].spine_id,
          ),
          shared_extent_start_m: Number(entry.startArc.toFixed(2)),
          shared_extent_end_m: Number(entry.endArc.toFixed(2)),
          route_ids: [...new Set(uniqueSpineIds.flatMap((spineId) =>
            spines.find((spine) => spine.spine_id === spineId)?.route_ids ?? [],
          ))].sort(),
          reason: "common_overlap_run",
        });
      }
    }
  }

  // Sort groups deterministically (by first member spine_id).
  groups.sort((a, b) =>
    (a.base_spine_id ?? a.spine_ids[0]).localeCompare(b.base_spine_id ?? b.spine_ids[0]) ||
    (a.shared_extent_start_m ?? 0) - (b.shared_extent_start_m ?? 0) ||
    a.spine_ids.join("|").localeCompare(b.spine_ids.join("|")),
  );

  // Assign deterministic IDs after sort.
  groups.forEach((g, idx) => {
    g.physical_bundle_id = `pb-${String(idx + 1).padStart(5, "0")}`;
  });

  // Cap rejects to top 200 by sharedFractionShorter descending.
  allRejects.sort((a, b) => b.sharedFractionShorter - a.sharedFractionShorter);
  const rejects = allRejects.slice(0, 200);

  return { groups, rejects, transitiveDiagnostics };
}

/**
 * Select the canonical spine for a physical bundle: the longest member.
 * Returns { physical_bundle_id, base_spine_id, geometry, route_ids, member_spine_ids }.
 */
export function selectPhysicalBundleSpine(group: SelectableBundleGroup, spinesById: Map<string, Spine>) {
  let best: Spine | null | undefined = group.base_spine_id ? spinesById.get(group.base_spine_id) : null;
  if (!best) {
    let bestLen = -1;
    for (const spineId of group.spine_ids) {
      const s = spinesById.get(spineId);
      if (!s) continue;
      const len = s.length_m ?? 0;
      if (len > bestLen) { bestLen = len; best = s; }
    }
  }
  if (!best) {
    // Fallback: use first spine.
    best = spinesById.get(group.spine_ids[0]);
  }

  // Union of all member route_ids, sorted and deduped.
  const allRouteIds = new Set<string>(group.route_ids ?? []);
  if (allRouteIds.size === 0) {
    for (const spineId of group.spine_ids) {
      const s = spinesById.get(spineId);
      if (s?.route_ids) for (const r of s.route_ids) allRouteIds.add(r);
    }
  }
  const route_ids = [...allRouteIds].sort();

  return {
    physical_bundle_id: group.physical_bundle_id,
    base_spine_id: best!.spine_id,
    geometry: best!.geometry,
    route_ids,
    member_spine_ids: group.spine_ids,
  };
}

/**
 * Re-export computeBaseSpineHash under the Phase 1.5 name.
 * Reuses the same djb2-based hashing from spine.ts.
 */
export function computePhysicalBundleSpineHash(coords: Position[]): string {
  return computeBaseSpineHash(coords);
}

/**
 * Clip a polyline to the extent defined by two query points.
 *
 * Given `spineCoords` (an array of [lon, lat]) and `fromCoord` / `toCoord`,
 * finds the nearest vertex on a resampled spine for each query point, then
 * returns the slice of the ORIGINAL spine vertices between those projected
 * positions. The endpoints of the returned polyline are exactly the projected
 * points (nearest resampled vertices), with all original vertices in between
 * preserved.
 */
export function clipPolylineToExtent(
  spineCoords: Position[],
  fromCoord: Position,
  toCoord: Position,
  { resampleM = 25 }: { resampleM?: number } = {},
): Position[] | null {
  if (!Array.isArray(spineCoords) || spineCoords.length < 2) return null;
  if (!Array.isArray(fromCoord) || !Array.isArray(toCoord)) return null;

  // Resample the spine for nearest-vertex search.
  const sampled = resamplePolyline(spineCoords, resampleM);

  // Find nearest resampled vertex to fromCoord and toCoord.
  let fromIdx = 0, toIdx = 0;
  let fromDist = Infinity, toDist = Infinity;
  for (let i = 0; i < sampled.length; i++) {
    const df = haversineM(fromCoord, sampled[i]);
    const dt = haversineM(toCoord, sampled[i]);
    if (df < fromDist) { fromDist = df; fromIdx = i; }
    if (dt < toDist) { toDist = dt; toIdx = i; }
  }

  // Detect whether the corridor runs opposite to the bundle spine direction.
  // If so, we build the slice in spine order (lo->hi) and reverse at the end,
  // so that result[0] corresponds to the corridor's fromCoord and result[-1]
  // corresponds to the corridor's toCoord.
  const reversed = fromIdx > toIdx;
  let lo: number, hi: number, loArcFrac: number, hiArcFrac: number;

  // Map the sampled indices back to cumulative arc length positions on the
  // original spine, then extract the matching original vertices.
  // Strategy: use cumulative arc length to find which original vertices fall
  // between the arc-length positions of sampled[lo] and sampled[hi].

  // Compute cumulative arc lengths on the original spine.
  const origArcLen = [0];
  for (let i = 1; i < spineCoords.length; i++) {
    origArcLen.push(origArcLen[i - 1] + haversineM(spineCoords[i - 1], spineCoords[i]));
  }
  const totalLen = origArcLen[origArcLen.length - 1];
  if (totalLen === 0) return [spineCoords[0], spineCoords[spineCoords.length - 1]];

  // Cumulative arc lengths on the resampled spine.
  const sampledArcLen = [0];
  for (let i = 1; i < sampled.length; i++) {
    sampledArcLen.push(sampledArcLen[i - 1] + haversineM(sampled[i - 1], sampled[i]));
  }
  const sampledTotal = sampledArcLen[sampledArcLen.length - 1];

  if (reversed) {
    // Corridor runs opposite to spine: fromIdx > toIdx in spine order.
    // Build the slice lo->hi (spine direction) using the swapped indices so
    // the intermediate-vertex scan goes in the right direction. Reverse at end.
    lo = toIdx;
    hi = fromIdx;
    // lo corresponds to the corridor's toCoord projection; hi to fromCoord.
    loArcFrac = sampledTotal > 0 ? sampledArcLen[lo] / sampledTotal : 0;
    hiArcFrac = sampledTotal > 0 ? sampledArcLen[hi] / sampledTotal : 1;
  } else {
    lo = fromIdx;
    hi = toIdx;
    loArcFrac = sampledTotal > 0 ? sampledArcLen[lo] / sampledTotal : 0;
    hiArcFrac = sampledTotal > 0 ? sampledArcLen[hi] / sampledTotal : 1;
  }

  const loArcTarget = loArcFrac * totalLen;
  const hiArcTarget = hiArcFrac * totalLen;

  // Find which original vertices fall within [loArcTarget, hiArcTarget].
  const result: Position[] = [];

  // Projected lo point.
  const loPt = interpolatePolylineAtArc(spineCoords, origArcLen, loArcTarget);
  result.push(loPt);

  // Intermediate original vertices strictly between the two arc positions.
  for (let i = 0; i < spineCoords.length; i++) {
    const arcPos = origArcLen[i];
    if (arcPos > loArcTarget && arcPos < hiArcTarget) {
      result.push(spineCoords[i]);
    }
  }

  // Projected hi point.
  const hiPt = interpolatePolylineAtArc(spineCoords, origArcLen, hiArcTarget);
  // Avoid duplicate if hiPt equals last result.
  const lastR = result[result.length - 1];
  if (haversineM(hiPt, lastR) > 0.01) result.push(hiPt);

  if (result.length < 2) {
    // Degenerate: return just lo and hi points (corridor direction).
    return reversed ? [hiPt, loPt] : [loPt, hiPt];
  }

  // If the corridor ran opposite to the spine, reverse so that result[0] is
  // closest to the corridor's fromCoord and result[-1] to its toCoord.
  if (reversed) result.reverse();
  return result;
}

/**
 * Interpolate a point at arc length `arcTarget` along `coords`.
 */
function interpolatePolylineAtArc(coords: Position[], arcLens: number[], arcTarget: number): Position {
  if (arcTarget <= arcLens[0]) return coords[0];
  if (arcTarget >= arcLens[arcLens.length - 1]) return coords[coords.length - 1];
  for (let i = 1; i < coords.length; i++) {
    if (arcLens[i] >= arcTarget) {
      const segLen = arcLens[i] - arcLens[i - 1];
      if (segLen === 0) return coords[i];
      const t = (arcTarget - arcLens[i - 1]) / segLen;
      const a = coords[i - 1];
      const b = coords[i];
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
  }
  return coords[coords.length - 1];
}
