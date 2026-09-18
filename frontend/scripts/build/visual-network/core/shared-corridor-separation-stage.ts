// Shared-corridor separation enforcement -- runs AFTER lane offsets are baked
// (physical-bundle-materialization's continuous lane bake, and cross-color
// spread v1/v2 in lane-offset-finalization-stage.ts). Those earlier passes
// each offset a polyline along ITS OWN vertexNormal; two independently
// digitized OpenData source traces can therefore end up nowhere near the
// intended separation even though each was individually offset correctly --
// symmetric +/-N m offsets along independent normals do not guarantee N*2 m
// of actual separation. Measured example: the B/Q Brighton corridor near
// Newkirk Plaza collapses to <1m separation in spots while a few blocks north
// it holds 10-15m, despite both lines carrying a baked offset.
//
// This pass re-measures ACTUAL pointwise separation (not the offset that was
// intended) over every shared-corridor pair and, only where it falls below
// FLOOR_SEPARATION_M, replaces that LOCAL pocket (+ a buffer, tapered) with a
// shared Hermite-fit centerline and re-offsets both members symmetrically
// from it -- the same technique already proven for the Church Ave B/Q hotspot
// in brighton-bq-church-spacing.ts, generalized to the whole network.
//
// Deliberately LOCAL: some shared corridors run for kilometers (the full B/Q
// Brighton trunk is >13km), and fitHermiteCenterline only tracks the real
// curve well over a short span -- Brighton's own use is a ~1.3km slice. A
// single global fit across a multi-km extent produces a centerline that
// doesn't track either line's actual path and can make separation WORSE, not
// better. So each deficient stretch is windowed (capped at MAX_WINDOW_M) and
// fit independently; a long deficient run is processed as several adjacent
// windows rather than one giant one. Every fit is verified against its own
// pointwise result before being written -- if it doesn't actually reach the
// floor (or made things worse), it's discarded rather than applied blind.
import { writeFileSync } from "node:fs";
import {
  buildBalancedPair,
  cumulativeArcs,
  haversineM,
  lengthM,
  minSeparationM,
  orientationNeedsReverse,
  pointLineDistanceM,
  replaceArcRange,
  samplePolyline,
  sliceArc,
} from "../../brighton-bq-church-spacing.ts";
import type { BalancedOptions } from "../../brighton-bq-church-spacing.ts";
import type { LineFeature, Position } from "../shared/types.ts";
import { routeIdsOf } from "../shared/route-config.ts";

const FLOOR_SEPARATION_M = 13; // matches Church Ave targetSeparationM
const TAPER_M = 40;
const BUFFER_M = 60;
const SAMPLE_M = 8; // fine resample used inside a local fit window
const SCAN_SAMPLE_M = 20; // coarse resample used to locate deficient pockets across a whole shared extent
const MAX_WINDOW_M = 900; // caps a single Hermite-fit window (buffer included) so the fit stays local and well-conditioned
const MIN_WINDOW_M = 200; // floor on window length so the verification core clears the edge taper (see windowsForPocket)
const MERGE_GAP_M = 2 * BUFFER_M; // deficient pockets closer than this merge into one window
const DETECT_DIST_MAX_M = 22; // generous enough to still see a corridor once it's pinched near 0m
const MIN_SHARED_LEN_M = 150;
const JUNCTION_EXCLUSION_M = 80; // keep hands off fanout/branch-transition geometry (joint-offset-taper.ts's territory)
const SMOOTHING_PASSES = 2;
const VERIFY_TOLERANCE_M = 0.5; // a fit must land within this of the floor to be trusted
const ROLLBACK_TOLERANCE_M = 0.25; // a pair's fixes are rolled back if they leave any point this much closer than before

const EXCLUDED_ROLES = new Set(["fanout", "branch_tail"]);

type SeparationCandidate = LineFeature & {
  geometry: { type: "LineString"; coordinates: Position[] };
};

function isCandidate(f: LineFeature): f is SeparationCandidate {
  return (
    f?.geometry?.type === "LineString" &&
    Array.isArray(f.geometry.coordinates) &&
    f.geometry.coordinates.length >= 2 &&
    Boolean(f.properties?.color) &&
    !EXCLUDED_ROLES.has(String(f.properties?.bundle_materialization_role ?? ""))
  );
}

// NYC subway lines commonly run 15-30km end to end and criss-cross the whole
// system, so a per-pair AABB overlap check rejects almost nothing -- most
// differently-colored feature pairs have overlapping bounding boxes even when
// they never run near each other. Naively calling findSharedArcExtent (which
// resamples each FULL feature at ~20m and does an O(n*m) nearest-point scan)
// on every surviving pair is O(candidates^2 * length^2) and does not finish
// in reasonable time on a ~70-feature, multi-hundred-km network.
//
// Instead, build a coarse spatial grid (same technique as buildJunctionBridges
// in bundle-stage.ts) from a LOW-density resample of every candidate, and use
// it to find the small set of features that actually pass near each other.
// Only that short candidate-pair list goes through the expensive exact check.
const GRID_STRIDE_M = 120; // coarse sample spacing for the prefilter grid
const GRID_CELL_M = 150; // grid cell size; must be >= DETECT_DIST_MAX_M + GRID_STRIDE_M slack

function coarseSamples(coords: Position[]): Position[] {
  const len = lengthM(coords);
  const count = Math.max(3, Math.min(300, Math.ceil(len / GRID_STRIDE_M) + 1));
  return samplePolyline(coords, count);
}

// Returns, for each candidate index, the set of OTHER candidate indices whose
// coarse samples land within roughly GRID_CELL_M of one of this feature's
// coarse samples -- a generous superset of "might share a corridor" that's
// cheap to compute and never misses a true positive (MIN_SHARED_LEN_M is
// well above the grid cell size, so a genuine shared stretch always puts at
// least one sample from each feature in the same or an adjacent cell).
function buildProximityIndex(candidates: SeparationCandidate[]): Set<number>[] {
  const cellDeg = GRID_CELL_M / 111320;
  const grid = new Map<string, number[]>();
  const samplesByFeature: Position[][] = candidates.map((f) => coarseSamples(f.geometry.coordinates));

  for (let i = 0; i < samplesByFeature.length; i += 1) {
    for (const [lon, lat] of samplesByFeature[i]) {
      const key = `${Math.floor(lon / cellDeg)}|${Math.floor(lat / cellDeg)}`;
      let bucket = grid.get(key);
      if (!bucket) {
        bucket = [];
        grid.set(key, bucket);
      }
      if (bucket[bucket.length - 1] !== i) bucket.push(i);
    }
  }

  const nearby: Set<number>[] = candidates.map(() => new Set<number>());
  for (let i = 0; i < samplesByFeature.length; i += 1) {
    for (const [lon, lat] of samplesByFeature[i]) {
      const cx = Math.floor(lon / cellDeg);
      const cy = Math.floor(lat / cellDeg);
      for (let dx = -1; dx <= 1; dx += 1) {
        for (let dy = -1; dy <= 1; dy += 1) {
          const bucket = grid.get(`${cx + dx}|${cy + dy}`);
          if (!bucket) continue;
          for (const j of bucket) {
            if (j !== i) nearby[i].add(j);
          }
        }
      }
    }
  }
  return nearby;
}

// Junction locations come from two signals:
// 1. Endpoints of excluded-role (fanout/branch_tail) features -- that's
//    exactly where a materialized branch peels off the shared trunk.
// 2. Endpoint CLUSTERS: two or more different features whose endpoints sit
//    within ENDPOINT_CLUSTER_M of each other meet at a junction (a Y-split or
//    a continuation joint) even when no fanout/branch_tail feature marks it.
//    Measured example: the D and the N/R both originate ~16m apart at Pacific
//    St -- a real Y-split with no role-tagged feature anywhere near it.
// A lone feature endpoint in open track is NOT a junction and must not be
// excluded -- that would blind the checks near every corridor tail.
const ENDPOINT_CLUSTER_M = 25;

function nearAnyJunctionPoint(point: Position, junctionPoints: Position[], maxM: number): boolean {
  for (const jp of junctionPoints) {
    if (haversineM(point, jp) <= maxM) return true;
  }
  return false;
}

// Per-sample NEAREST-NEIGHBOR separation along a. Index-paired sampling
// (comparing aSamples[i] to bSamples[i]) was tried first and is subtly wrong:
// the two features never have exactly equal arc length, so over a multi-km
// extent the index correspondence drifts and a true near-total overlap can
// read as 13-21m "separation" (measured: B/Q near Newkirk Plaza, 1.48m real
// vs ~17m index-paired). Nearest-neighbor is what a viewer sees. The b side
// is resampled at the same density to bound the O(samples * segments) cost.
type PointwiseSeparation = {
  seps: number[];
  count: number;
};

function pointwiseSeparation(aSeg: Position[], bSeg: Position[], sampleM: number): PointwiseSeparation {
  const segLen = Math.max(lengthM(aSeg), lengthM(bSeg));
  const count = Math.max(8, Math.ceil(segLen / sampleM));
  const aSamples = samplePolyline(aSeg, count);
  const bSamples = samplePolyline(bSeg, count);
  const seps = aSamples.map((p) => pointLineDistanceM(p, bSamples));
  return { seps, count };
}

type SharedArcExtent = {
  aStartArc: number;
  aEndArc: number;
  bStartArc: number;
  bEndArc: number;
  sharedLenM: number;
};

function projectMeters(p: Position, refLat: number): [number, number] {
  const mLat = 110574;
  const mLon = 111320 * Math.cos((refLat * Math.PI) / 180);
  return [p[0] * mLon, p[1] * mLat];
}

// Arc position (meters from the start of `coords`) of the point on `coords`
// nearest to `point`. `arcs` must be cumulativeArcs(coords).
function nearestArcOnPolyline(point: Position, coords: Position[], arcs: number[]): number {
  const refLat = point[1];
  const p = projectMeters(point, refLat);
  let bestD = Infinity;
  let bestArc = 0;
  for (let i = 1; i < coords.length; i += 1) {
    const s0 = projectMeters(coords[i - 1], refLat);
    const s1 = projectMeters(coords[i], refLat);
    const vx = s1[0] - s0[0];
    const vy = s1[1] - s0[1];
    const segLen2 = vx * vx + vy * vy;
    const t = segLen2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - s0[0]) * vx + (p[1] - s0[1]) * vy) / segLen2));
    const dx = p[0] - (s0[0] + vx * t);
    const dy = p[1] - (s0[1] + vy * t);
    const d = Math.hypot(dx, dy);
    if (d < bestD) {
      bestD = d;
      bestArc = arcs[i - 1] + (arcs[i] - arcs[i - 1]) * t;
    }
  }
  return bestArc;
}

// ALL contiguous stretches of `coordsA` that run within distMaxM of `coordsB`
// (each at least minSharedLenM long), with the corresponding b arc range found
// by projecting the run's endpoints onto b. cross-color-spread.ts's
// findSharedArcExtent returns only the LONGEST run, which silently ignores a
// second shared corridor between the same two features -- measured example:
// the D coincides with the N/R along two separate downtown-Brooklyn stretches,
// and the unprocessed shorter one kept a 0m overlap through every fix pass.
function findSharedArcExtents(
  coordsA: Position[],
  coordsB: Position[],
  opts: { resampleM: number; distMaxM: number; minSharedLenM: number },
): SharedArcExtent[] {
  if (coordsA.length < 2 || coordsB.length < 2) return [];
  const aCount = Math.max(2, Math.ceil(lengthM(coordsA) / opts.resampleM));
  const bCount = Math.max(2, Math.ceil(lengthM(coordsB) / opts.resampleM));
  const ra = samplePolyline(coordsA, aCount);
  const rb = samplePolyline(coordsB, bCount);
  const raArcs = cumulativeArcs(ra);
  const rbArcs = cumulativeArcs(rb);
  const out: SharedArcExtent[] = [];
  let runStart = -1;
  for (let i = 0; i <= ra.length; i += 1) {
    const near = i < ra.length && pointLineDistanceM(ra[i], rb) <= opts.distMaxM;
    if (near && runStart === -1) runStart = i;
    if (!near && runStart !== -1) {
      const endIdx = i - 1;
      const aStartArc = raArcs[runStart];
      const aEndArc = raArcs[endIdx];
      if (aEndArc - aStartArc >= opts.minSharedLenM) {
        const b1 = nearestArcOnPolyline(ra[runStart], rb, rbArcs);
        const b2 = nearestArcOnPolyline(ra[endIdx], rb, rbArcs);
        const bStartArc = Math.min(b1, b2);
        const bEndArc = Math.max(b1, b2);
        const sharedLenM = Math.min(aEndArc - aStartArc, bEndArc - bStartArc);
        if (sharedLenM >= opts.minSharedLenM) {
          out.push({ aStartArc, aEndArc, bStartArc, bEndArc, sharedLenM });
        }
      }
      runStart = -1;
    }
  }
  return out;
}

// True nearest-neighbor minimum separation between two polylines, resampled
// to keep the O(n*m) point-vs-segment scan tractable on multi-km extents.
// Unlike pointwiseSeparation (index-paired, used for the pocket scan where
// arc correspondence matters), this measures what a viewer actually sees --
// it's the metric for the never-degrade rollback guard.
function minNearestSeparationM(aSeg: Position[], bSeg: Position[], sampleM: number): number {
  if (aSeg.length < 2 || bSeg.length < 2) return Infinity;
  const aCount = Math.max(8, Math.ceil(lengthM(aSeg) / sampleM));
  const bCount = Math.max(8, Math.ceil(lengthM(bSeg) / sampleM));
  return minSeparationM(samplePolyline(aSeg, aCount), samplePolyline(bSeg, bCount));
}


// Contiguous (index) runs where seps[i] < floor, merged if the gap between
// runs (converted to arc meters) is under MERGE_GAP_M.
function deficientArcPockets(seps: number[], count: number, totalLenM: number, floor: number): [number, number][] {
  const runs: [number, number][] = [];
  let start = -1;
  for (let i = 0; i < seps.length; i += 1) {
    if (seps[i] < floor) {
      if (start === -1) start = i;
    } else if (start !== -1) {
      runs.push([start, i - 1]);
      start = -1;
    }
  }
  if (start !== -1) runs.push([start, seps.length - 1]);
  if (runs.length === 0) return [];

  const idxToArc = (i: number) => (count <= 1 ? 0 : (i / (count - 1)) * totalLenM);
  const arcRuns = runs.map(([i0, i1]): [number, number] => [idxToArc(i0), idxToArc(i1)]);

  const merged: [number, number][] = [arcRuns[0]];
  for (let i = 1; i < arcRuns.length; i += 1) {
    const last = merged[merged.length - 1];
    const [s, e] = arcRuns[i];
    if (s - last[1] <= MERGE_GAP_M) {
      last[1] = e;
    } else {
      merged.push([s, e]);
    }
  }
  return merged;
}

// Split a pocket into consecutive windows no longer than MAX_WINDOW_M
// (buffer included), so a very long deficient run is fixed as several
// well-conditioned local fits instead of one over-long, poorly-conditioned one.
//
// Every window is also grown to at least MIN_WINDOW_M: buildBalancedPair's
// taper (blendFromCore=false) ramps the output back to the ORIGINAL points at
// the window edges over TAPER_M, and the core-fraction margin is capped at
// 0.25 -- so for a window shorter than ~(TAPER_M / 0.25) the verification core
// starts inside the taper ramp and coreMinAfterM can never reach the floor.
// Short edge pockets (e.g. a 60m overlap at a corridor head) would otherwise
// fail verification forever and the overlap would survive untouched.
function windowsForPocket(pocketStart: number, pocketEnd: number, totalLenM: number): [number, number][] {
  const usableMax = Math.max(MAX_WINDOW_M - 2 * BUFFER_M, 100);
  const pocketLen = pocketEnd - pocketStart;
  const chunkCount = Math.max(1, Math.ceil(pocketLen / usableMax));
  const chunkLen = pocketLen / chunkCount;
  const windows: [number, number][] = [];
  for (let c = 0; c < chunkCount; c += 1) {
    const cStart = pocketStart + c * chunkLen;
    const cEnd = pocketStart + (c + 1) * chunkLen;
    let wStart = cStart - BUFFER_M;
    let wEnd = cEnd + BUFFER_M;
    if (wEnd - wStart < MIN_WINDOW_M) {
      const grow = (MIN_WINDOW_M - (wEnd - wStart)) / 2;
      wStart -= grow;
      wEnd += grow;
    }
    // Clamp to the extent, preserving length by shifting inward when possible.
    if (wStart < 0) {
      wEnd = Math.min(totalLenM, wEnd - wStart);
      wStart = 0;
    }
    if (wEnd > totalLenM) {
      wStart = Math.max(0, wStart - (wEnd - totalLenM));
      wEnd = totalLenM;
    }
    windows.push([wStart, wEnd]);
  }
  return windows;
}

type FixOutcome = {
  a_id: string;
  b_id: string;
  a_color: string;
  b_color: string;
  shared_len_m: number;
  min_before_m: number;
  min_after_m: number;
  windows_attempted: number;
  windows_applied: number;
  skipped_reasons: string[];
};

type SharedCorridorSeparationStageInput = {
  bundleArtifacts: { visualFeatures: LineFeature[] };
  separationReportJsonPath: string;
};

type HotspotResult = {
  name: string;
  passed: boolean;
  detail: string;
};

type SharedPair = {
  a: SeparationCandidate;
  b: SeparationCandidate;
  ext: SharedArcExtent;
};

type WindowFit = {
  yellow: Position[];
  orange: Position[];
  aSign: number;
  wStart: number;
  wEnd: number;
  coreStart: number;
  coreEnd: number;
};

type PendingReplacement = {
  feature: SeparationCandidate;
  start: number;
  end: number;
  coords: Position[];
};

type ClaimedRanges = Map<LineFeature, [number, number][]>;

function featureLabel(f: LineFeature): string {
  return String(
    f.properties?.physical_bundle_id ??
    f.properties?.bundle_id ??
    f.properties?.corridor_id ??
    "unknown",
  );
}

function collectExcludedRoleEndpoints(features: LineFeature[]): Position[] {
  const points: Position[] = [];
  for (const feature of features) {
    const coords = feature.geometry?.coordinates;
    if (!Array.isArray(coords) || coords.length < 2) continue;
    const role = String(feature.properties?.bundle_materialization_role ?? "");
    if (EXCLUDED_ROLES.has(role)) points.push(coords[0], coords[coords.length - 1]);
  }
  return points;
}

function collectEndpointClusters(features: LineFeature[]): Position[] {
  const endpoints: { point: Position; feature: LineFeature }[] = [];
  for (const feature of features) {
    const coords = feature.geometry?.coordinates;
    if (!Array.isArray(coords) || coords.length < 2) continue;
    if (feature.geometry?.type !== "LineString") continue;
    endpoints.push({ point: coords[0], feature });
    endpoints.push({ point: coords[coords.length - 1], feature });
  }
  const points: Position[] = [];
  for (let i = 0; i < endpoints.length; i += 1) {
    for (let j = i + 1; j < endpoints.length; j += 1) {
      if (endpoints[i].feature === endpoints[j].feature) continue;
      if (haversineM(endpoints[i].point, endpoints[j].point) <= ENDPOINT_CLUSTER_M) {
        points.push(endpoints[i].point, endpoints[j].point);
      }
    }
  }
  return points;
}

function rangeBlocked(claimed: ClaimedRanges, feature: LineFeature, start: number, end: number) {
  const ranges = claimed.get(feature);
  if (!ranges) return false;
  return ranges.some(([rangeStart, rangeEnd]) =>
    !(end + TAPER_M < rangeStart || start - TAPER_M > rangeEnd),
  );
}

function claimRange(claimed: ClaimedRanges, feature: LineFeature, start: number, end: number) {
  const ranges = claimed.get(feature);
  if (ranges) ranges.push([start, end]);
  else claimed.set(feature, [[start, end]]);
}

function classifySharedCorridorPairs(target: LineFeature[]) {
  const candidates = target.filter(isCandidate);
  const junctionPoints = [
    ...collectExcludedRoleEndpoints(target),
    ...collectEndpointClusters(target),
  ];
  const nearbyIndex = buildProximityIndex(candidates);
  const pairs: SharedPair[] = [];
  const seenPairs = new Set<string>();
  for (let i = 0; i < candidates.length; i += 1) {
    for (const j of nearbyIndex[i]) {
      if (j <= i) continue;
      const pairKey = `${i}|${j}`;
      if (seenPairs.has(pairKey)) continue;
      seenPairs.add(pairKey);
      const a = candidates[i];
      const b = candidates[j];
      if (String(a.properties.color).toUpperCase() === String(b.properties.color).toUpperCase()) {
        continue;
      }
      const exts = findSharedArcExtents(a.geometry.coordinates, b.geometry.coordinates, {
        resampleM: 20,
        distMaxM: DETECT_DIST_MAX_M,
        minSharedLenM: MIN_SHARED_LEN_M,
      });
      for (const ext of exts) pairs.push({ a, b, ext });
    }
  }
  pairs.sort((p, q) => q.ext.sharedLenM - p.ext.sharedLenM);
  return { candidates, junctionPoints, pairs };
}

function balancedWindowOptions(windowSegmentLenM: number, forcedASign?: number): BalancedOptions {
  const coreFractionMargin = Math.min(0.25, (2 * TAPER_M) / Math.max(1, windowSegmentLenM));
  const options: BalancedOptions = {
    bbox: { minLon: -180, maxLon: 180, minLat: -90, maxLat: 90 },
    marginM: 0,
    targetSeparationM: FLOOR_SEPARATION_M,
    blendM: TAPER_M,
    sampleM: SAMPLE_M,
    smoothingPasses: SMOOTHING_PASSES,
    blendFromCore: false,
    coreStartFraction: coreFractionMargin,
    coreEndFraction: 1 - coreFractionMargin,
  };
  if (forcedASign !== undefined) options.forcedASign = forcedASign;
  return options;
}

function fitOneWindow(
  aSegFull: Position[],
  bSegFull: Position[],
  ext: SharedArcExtent,
  reversed: boolean,
  bLen: number,
  wStart: number,
  wEnd: number,
  coreStart: number,
  coreEnd: number,
  junctionPoints: Position[],
  claimed: ClaimedRanges,
  a: SeparationCandidate,
  b: SeparationCandidate,
  outcome: FixOutcome,
): WindowFit | null {
  const aWin = sliceArc(aSegFull, wStart, wEnd);
  const bWin = sliceArc(bSegFull, wStart, wEnd);
  if (aWin.length < 2 || bWin.length < 2) {
    outcome.skipped_reasons.push("degenerate_window");
    return null;
  }
  if (
    aWin.some((p) => nearAnyJunctionPoint(p, junctionPoints, JUNCTION_EXCLUSION_M)) ||
    bWin.some((p) => nearAnyJunctionPoint(p, junctionPoints, JUNCTION_EXCLUSION_M))
  ) {
    outcome.skipped_reasons.push("near_junction_or_branch_transition");
    return null;
  }
  const aFullStart = ext.aStartArc + wStart;
  const aFullEnd = ext.aStartArc + wEnd;
  const bFullStart = reversed ? ext.bStartArc + (bLen - wEnd) : ext.bStartArc + wStart;
  const bFullEnd = reversed ? ext.bStartArc + (bLen - wStart) : ext.bStartArc + wEnd;
  if (
    rangeBlocked(claimed, a, aFullStart, aFullEnd) ||
    rangeBlocked(claimed, b, Math.min(bFullStart, bFullEnd), Math.max(bFullStart, bFullEnd))
  ) {
    outcome.skipped_reasons.push("range_already_claimed");
    return null;
  }
  const balanced = buildBalancedPair(aWin, bWin, balancedWindowOptions(Math.max(lengthM(aWin), lengthM(bWin))));
  if (
    !Number.isFinite(balanced.coreMinAfterM) ||
    balanced.coreMinAfterM < FLOOR_SEPARATION_M - VERIFY_TOLERANCE_M
  ) {
    outcome.skipped_reasons.push("fit_verification_failed");
    return null;
  }
  return {
    yellow: balanced.yellow,
    orange: balanced.orange,
    aSign: balanced.aSign,
    wStart,
    wEnd,
    coreStart,
    coreEnd,
  };
}

function contiguousWindowRuns(winResults: Array<WindowFit | null>) {
  const found: { start: number; end: number }[] = [];
  let ri = 0;
  while (ri < winResults.length) {
    if (winResults[ri] === null) {
      ri += 1;
      continue;
    }
    let runEnd = ri;
    while (runEnd < winResults.length && winResults[runEnd] !== null) runEnd += 1;
    found.push({ start: ri, end: runEnd });
    ri = runEnd;
  }
  return found;
}

function refitWindowToSign(
  fitted: WindowFit,
  aSegFull: Position[],
  bSegFull: Position[],
  dominantSign: number,
  outcome: FixOutcome,
): boolean {
  const aWin = sliceArc(aSegFull, fitted.wStart, fitted.wEnd);
  const bWin = sliceArc(bSegFull, fitted.wStart, fitted.wEnd);
  const refit = buildBalancedPair(
    aWin,
    bWin,
    balancedWindowOptions(Math.max(lengthM(aWin), lengthM(bWin)), dominantSign),
  );
  if (
    !Number.isFinite(refit.coreMinAfterM) ||
    refit.coreMinAfterM < FLOOR_SEPARATION_M - VERIFY_TOLERANCE_M
  ) {
    outcome.skipped_reasons.push("sign_refit_verification_failed");
    return false;
  }
  fitted.yellow = refit.yellow;
  fitted.orange = refit.orange;
  fitted.aSign = dominantSign;
  return true;
}

type NormalizedRuns = {
  runs: { start: number; end: number }[];
  windowsFixedDelta: number;
};

function normalizeRunSigns(
  winResults: Array<WindowFit | null>,
  aSegFull: Position[],
  bSegFull: Position[],
  outcome: FixOutcome,
): NormalizedRuns {
  let runs = contiguousWindowRuns(winResults);
  let windowsFixedDelta = 0;
  for (let guard = 0; guard < 3; guard += 1) {
    let refitFailed = false;
    for (const run of runs) {
      let positiveCount = 0;
      let negativeCount = 0;
      for (let j = run.start; j < run.end; j += 1) {
        const fitted = winResults[j];
        if (!fitted) continue;
        if (fitted.aSign > 0) positiveCount += 1;
        else negativeCount += 1;
      }
      const dominantSign = positiveCount >= negativeCount ? 1 : -1;
      for (let j = run.start; j < run.end; j += 1) {
        const fitted = winResults[j];
        if (!fitted || fitted.aSign === dominantSign) continue;
        if (refitWindowToSign(fitted, aSegFull, bSegFull, dominantSign, outcome)) continue;
        winResults[j] = null;
        outcome.windows_applied -= 1;
        windowsFixedDelta -= 1;
        refitFailed = true;
      }
    }
    if (!refitFailed) break;
    runs = contiguousWindowRuns(winResults);
  }
  return { runs, windowsFixedDelta };
}

type ArcKeepRange = {
  keepStart: number;
  keepEnd: number;
};

function keepRangeForRunWindow(
  fitted: WindowFit,
  j: number,
  runStart: number,
  runEnd: number,
): ArcKeepRange {
  return {
    keepStart: j === runStart ? 0 : fitted.coreStart - fitted.wStart,
    keepEnd: j === runEnd - 1 ? lengthM(fitted.yellow) : fitted.coreEnd - fitted.wStart,
  };
}

function queueMergedRunReplacements(
  runs: { start: number; end: number }[],
  winResults: Array<WindowFit | null>,
  pair: SharedPair,
  reversed: boolean,
  bLen: number,
  pendingReplacements: PendingReplacement[],
  claimed: ClaimedRanges,
): boolean {
  const { a, b, ext } = pair;
  let anyApplied = false;
  for (let rIdx = runs.length - 1; rIdx >= 0; rIdx -= 1) {
    const { start: runStart, end: runEnd } = runs[rIdx];
    const mergedYellow: Position[] = [];
    const mergedOrange: Position[] = [];
    let lastASign = 0;
    for (let j = runStart; j < runEnd; j += 1) {
      const fitted = winResults[j];
      if (!fitted) continue;
      const { keepStart, keepEnd } = keepRangeForRunWindow(fitted, j, runStart, runEnd);
      if (keepEnd > keepStart) {
        mergedYellow.push(...sliceArc(fitted.yellow, keepStart, keepEnd));
        mergedOrange.push(...sliceArc(fitted.orange, keepStart, keepEnd));
      }
      lastASign = fitted.aSign;
    }
    if (mergedYellow.length < 2 || mergedOrange.length < 2) continue;
    const firstR = winResults[runStart];
    const lastR = winResults[runEnd - 1];
    if (!firstR || !lastR) continue;
    const aFullStart = ext.aStartArc + firstR.wStart;
    const aFullEnd = ext.aStartArc + lastR.wEnd;
    pendingReplacements.push({ feature: a, start: aFullStart, end: aFullEnd, coords: mergedYellow });
    const bReplStart = reversed ? bLen - lastR.wEnd : firstR.wStart;
    const bReplEnd = reversed ? bLen - firstR.wStart : lastR.wEnd;
    const bFullStart = ext.bStartArc + Math.min(bReplStart, bReplEnd);
    const bFullEnd = ext.bStartArc + Math.max(bReplStart, bReplEnd);
    pendingReplacements.push({
      feature: b,
      start: bFullStart,
      end: bFullEnd,
      coords: reversed ? mergedOrange.slice().reverse() : mergedOrange,
    });
    a.properties.shared_corridor_separation_enforced = true;
    b.properties.shared_corridor_separation_enforced = true;
    a.properties.lane_offset_baked = true;
    b.properties.lane_offset_baked = true;
    a.properties.lane_slot_semantic = lastASign * 0.5;
    b.properties.lane_slot_semantic = -lastASign * 0.5;
    claimRange(claimed, a, aFullStart, aFullEnd);
    claimRange(claimed, b, bFullStart, bFullEnd);
    anyApplied = true;
  }
  return anyApplied;
}

function applyQueuedReplacements(pendingReplacements: PendingReplacement[]) {
  if (pendingReplacements.length === 0) return;
  const byFeature = new Map<SeparationCandidate, PendingReplacement[]>();
  for (const repl of pendingReplacements) {
    const list = byFeature.get(repl.feature);
    if (list) list.push(repl);
    else byFeature.set(repl.feature, [repl]);
  }
  byFeature.forEach((repls, feature) => {
    repls.sort((x, y) => y.start - x.start);
    let coords = feature.geometry.coordinates;
    for (const repl of repls) {
      coords = replaceArcRange(coords, repl.start, repl.end, repl.coords);
    }
    feature.geometry = { type: "LineString", coordinates: coords };
  });
}

function rollbackPairGeometry(
  a: SeparationCandidate,
  b: SeparationCandidate,
  aCoordsBefore: Position[],
  bCoordsBefore: Position[],
  aPropsBefore: LineFeature["properties"],
  bPropsBefore: LineFeature["properties"],
  claimed: ClaimedRanges,
  aClaimCountBefore: number,
  bClaimCountBefore: number,
) {
  a.geometry = { type: "LineString", coordinates: aCoordsBefore };
  b.geometry = { type: "LineString", coordinates: bCoordsBefore };
  a.properties = aPropsBefore;
  b.properties = bPropsBefore;
  const aClaims = claimed.get(a);
  const bClaims = claimed.get(b);
  if (aClaims) aClaims.length = aClaimCountBefore;
  if (bClaims) bClaims.length = bClaimCountBefore;
}

type PocketProjection = {
  anyApplied: boolean;
  windowsFixedDelta: number;
};

function projectPairPockets(
  pair: SharedPair,
  aSegFull: Position[],
  bSegFull: Position[],
  reversed: boolean,
  bLen: number,
  aLen: number,
  pockets: [number, number][],
  junctionPoints: Position[],
  claimed: ClaimedRanges,
  outcome: FixOutcome,
): PocketProjection {
  const pendingReplacements: PendingReplacement[] = [];
  let anyApplied = false;
  let windowsFixedDelta = 0;
  for (const [pStart, pEnd] of pockets) {
    const pocketWindows = windowsForPocket(pStart, pEnd, aLen);
    const pocketLen = pEnd - pStart;
    const chunkCount = pocketWindows.length;
    const chunkLen = pocketLen / chunkCount;
    const winResults: Array<WindowFit | null> = [];
    for (let wi = 0; wi < chunkCount; wi += 1) {
      const [wStart, wEnd] = pocketWindows[wi];
      outcome.windows_attempted += 1;
      const fitted = fitOneWindow(
        aSegFull,
        bSegFull,
        pair.ext,
        reversed,
        bLen,
        wStart,
        wEnd,
        pStart + wi * chunkLen,
        pStart + (wi + 1) * chunkLen,
        junctionPoints,
        claimed,
        pair.a,
        pair.b,
        outcome,
      );
      winResults.push(fitted);
      if (fitted) {
        outcome.windows_applied += 1;
        windowsFixedDelta += 1;
      }
    }
    const normalized = normalizeRunSigns(winResults, aSegFull, bSegFull, outcome);
    windowsFixedDelta += normalized.windowsFixedDelta;
    if (
      queueMergedRunReplacements(
        normalized.runs,
        winResults,
        pair,
        reversed,
        bLen,
        pendingReplacements,
        claimed,
      )
    ) {
      anyApplied = true;
    }
  }
  applyQueuedReplacements(pendingReplacements);
  return { anyApplied, windowsFixedDelta };
}

type RollbackDecision = {
  kept: boolean;
  transitions: Position[];
};

function remeasureOrRollback(
  pair: SharedPair,
  aSegFull: Position[],
  bSegFull: Position[],
  bSegFullRaw: Position[],
  aCoordsBefore: Position[],
  bCoordsBefore: Position[],
  aPropsBefore: LineFeature["properties"],
  bPropsBefore: LineFeature["properties"],
  aTotalLenBefore: number,
  bTotalLenBefore: number,
  claimed: ClaimedRanges,
  aClaimCountBefore: number,
  bClaimCountBefore: number,
  outcome: FixOutcome,
): RollbackDecision {
  const { a, b, ext } = pair;
  const aDelta = lengthM(a.geometry.coordinates) - aTotalLenBefore;
  const bDelta = lengthM(b.geometry.coordinates) - bTotalLenBefore;
  const aSegFullAfter = sliceArc(a.geometry.coordinates, ext.aStartArc, ext.aEndArc + aDelta);
  const bSegFullAfterRaw = sliceArc(b.geometry.coordinates, ext.bStartArc, ext.bEndArc + bDelta);
  const bSegFullAfter = orientationNeedsReverse(aSegFullAfter, bSegFullAfterRaw)
    ? bSegFullAfterRaw.slice().reverse()
    : bSegFullAfterRaw;
  const beforeNN = minNearestSeparationM(aSegFull, bSegFull, SCAN_SAMPLE_M);
  const afterNN = minNearestSeparationM(aSegFullAfter, bSegFullAfter, SCAN_SAMPLE_M);
  if (afterNN < beforeNN - ROLLBACK_TOLERANCE_M) {
    rollbackPairGeometry(
      a,
      b,
      aCoordsBefore,
      bCoordsBefore,
      aPropsBefore,
      bPropsBefore,
      claimed,
      aClaimCountBefore,
      bClaimCountBefore,
    );
    outcome.skipped_reasons.push(
      `rolled_back_degraded_separation(${beforeNN.toFixed(2)}m->${afterNN.toFixed(2)}m)`,
    );
    return { kept: false, transitions: [] };
  }
  if (aSegFullAfter.length >= 2 && bSegFullAfter.length >= 2) {
    const after = pointwiseSeparation(aSegFullAfter, bSegFullAfter, SCAN_SAMPLE_M);
    outcome.min_after_m = Number(Math.min(...after.seps).toFixed(2));
  }
  return {
    kept: true,
    transitions: [
      aSegFull[0],
      aSegFull[aSegFull.length - 1],
      bSegFullRaw[0],
      bSegFullRaw[bSegFullRaw.length - 1],
    ],
  };
}

function projectOneSharedPair(
  pair: SharedPair,
  junctionPoints: Position[],
  claimed: ClaimedRanges,
): {
  outcome: FixOutcome;
  windowsFixedDelta: number;
  pairFixed: boolean;
  transitions: Position[];
} | null {
  const { a, b, ext } = pair;
  if (
    rangeBlocked(claimed, a, ext.aStartArc, ext.aEndArc) ||
    rangeBlocked(claimed, b, ext.bStartArc, ext.bEndArc)
  ) {
    return null;
  }
  const aSegFull = sliceArc(a.geometry.coordinates, ext.aStartArc, ext.aEndArc);
  const bSegFullRaw = sliceArc(b.geometry.coordinates, ext.bStartArc, ext.bEndArc);
  if (aSegFull.length < 2 || bSegFullRaw.length < 2) {
    return null;
  }
  const reversed = orientationNeedsReverse(aSegFull, bSegFullRaw);
  const bSegFull = reversed ? bSegFullRaw.slice().reverse() : bSegFullRaw;
  const aLen = lengthM(aSegFull);
  const bLen = lengthM(bSegFull);
  const { seps, count } = pointwiseSeparation(aSegFull, bSegFull, SCAN_SAMPLE_M);
  const minBeforeM = Math.min(...seps);
  const outcome: FixOutcome = {
    a_id: featureLabel(pair.a),
    b_id: featureLabel(pair.b),
    a_color: String(pair.a.properties.color),
    b_color: String(pair.b.properties.color),
    shared_len_m: Number(pair.ext.sharedLenM.toFixed(1)),
    min_before_m: Number(minBeforeM.toFixed(2)),
    min_after_m: Number(minBeforeM.toFixed(2)),
    windows_attempted: 0,
    windows_applied: 0,
    skipped_reasons: [],
  };
  const pockets = deficientArcPockets(seps, count, aLen, FLOOR_SEPARATION_M);
  if (pockets.length === 0) {
    return { outcome, windowsFixedDelta: 0, pairFixed: false, transitions: [] };
  }
  const aCoordsBefore = a.geometry.coordinates;
  const bCoordsBefore = b.geometry.coordinates;
  const aPropsBefore = { ...a.properties };
  const bPropsBefore = { ...b.properties };
  const aClaimsBefore = claimed.get(a);
  const bClaimsBefore = claimed.get(b);
  const aClaimCountBefore = aClaimsBefore ? aClaimsBefore.length : 0;
  const bClaimCountBefore = bClaimsBefore ? bClaimsBefore.length : 0;
  const aTotalLenBefore = lengthM(a.geometry.coordinates);
  const bTotalLenBefore = lengthM(b.geometry.coordinates);
  const projected = projectPairPockets(
    pair,
    aSegFull,
    bSegFull,
    reversed,
    bLen,
    aLen,
    pockets,
    junctionPoints,
    claimed,
    outcome,
  );
  if (!projected.anyApplied) {
    return { outcome, windowsFixedDelta: projected.windowsFixedDelta, pairFixed: false, transitions: [] };
  }
  const measured = remeasureOrRollback(
    pair,
    aSegFull,
    bSegFull,
    bSegFullRaw,
    aCoordsBefore,
    bCoordsBefore,
    aPropsBefore,
    bPropsBefore,
    aTotalLenBefore,
    bTotalLenBefore,
    claimed,
    aClaimCountBefore,
    bClaimCountBefore,
    outcome,
  );
  if (!measured.kept) {
    const applied = outcome.windows_applied;
    outcome.windows_applied = 0;
    return {
      outcome,
      windowsFixedDelta: projected.windowsFixedDelta - applied,
      pairFixed: false,
      transitions: [],
    };
  }
  return {
    outcome,
    windowsFixedDelta: projected.windowsFixedDelta,
    pairFixed: true,
    transitions: measured.transitions,
  };
}

function projectSharedCorridorFixes(pairs: SharedPair[], junctionPoints: Position[]) {
  const claimed: ClaimedRanges = new Map();
  const outcomes: FixOutcome[] = [];
  const pairTransitions = new Map<SharedPair, Position[]>();
  let windowsFixed = 0;
  let pairsWithAFix = 0;
  for (const pair of pairs) {
    const result = projectOneSharedPair(pair, junctionPoints, claimed);
    if (!result) continue;
    windowsFixed += result.windowsFixedDelta;
    if (result.pairFixed) {
      pairsWithAFix += 1;
      pairTransitions.set(pair, result.transitions);
    }
    outcomes.push(result.outcome);
  }
  return { outcomes, pairTransitions, windowsFixed, pairsWithAFix };
}

function measureNewkirkHotspot(candidates: SeparationCandidate[]): HotspotResult {
  const NEWKIRK_LAT_MIN = 40.630;
  const NEWKIRK_LAT_MAX = 40.638;
  const NEWKIRK_FLOOR_M = 8;
  const bqFeatures = candidates.filter((f) => {
    const ids = routeIdsOf(f.properties);
    return ids.includes("B") || ids.includes("Q");
  });
  let worstNewkirk = Infinity;
  for (let i = 0; i < bqFeatures.length; i += 1) {
    for (let j = i + 1; j < bqFeatures.length; j += 1) {
      const a = bqFeatures[i];
      const b = bqFeatures[j];
      if (String(a.properties.color).toUpperCase() === String(b.properties.color).toUpperCase()) {
        continue;
      }
      for (const point of a.geometry.coordinates) {
        if (point[1] < NEWKIRK_LAT_MIN || point[1] > NEWKIRK_LAT_MAX) continue;
        for (const other of b.geometry.coordinates) {
          if (other[1] < NEWKIRK_LAT_MIN - 0.002 || other[1] > NEWKIRK_LAT_MAX + 0.002) {
            continue;
          }
          worstNewkirk = Math.min(worstNewkirk, haversineM(point, other));
        }
      }
    }
  }
  const passed = !Number.isFinite(worstNewkirk) || worstNewkirk >= NEWKIRK_FLOOR_M;
  return {
    name: "bq_newkirk_plaza_separation",
    passed,
    detail: Number.isFinite(worstNewkirk)
      ? `worst B/Q separation near Newkirk Plaza (lat ${NEWKIRK_LAT_MIN}-${NEWKIRK_LAT_MAX}): ${worstNewkirk.toFixed(2)}m (floor ${NEWKIRK_FLOOR_M}m)`
      : "no B/Q vertices found in Newkirk Plaza lat band",
  };
}

type HotspotSeparation = {
  worst: number;
  where: string;
};

function worstFlatbushPairSeparation(
  pairs: SharedPair[],
  junctionPoints: Position[],
  pairTransitions: Map<SharedPair, Position[]>,
  inZone: (p: Position) => boolean,
): HotspotSeparation {
  let worst = Infinity;
  let where = "";
  for (const pair of pairs) {
    const { a, b } = pair;
    if (!a.geometry.coordinates.some(inZone) && !b.geometry.coordinates.some(inZone)) continue;
    const transitions = pairTransitions.get(pair) ?? [];
    for (const p of a.geometry.coordinates) {
      if (!inZone(p)) continue;
      if (nearAnyJunctionPoint(p, junctionPoints, JUNCTION_EXCLUSION_M)) continue;
      if (transitions.some((transition) =>
        haversineM(p, transition) <= JUNCTION_EXCLUSION_M,
      )) {
        continue;
      }
      let best = Infinity;
      for (const point of b.geometry.coordinates) {
        best = Math.min(best, haversineM(p, point));
      }
      if (best < worst) {
        worst = best;
        where = `${featureLabel(a)}(${a.properties.color}) vs ${featureLabel(b)}(${b.properties.color}) at [${p[0].toFixed(6)}, ${p[1].toFixed(6)}]`;
      }
    }
  }
  return { worst, where };
}

function measureFlatbushHotspot(
  pairs: SharedPair[],
  junctionPoints: Position[],
  pairTransitions: Map<SharedPair, Position[]>,
): HotspotResult {
  const FLATBUSH_ATLANTIC_LAT_MIN = 40.683;
  const FLATBUSH_ATLANTIC_LAT_MAX = 40.692;
  const FLATBUSH_ATLANTIC_LON_MIN = -73.985;
  const FLATBUSH_ATLANTIC_LON_MAX = -73.974;
  const NEAR_TOTAL_OVERLAP_M = 1.0;
  const inZone = (p: Position) =>
    p[1] >= FLATBUSH_ATLANTIC_LAT_MIN && p[1] <= FLATBUSH_ATLANTIC_LAT_MAX &&
    p[0] >= FLATBUSH_ATLANTIC_LON_MIN && p[0] <= FLATBUSH_ATLANTIC_LON_MAX;
  const { worst, where } = worstFlatbushPairSeparation(pairs, junctionPoints, pairTransitions, inZone);
  const passed = !Number.isFinite(worst) || worst >= NEAR_TOTAL_OVERLAP_M;
  return {
    name: "flatbush_atlantic_no_near_total_overlap",
    passed,
    detail: Number.isFinite(worst)
      ? `worst cross-color separation in Flatbush/Atlantic zone (excluding junction points): ${worst.toFixed(2)}m (floor ${NEAR_TOTAL_OVERLAP_M}m) -- ${where}`
      : "no cross-color pair found in Flatbush/Atlantic zone away from a junction",
  };
}

function writeSeparationReport(
  separationReportJsonPath: string,
  pairsScanned: number,
  pairsWithAFix: number,
  windowsFixed: number,
  hotspots: HotspotResult[],
  outcomes: FixOutcome[],
) {
  const report = {
    generated_at: new Date().toISOString(),
    source: "shared-corridor-separation-stage.ts",
    parameters: {
      floor_separation_m: FLOOR_SEPARATION_M,
      taper_m: TAPER_M,
      buffer_m: BUFFER_M,
      sample_m: SAMPLE_M,
      scan_sample_m: SCAN_SAMPLE_M,
      max_window_m: MAX_WINDOW_M,
      detect_dist_max_m: DETECT_DIST_MAX_M,
      min_shared_len_m: MIN_SHARED_LEN_M,
      junction_exclusion_m: JUNCTION_EXCLUSION_M,
    },
    summary: {
      pairs_scanned: pairsScanned,
      pairs_with_a_fix: pairsWithAFix,
      windows_fixed: windowsFixed,
    },
    hotspots,
    worst_offenders: outcomes
      .filter((o) => o.min_before_m < FLOOR_SEPARATION_M)
      .sort((p, q) => p.min_before_m - q.min_before_m)
      .slice(0, 30),
    pairs: outcomes,
  };
  writeFileSync(separationReportJsonPath, `${JSON.stringify(report, null, 2)}\n`);
}

export function applySharedCorridorSeparationStage({
  bundleArtifacts,
  separationReportJsonPath,
}: SharedCorridorSeparationStageInput): void {
  const classified = classifySharedCorridorPairs(bundleArtifacts.visualFeatures);
  const projected = projectSharedCorridorFixes(classified.pairs, classified.junctionPoints);
  const hotspots = [
    measureNewkirkHotspot(classified.candidates),
    measureFlatbushHotspot(classified.pairs, classified.junctionPoints, projected.pairTransitions),
  ];
  writeSeparationReport(
    separationReportJsonPath,
    classified.pairs.length,
    projected.pairsWithAFix,
    projected.windowsFixed,
    hotspots,
    projected.outcomes,
  );
  console.log(`[visual-network] shared-corridor separation: pairs scanned=${classified.pairs.length} pairs fixed=${projected.pairsWithAFix} windows fixed=${projected.windowsFixed}`);
  console.log(`[visual-network] wrote ${separationReportJsonPath}`);
  for (const hotspot of hotspots) {
    console.log(`[visual-network] shared-corridor hotspot ${hotspot.name}: ${hotspot.passed ? "PASS" : "FAIL"} -- ${hotspot.detail}`);
  }
  const failedHotspots = hotspots.filter((hotspot) => !hotspot.passed);
  if (failedHotspots.length > 0) {
    console.error(
      `[visual-network] *** shared-corridor separation hotspot check FAILED: ${failedHotspots.map((hotspot) => hotspot.name).join(", ")} ***`,
    );
    process.exit(1);
  }
}
