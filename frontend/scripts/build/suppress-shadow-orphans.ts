// Pure helper -- no fs, no globals.
//
// Drops "shadow" lanes: an error-severity orphan whose geometry mostly overlays a
// DIFFERENT-color route's line. These are redundant secondary/rush-hour service
// patterns (e.g. the green 5 rush pattern that runs on top of the red 2 up White
// Plains Rd to Nereid). They are not their route's primary path -- they were
// flagged as orphans (not cleanly connected) AND they shadow another route -- so
// drawing them just produces a crossing/hook over the other route's trunk. Apple
// and Transit suppress these. The route's primary geometry (carried by a different,
// non-orphan feature) is untouched.

import type { Position, Feature, LineStringGeometry, VisualFeatureProperties } from "./types.ts";

type LineFeature = Feature<LineStringGeometry, VisualFeatureProperties>;

const EARTH_RADIUS_M = 6371000;

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const r = Math.PI / 180;
  const dLat = (lat2 - lat1) * r;
  const dLon = (lon2 - lon1) * r;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function nearestM(p: Position, coords: Position[]): number {
  let best = Infinity;
  for (const q of coords) {
    const d = haversineM(p, q);
    if (d < best) best = d;
  }
  return best;
}

/**
 * @param {Array} features
 * @param {object} [options]
 * @param {number} [options.shadowDistM=18]    a vertex within this of another-color line "shadows" it
 * @param {number} [options.shadowFracMin=0.7] drop only if >= this fraction of vertices shadow another color
 * @returns {{ features: Array, removedIds: string[] }}
 */
export function suppressShadowOrphans(
  features: LineFeature[],
  options: { shadowDistM?: number; shadowFracMin?: number } = {},
): { features: LineFeature[]; removedIds: string[] } {
  const { shadowDistM = 18, shadowFracMin = 0.7 } = options;
  const lines = features.filter(
    (f) => f.geometry?.type === "LineString" && Array.isArray(f.geometry.coordinates) && f.geometry.coordinates.length >= 2,
  );

  const removed = new Set<LineFeature>();
  const removedIds: string[] = [];

  for (const f of lines) {
    const p = f.properties ?? {};
    if (p.qa_orphan_severity !== "error") continue;
    const color = p.color;
    if (!color) continue;
    const myRoutes = new Set(p.route_ids ?? []);

    // sample this feature's vertices and test how many shadow a DIFFERENT-color,
    // not-fully-shared other line.
    const sample = f.geometry.coordinates.filter((_, i) => i % 3 === 0);
    let shadow = 0;
    for (const pt of sample) {
      let near = false;
      for (const other of lines) {
        if (other === f) continue;
        if (other.properties?.color === color) continue; // different color only
        // skip lines that carry exactly the same route set (true shared trunk)
        const otherRoutes = other.properties?.route_ids ?? [];
        if (otherRoutes.length && otherRoutes.every((r) => myRoutes.has(r)) && myRoutes.size === otherRoutes.length) continue;
        if (nearestM(pt, other.geometry.coordinates) <= shadowDistM) { near = true; break; }
      }
      if (near) shadow += 1;
    }
    if (sample.length && shadow / sample.length >= shadowFracMin) {
      removed.add(f);
      removedIds.push(String(p.corridor_id ?? p.bundle_id ?? "?"));
    }
  }

  return { features: features.filter((f) => !removed.has(f)), removedIds };
}
