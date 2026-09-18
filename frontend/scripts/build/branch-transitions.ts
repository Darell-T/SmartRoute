// frontend/scripts/build/branch-transitions.mjs
// Pure helper: detect same-color same-anchor endpoint pairs across DIFFERENT
// bundles that are within maxBridgeM, emit a transition LineString connecting
// them. Used to render the visible connector where a colored lane leaves
// one bundle and enters another at a junction.
//
// No fs, no globals. Imported by build-subway-visual-network.mjs.

import type { Position, Feature, LineStringGeometry } from "./types.ts";

// A bundle-lane Feature carries the same permissive property bag the build
// attaches; the members read below are the ones this pass cares about.
type BranchLaneProperties = {
  color?: string;
  bundle_id?: string;
  from_anchor_id?: string | null;
  to_anchor_id?: string | null;
  materialized_bundle_id?: string | null;
};

type BranchLane = Feature<LineStringGeometry, BranchLaneProperties>;

type BranchEntry = {
  lane: BranchLane;
  endpoint: "from" | "to";
  coord: Position;
};

type BranchTransitionProperties = {
  visual_feature_type: "branch_transition";
  color: string | undefined;
  anchor_id: string;
  bundle_id_from: string | undefined;
  bundle_id_to: string | undefined;
  length_m: number;
};

type BranchTransitionFeature = Feature<LineStringGeometry, BranchTransitionProperties>;

type BuildBranchTransitionsOptions = {
  maxBridgeM?: number;
  minBridgeM?: number;
};

type BuildBranchTransitionsResult = {
  transitions: BranchTransitionFeature[];
  coincidentSkipped: number;
};

const EARTH_RADIUS_M = 6371000;

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number): number => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

/**
 * Build branch-transition LineString features connecting same-color lanes
 * that share an anchor across DIFFERENT bundles.
 *
 * @param {Array} bundleLanes  Array of GeoJSON Feature objects (LineString).
 * @param {object} [options]
 * @param {number} [options.maxBridgeM=90]  Max distance between endpoints.
 * @param {number} [options.minBridgeM=0.5]  Min distance to emit. Pairs whose
 *   endpoints are already coincident (within minBridgeM) do not require a
 *   visible connector and are dropped. Set to 0 to keep coincident pairs
 *   (e.g. for diagnostic purposes).
 * @returns {{ transitions: Array, coincidentSkipped: number }}
 *   `transitions` is the array of GeoJSON Feature objects (LineString) with
 *   properties:
 *     visual_feature_type: "branch_transition"
 *     color: string
 *     anchor_id: string
 *     bundle_id_from: string  (canonicalized: lexicographically smaller bundle_id)
 *     bundle_id_to: string    (canonicalized: lexicographically larger bundle_id)
 *     length_m: number        (raw float, not rounded)
 *   `coincidentSkipped` is the number of pairs dropped by the minBridgeM filter.
 *   These represent endpoints already touching; no visual connector is needed.
 */
function indexLaneEndpoints(bundleLanes: BranchLane[]): Map<string, BranchEntry[]> {
  const byAnchorColor = new Map<string, BranchEntry[]>();
  for (const lane of bundleLanes) {
    const p = lane.properties;
    const coords = lane.geometry?.coordinates ?? [];
    if (coords.length < 2) continue;
    const fromCoord = coords[0];
    const toCoord = coords[coords.length - 1];
    const endpoints: Array<[string | null | undefined, "from" | "to", Position]> = [
      [p.from_anchor_id, "from", fromCoord],
      [p.to_anchor_id, "to", toCoord],
    ];
    for (const [anchorId, endpoint, coord] of endpoints) {
      if (!anchorId) continue;
      const key = `${anchorId}|${p.color}`;
      const entries = byAnchorColor.get(key);
      if (entries) entries.push({ lane, endpoint, coord });
      else byAnchorColor.set(key, [{ lane, endpoint, coord }]);
    }
  }
  return byAnchorColor;
}

function shouldSkipBranchPair(a: BranchEntry, b: BranchEntry): boolean {
  if (a.lane.properties.bundle_id === b.lane.properties.bundle_id) return true;
  const materializedA = a.lane.properties.materialized_bundle_id;
  const materializedB = b.lane.properties.materialized_bundle_id;
  return Boolean(materializedA && materializedA === materializedB);
}

function emitTransitionsForAnchor(
  key: string,
  entries: BranchEntry[],
  maxBridgeM: number,
  minBridgeM: number,
): BuildBranchTransitionsResult {
  const transitions: BranchTransitionFeature[] = [];
  let coincidentSkipped = 0;
  if (entries.length < 2) return { transitions, coincidentSkipped };
  const anchorId = key.split("|")[0];
  for (let i = 0; i < entries.length; i++) {
    for (let j = i + 1; j < entries.length; j++) {
      const a = entries[i];
      const b = entries[j];
      if (shouldSkipBranchPair(a, b)) continue;
      const d = haversineM(a.coord, b.coord);
      if (d > maxBridgeM) continue;
      if (d < minBridgeM) {
        coincidentSkipped += 1;
        continue;
      }
      const fromA = (a.lane.properties.bundle_id ?? "") <= (b.lane.properties.bundle_id ?? "");
      const [fromEntry, toEntry] = fromA ? [a, b] : [b, a];
      transitions.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: [fromEntry.coord, toEntry.coord],
        },
        properties: {
          visual_feature_type: "branch_transition",
          color: a.lane.properties.color,
          anchor_id: anchorId,
          bundle_id_from: fromEntry.lane.properties.bundle_id,
          bundle_id_to: toEntry.lane.properties.bundle_id,
          length_m: d,
        },
      });
    }
  }
  return { transitions, coincidentSkipped };
}

/**
 * Build branch-transition LineString features connecting same-color lanes
 * that share an anchor across DIFFERENT bundles.
 */
export function buildBranchTransitions(
  bundleLanes: BranchLane[],
  { maxBridgeM = 90, minBridgeM = 0.5 }: BuildBranchTransitionsOptions = {},
): BuildBranchTransitionsResult {
  const out: BranchTransitionFeature[] = [];
  let coincidentSkipped = 0;
  for (const [key, entries] of indexLaneEndpoints(bundleLanes)) {
    const emitted = emitTransitionsForAnchor(key, entries, maxBridgeM, minBridgeM);
    out.push(...emitted.transitions);
    coincidentSkipped += emitted.coincidentSkipped;
  }
  return { transitions: out, coincidentSkipped };
}
