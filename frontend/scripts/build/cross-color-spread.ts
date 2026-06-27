// frontend/scripts/build/cross-color-spread.ts
// Pure helper -- no fs, no globals. Detects clusters of DIFFERENT-color
// renderable features that run alongside each other on a shared physical
// corridor, and assigns each color a centered lane slot by canonical color
// rank. The build script applies the actual perpendicular offset.
//
// Same-color overlaps are intentionally ignored: Phase 3d (same-color-merge)
// already collapses those into one stroke.

import { computePairOverlap, resamplePolyline, pointToPolylineMinDistM } from "./physical-bundle.ts";
import { BUNDLE_COLOR_ORDER } from "./lane-order.ts";
import type { Feature, LineStringGeometry, Position, RouteId } from "./types.ts";

const EARTH_RADIUS_M = 6371000;
const M_PER_DEG_LAT = 111320;

type CrossColorFeatureProperties = {
  bundle_id?: string;
  color?: unknown;
  route_ids?: RouteId[];
  lane_slot_semantic?: unknown;
  lane_slot?: unknown;
  lane_slot_source?: unknown;
  length_m?: number | null;
  [key: string]: unknown;
};

export type CrossColorSpreadFeature = Feature<LineStringGeometry, CrossColorFeatureProperties>;

type CrossColorSpine = {
  spine_id: string;
  geometry: LineStringGeometry;
  length_m: number | null;
  color: string;
  feature: CrossColorSpreadFeature;
};

type CrossColorGroupMember = {
  bundle_id: string | undefined;
  color: string;
  route_ids: RouteId[];
  lane_slot: number | undefined;
  _featureRef: CrossColorSpreadFeature;
};

export type CrossColorGroup = {
  members: CrossColorGroupMember[];
};

export type DetectCrossColorAdjacencyOptions = {
  sharedFractionMin?: number;
  sharedLenMinM?: number;
  avgDistMaxM?: number;
  tangentMaxDeg?: number;
  resampleM?: number;
};

export type DetectCrossColorAdjacencyResult = {
  groups: CrossColorGroup[];
};

export type SharedArcExtent = {
  aStartArc: number;
  aEndArc: number;
  bStartArc: number;
  bEndArc: number;
  sharedLenM: number;
};

type SharedArcExtentOptions = {
  resampleM?: number;
  distMaxM?: number;
  minSharedLenM?: number;
};

type RunExtent = {
  startIdx: number;
  endIdx: number;
};

function metersPerDegLng(lat: number): number {
  return Math.cos((lat * Math.PI) / 180) * M_PER_DEG_LAT;
}
function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number): number => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}
function cumulativeArc(coords: Position[]): number[] {
  const arc = [0];
  for (let i = 1; i < coords.length; i += 1) {
    arc.push(arc[i - 1] + haversineM(coords[i - 1], coords[i]));
  }
  return arc;
}

function isPosition(value: unknown): value is Position {
  return Array.isArray(value) && value.length >= 2 && typeof value[0] === "number" && typeof value[1] === "number";
}

function isCrossColorFeature(feature: unknown): feature is CrossColorSpreadFeature {
  if (!feature || typeof feature !== "object") return false;
  const maybeFeature = feature as { geometry?: { type?: unknown; coordinates?: unknown }; properties?: unknown };
  return (
    maybeFeature.geometry?.type === "LineString" &&
    Array.isArray(maybeFeature.geometry.coordinates) &&
    maybeFeature.geometry.coordinates.length >= 2 &&
    maybeFeature.geometry.coordinates.every(isPosition) &&
    maybeFeature.properties !== null &&
    typeof maybeFeature.properties === "object"
  );
}

// NOTE: this rank map mirrors the private `rank` in lane-order.ts. Both derive
// from BUNDLE_COLOR_ORDER. If that list changes, both stay in sync only because
// they import the same constant. Kept local (not exported from lane-order.ts)
// to avoid widening that module's API for a single consumer.
const RANK = new Map<string, number>(BUNDLE_COLOR_ORDER.map((c, i) => [String(c).toUpperCase(), i]));
function colorRank(color: string): number {
  const r = RANK.get(String(color).toUpperCase());
  return r === undefined ? Number.POSITIVE_INFINITY : r;
}

/**
 * @param {Array} features  Renderable line features. Each must have
 *   geometry.coordinates and properties { bundle_id, color, route_ids,
 *   lane_slot_semantic }.
 * @param {object} options
 * @param {number} [options.sharedFractionMin=0.6]
 * @param {number} [options.sharedLenMinM=250]
 * @param {number} [options.avgDistMaxM=18]
 * @param {number} [options.tangentMaxDeg=30]
 * @param {number} [options.resampleM=25]
 * @returns {{ groups: Array<{ members: Array<{ bundle_id, color, route_ids, lane_slot, _featureRef }> }> }}
 */
export function detectCrossColorAdjacency(
  features: CrossColorSpreadFeature[],
  options: DetectCrossColorAdjacencyOptions = {},
): DetectCrossColorAdjacencyResult {
  const {
    sharedFractionMin = 0.6,
    sharedLenMinM = 250,
    avgDistMaxM = 18,
    tangentMaxDeg = 30,
    resampleM = 25,
  } = options;

  const candidates = features.filter((f): f is CrossColorSpreadFeature => {
    if (!isCrossColorFeature(f)) return false;
    const slot = Number(f.properties?.lane_slot_semantic ?? f.properties?.lane_slot ?? 0);
    return (
      slot === 0 &&
      // Members already offset by the continuous materialization carry their lane
      // offset baked into geometry; re-spreading them would double-offset.
      f.properties?.lane_slot_source !== "physical_bundle_continuous" &&
      Boolean(f.properties?.color)
    );
  });

  const spines: CrossColorSpine[] = candidates.map((f, idx) => ({
    spine_id: String(idx),
    geometry: f.geometry,
    length_m: f.properties.length_m ?? null,
    color: String(f.properties.color).toUpperCase(),
    feature: f,
  }));

  const parent = spines.map((_, i) => i);
  const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const union = (i: number, j: number): void => { parent[find(i)] = find(j); };

  for (let i = 0; i < spines.length; i++) {
    for (let j = i + 1; j < spines.length; j++) {
      if (spines[i].color === spines[j].color) continue;
      const o = computePairOverlap(spines[i], spines[j], { resampleM, distMaxM: avgDistMaxM });
      if (o.avgDistM > avgDistMaxM) continue;
      if (o.sharedFractionShorter < sharedFractionMin) continue;
      if (o.sharedLenM < sharedLenMinM) continue;
      if (o.tangentDeltaAvgDeg > tangentMaxDeg) continue;
      union(i, j);
    }
  }

  const byRoot = new Map<number, CrossColorSpine[]>();
  for (let i = 0; i < spines.length; i++) {
    const r = find(i);
    let members = byRoot.get(r);
    if (!members) {
      members = [];
      byRoot.set(r, members);
    }
    members.push(spines[i]);
  }

  const groups: CrossColorGroup[] = [];
  for (const members of byRoot.values()) {
    const distinctColors = [...new Set(members.map((m) => m.color))];
    if (distinctColors.length < 2) continue;

    // Tie-break by string so the slot assignment is deterministic even when two
    // colors share a rank -- notably two unknown colors both return Infinity and
    // (Infinity - Infinity) is NaN, which yields an unspecified sort order in V8.
    distinctColors.sort(
      (a, b) => (colorRank(a) - colorRank(b)) || String(a).localeCompare(String(b)),
    );
    const k = distinctColors.length;
    const slotForColor = new Map<string, number>(
      distinctColors.map((c, idx) => [c, idx - (k - 1) / 2]),
    );

    // _featureRef is a LIVE reference to the input feature. The build script
    // mutates its geometry in place to bake the offset. Callers must NOT
    // JSON.stringify a group object directly -- that would pull the whole
    // feature (and its full coordinate array) into the debug artifact.
    groups.push({
      members: members.map((m) => ({
        bundle_id: m.feature.properties.bundle_id,
        color: m.color,
        route_ids: m.feature.properties.route_ids ?? [],
        lane_slot: slotForColor.get(m.color),
        _featureRef: m.feature,
      })),
    });
  }

  return { groups };
}

/**
 * Find the contiguous stretch where two polylines run close together, and
 * return the arc-length extent of that stretch on EACH polyline. Unlike
 * computePairOverlap (which reports a fraction over the whole shorter line),
 * this returns the actual sub-extent so a caller can offset ONLY that stretch.
 * Gated on the ABSOLUTE shared length, so two long lines that share a short
 * segment (e.g. A/C/E and G near Hoyt-Schermerhorn) still qualify.
 *
 * @returns {{ aStartArc, aEndArc, bStartArc, bEndArc, sharedLenM } | null}
 */
export function findSharedArcExtent(
  coordsA: Position[],
  coordsB: Position[],
  options: SharedArcExtentOptions = {},
): SharedArcExtent | null {
  const { resampleM = 25, distMaxM = 18, minSharedLenM = 250 } = options;
  if (!Array.isArray(coordsA) || coordsA.length < 2) return null;
  if (!Array.isArray(coordsB) || coordsB.length < 2) return null;

  const ra = resamplePolyline(coordsA, resampleM);
  const rb = resamplePolyline(coordsB, resampleM);
  if (ra.length < 2 || rb.length < 2) return null;

  const arcA = cumulativeArc(ra);
  const arcB = cumulativeArc(rb);

  // Longest contiguous run of samples on `samples` that are within distMaxM of
  // `other`. Returns { startIdx, endIdx } (inclusive) or null.
  function longestRun(samples: Position[], other: Position[]): RunExtent | null {
    let best: RunExtent | null = null;
    let curStart = -1;
    for (let i = 0; i < samples.length; i += 1) {
      const near = pointToPolylineMinDistM(samples[i], other) <= distMaxM;
      if (near) {
        if (curStart === -1) curStart = i;
        const len = i - curStart;
        if (!best || len > best.endIdx - best.startIdx) {
          best = { startIdx: curStart, endIdx: i };
        }
      } else {
        curStart = -1;
      }
    }
    return best;
  }

  const runA = longestRun(ra, rb);
  const runB = longestRun(rb, ra);
  if (!runA || !runB) return null;

  const aStartArc = arcA[runA.startIdx];
  const aEndArc = arcA[runA.endIdx];
  const bStartArc = arcB[runB.startIdx];
  const bEndArc = arcB[runB.endIdx];
  const sharedLenM = Math.min(aEndArc - aStartArc, bEndArc - bStartArc);
  if (sharedLenM < minSharedLenM) return null;

  return { aStartArc, aEndArc, bStartArc, bEndArc, sharedLenM };
}

/**
 * Apply a perpendicular offset to a polyline, but ONLY over the arc range
 * [startArc, endArc], ramping 0 -> full over a taper zone of taperM meters on
 * each side. Returns a NEW coords array with the SAME vertex count (one
 * continuous polyline -- no splitting, no connectors). Vertices whose ramp
 * factor is 0 are returned byte-identical so the non-shared portion of the
 * line is untouched.
 *
 * @param {Array} coords
 * @param {number} startArc  meters from line start where full offset begins
 * @param {number} endArc    meters from line start where full offset ends
 * @param {number} offsetMeters  signed offset magnitude (right-hand normal)
 * @param {number} [taperM=40]
 * @returns {Array} new coordinates
 */
export function offsetPolylineOverExtent(
  coords: Position[],
  startArc: number,
  endArc: number,
  offsetMeters: number,
  taperM = 40,
): Position[] {
  if (!Array.isArray(coords) || coords.length < 2) return coords;
  if (!Number.isFinite(offsetMeters) || offsetMeters === 0) return coords.map((c) => c);

  const arc = cumulativeArc(coords);

  // Per-vertex meters-per-degree-lng (lat-dependent) + projected meters frame.
  const mPerLng = coords.map((c) => metersPerDegLng(c[1]));
  const projected: Position[] = coords.map((c, i) => [c[0] * mPerLng[i], c[1] * M_PER_DEG_LAT]);

  // Per-segment right-hand unit normals.
  const segNormals: Position[] = [];
  for (let i = 0; i < projected.length - 1; i += 1) {
    const dx = projected[i + 1][0] - projected[i][0];
    const dy = projected[i + 1][1] - projected[i][1];
    const len = Math.hypot(dx, dy);
    if (len === 0) {
      segNormals.push([0, 0]);
      continue;
    }
    segNormals.push([dy / len, -dx / len]);
  }

  // Per-vertex averaged unit normal (no miter cap -- shared corridors are gentle).
  function vertexNormal(i: number): Position {
    if (i === 0) return segNormals[0];
    if (i === projected.length - 1) return segNormals[segNormals.length - 1];
    const a = segNormals[i - 1];
    const b = segNormals[i];
    const sx = a[0] + b[0];
    const sy = a[1] + b[1];
    const sl = Math.hypot(sx, sy);
    if (sl < 1e-9) return b;
    return [sx / sl, sy / sl];
  }

  // Ramp factor at arc position s.
  function ramp(s: number): number {
    if (taperM <= 0) return s >= startArc && s <= endArc ? 1 : 0;
    if (s <= startArc - taperM || s >= endArc + taperM) return 0;
    if (s >= startArc && s <= endArc) return 1;
    if (s < startArc) return (s - (startArc - taperM)) / taperM;
    return ((endArc + taperM) - s) / taperM; // s > endArc
  }

  return coords.map((c, i) => {
    const r = Math.max(0, Math.min(1, ramp(arc[i])));
    if (r === 0) return c; // untouched (byte-identical)
    const n = vertexNormal(i);
    const off = offsetMeters * r;
    const dLon = (n[0] * off) / mPerLng[i];
    const dLat = (n[1] * off) / M_PER_DEG_LAT;
    return [c[0] + dLon, c[1] + dLat];
  });
}

/**
 * Apply a variable lane-slot offset across a whole fanout path. The slot
 * changes by arc length, not vertex index, so densely-sampled curves and
 * sparse straight pieces taper at the same physical rate.
 *
 * @param {Array} coords
 * @param {number} fromSlot
 * @param {number} toSlot
 * @param {number} laneWidthM
 * @returns {Array}
 */
export function offsetPolylineBySlotRamp(
  coords: Position[],
  fromSlot: number,
  toSlot: number,
  laneWidthM: number,
): Position[] {
  if (!Array.isArray(coords) || coords.length < 2) return coords;
  if (!Number.isFinite(fromSlot) || !Number.isFinite(toSlot)) return coords.map((c) => c);
  if (!Number.isFinite(laneWidthM) || laneWidthM === 0) return coords.map((c) => c);
  if (fromSlot === 0 && toSlot === 0) return coords.map((c) => c);

  const arc = cumulativeArc(coords);
  const total = arc[arc.length - 1] || 1;
  const mPerLng = coords.map((c) => metersPerDegLng(c[1]));
  const projected: Position[] = coords.map((c, i) => [c[0] * mPerLng[i], c[1] * M_PER_DEG_LAT]);

  const segNormals: Position[] = [];
  for (let i = 0; i < projected.length - 1; i += 1) {
    const dx = projected[i + 1][0] - projected[i][0];
    const dy = projected[i + 1][1] - projected[i][1];
    const len = Math.hypot(dx, dy);
    segNormals.push(len === 0 ? [0, 0] : [dy / len, -dx / len]);
  }

  function vertexNormal(i: number): Position {
    if (i === 0) return segNormals[0];
    if (i === projected.length - 1) return segNormals[segNormals.length - 1];
    const a = segNormals[i - 1];
    const b = segNormals[i];
    const sx = a[0] + b[0];
    const sy = a[1] + b[1];
    const sl = Math.hypot(sx, sy);
    if (sl < 1e-9) return b;
    return [sx / sl, sy / sl];
  }

  const smoothstep = (t: number): number => t * t * (3 - 2 * t);

  return coords.map((c, i) => {
    const t = smoothstep(Math.max(0, Math.min(1, arc[i] / total)));
    const slot = fromSlot + (toSlot - fromSlot) * t;
    const offsetMeters = slot * laneWidthM;
    if (offsetMeters === 0) return c;
    const n = vertexNormal(i);
    const dLon = (n[0] * offsetMeters) / mPerLng[i];
    const dLat = (n[1] * offsetMeters) / M_PER_DEG_LAT;
    return [c[0] + dLon, c[1] + dLat];
  });
}
