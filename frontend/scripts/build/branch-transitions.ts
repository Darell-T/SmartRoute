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
  [key: string]: unknown;
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
export function buildBranchTransitions(
  bundleLanes: BranchLane[],
  { maxBridgeM = 90, minBridgeM = 0.5 }: BuildBranchTransitionsOptions = {},
): BuildBranchTransitionsResult {
  const out: BranchTransitionFeature[] = [];
  let coincidentSkipped = 0;
  // Group lane endpoints by (anchor_id, color).
  const byAnchorColor = new Map<string, BranchEntry[]>();
  for (const lane of bundleLanes) {
    const p = lane.properties;
    const coords = lane.geometry?.coordinates ?? [];
    if (coords.length < 2) continue;
    const fromCoord = coords[0];
    const toCoord = coords[coords.length - 1];
    for (const [anchorId, endpoint, coord] of [
      [p.from_anchor_id, "from", fromCoord],
      [p.to_anchor_id, "to", toCoord],
    ] as Array<[string | null | undefined, "from" | "to", Position]>) {
      if (!anchorId) continue;
      const key = `${anchorId}|${p.color}`;
      if (!byAnchorColor.has(key)) byAnchorColor.set(key, []);
      byAnchorColor.get(key)!.push({ lane, endpoint, coord });
    }
  }

  for (const [key, entries] of byAnchorColor) {
    if (entries.length < 2) continue;
    const anchorId = key.split("|")[0];
    for (let i = 0; i < entries.length; i++) {
      for (let j = i + 1; j < entries.length; j++) {
        const a = entries[i];
        const b = entries[j];
        // Skip same-bundle pairs (those lanes already share the bundle's spine).
        if (a.lane.properties.bundle_id === b.lane.properties.bundle_id) continue;
        // Materialized physical bundles already encode their own shared spine,
        // branch tail, and fanout geometry. Adding a separate straight
        // transition inside the same materialized bundle creates the exact
        // triangular/chord artifact the fanout is meant to avoid.
        if (
          a.lane.properties.materialized_bundle_id &&
          a.lane.properties.materialized_bundle_id === b.lane.properties.materialized_bundle_id
        ) {
          continue;
        }
        const d = haversineM(a.coord, b.coord);
        if (d > maxBridgeM) continue;
        if (d < minBridgeM) {
          coincidentSkipped++;
          continue;
        }
        // Canonicalize the pair: sort by bundle_id lexicographically so the
        // same logical transition produces the same artifact bytes across runs
        // regardless of upstream iteration order.
        const idA = a.lane.properties.bundle_id;
        const idB = b.lane.properties.bundle_id;
        const [fromEntry, toEntry] = idA! <= idB! ? [a, b] : [b, a];
        out.push({
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
  }
  return { transitions: out, coincidentSkipped };
}
