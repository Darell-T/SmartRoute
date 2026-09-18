// frontend/scripts/build/spine-validation.ts
// Pure validation helper -- no fs, no globals.
// Imported by build-subway-visual-network.mjs (Stage D) and by the unit-test suite.

import type { Position, BBox } from "./types.ts";

type LaneProperties = {
  spine_id?: string | null;
  base_spine_hash?: string | null;
  bundle_id?: string;
  bridge?: boolean;
  lane_slot_source?: string;
  physical_bundle_id?: string | null;
  physical_bundle_spine_hash?: string | null;
  route_ids?: string[];
  from_anchor_id?: string | null;
  to_anchor_id?: string | null;
  from_stop_id?: string | null;
  color?: string;
  color_route_ids?: string[];
  bundle_id_from?: string;
  bundle_id_to?: string;
};

type LaneFeature = {
  properties: LaneProperties;
  geometry?: { type?: string; coordinates?: Position[] } | null;
};

type BundleLaneArtifacts = {
  bundleLaneFeatures: LaneFeature[];
};

type EndpointKeys = {
  fromKey: string;
  toKey: string;
  fromCoord: Position;
  toCoord: Position;
};

const EARTH_RADIUS_M = 6371000;

function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function bboxContainsCoord(bbox: BBox, coord: Position): boolean {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  return coord[0] >= minLon && coord[0] <= maxLon && coord[1] >= minLat && coord[1] <= maxLat;
}

function featureInBbox(feature: LaneFeature, bbox: BBox): boolean {
  const coords = feature.geometry?.coordinates;
  if (!Array.isArray(coords) || coords.length === 0) return false;
  return coords.some((c) => bboxContainsCoord(bbox, c));
}

function coordKey(coord: Position): string {
  return `${coord[0].toFixed(5)},${coord[1].toFixed(5)}`;
}

function getEndpointKeys(f: LaneFeature): EndpointKeys | null {
  const p = f.properties;
  const coords = f.geometry?.coordinates;
  if (!coords || coords.length < 2) return null;
  const fromKey = p.from_anchor_id ? `anchor:${p.from_anchor_id}` : `coord:${coordKey(coords[0])}`;
  const toKey = p.to_anchor_id ? `anchor:${p.to_anchor_id}` : `coord:${coordKey(coords[coords.length - 1])}`;
  return { fromKey, toKey, fromCoord: coords[0], toCoord: coords[coords.length - 1] };
}

type HashConflict = {
  spine_id: string;
  expected: string;
  got: string;
};

type PhysicalBundleHashConflict = {
  physical_bundle_id: string;
  expected: string;
  got: string;
};

function recordSpineHash(
  store: Map<string, string>,
  conflicts: HashConflict[],
  spineId: string,
  hash: string,
): void {
  const expected = store.get(spineId);
  if (expected === undefined) {
    store.set(spineId, hash);
    return;
  }
  if (expected !== hash) {
    conflicts.push({ spine_id: spineId, expected, got: hash });
  }
}

function recordPhysicalBundleHash(
  store: Map<string, string>,
  conflicts: PhysicalBundleHashConflict[],
  bundleId: string,
  hash: string,
): void {
  const expected = store.get(bundleId);
  if (expected === undefined) {
    store.set(bundleId, hash);
    return;
  }
  if (expected !== hash) {
    conflicts.push({ physical_bundle_id: bundleId, expected, got: hash });
  }
}

/**
 * Validates that every bundle lane with a spine_id agrees on base_spine_hash,
 * that no non-bridge lane is missing a spine_id, and that no spine-bearing lane
 * is missing a hash.
 *
 * Also validates that for every physical_bundle_id (non-null), all lanes
 * carrying that id share the same physical_bundle_spine_hash.
 */
export function assertSpineHashConsistency(bundleArtifacts: BundleLaneArtifacts) {
  const bundleLaneFeatures = bundleArtifacts.bundleLaneFeatures;
  const hashBySpineId = new Map<string, string>();
  const lanesWithMissingSpineId: Array<string | undefined> = [];
  const lanesWithMissingHash: string[] = [];
  const inconsistentGroups: HashConflict[] = [];
  const pbHashByBundleId = new Map<string, string>();
  const inconsistentPhysicalBundleGroups: PhysicalBundleHashConflict[] = [];

  for (const lane of bundleLaneFeatures) {
    const sid = lane.properties.spine_id;
    const hash = lane.properties.base_spine_hash;
    if (sid == null) {
      if (!((lane.properties).bridge === true || (lane.properties).lane_slot_source === "branch_transition")) {
        lanesWithMissingSpineId.push(lane.properties.bundle_id);
      }
      continue;
    }
    if (hash == null) {
      lanesWithMissingHash.push(`${sid}/${lane.properties.bundle_id}`);
      continue;
    }
    recordSpineHash(hashBySpineId, inconsistentGroups, sid, hash);

    const pbId = lane.properties.physical_bundle_id;
    const pbHash = lane.properties.physical_bundle_spine_hash;
    if (pbId == null || pbHash == null) continue;
    recordPhysicalBundleHash(pbHashByBundleId, inconsistentPhysicalBundleGroups, pbId, pbHash);
  }
  return {
    bundleLaneCount: bundleLaneFeatures.length,
    lanesWithMissingSpineId,
    lanesWithMissingHash,
    inconsistentGroups,
    inconsistentPhysicalBundleGroups,
  };
}

/**
 * Assert that every branch_transition feature's color is present in at least
 * one route of each of its endpoint corridors.
 */
export function assertNoBogusTransitions(
  bundleLaneFeatures: LaneFeature[],
  corridorRouteIndex: Map<string, Set<string>>,
) {
  const violations = [];

  for (const lane of bundleLaneFeatures) {
    const p = lane.properties;
    if (p.lane_slot_source !== "branch_transition") continue;

    const fromRoutes = corridorRouteIndex.get(p.bundle_id_from ?? "") ?? new Set<string>();
    const toRoutes = corridorRouteIndex.get(p.bundle_id_to ?? "") ?? new Set<string>();
    const colorRouteIds = p.color_route_ids ?? [];

    const colorInFrom = colorRouteIds.some((r) => fromRoutes.has(r));
    const colorInTo = colorRouteIds.some((r) => toRoutes.has(r));

    if (!colorInFrom && !colorInTo) {
      violations.push({
        bundle_id: p.bundle_id,
        reason: `color ${p.color} (routes ${colorRouteIds.join(",")}) absent from both corridors: from=${p.bundle_id_from} to=${p.bundle_id_to}`,
      });
    }
  }

  return { passed: violations.length === 0, violations };
}

function connectedBundleIds(features: LaneFeature[]): Set<string | undefined> {
  const endpointMap = new Map<string, LaneFeature[]>();
  for (const feature of features) {
    const endpoints = getEndpointKeys(feature);
    if (!endpoints) continue;
    for (const key of [endpoints.fromKey, endpoints.toKey]) {
      const members = endpointMap.get(key);
      if (members) members.push(feature);
      else endpointMap.set(key, [feature]);
    }
  }
  const visited = new Set<string | undefined>();
  const queue = [features[0]];
  visited.add(features[0].properties.bundle_id);

  while (queue.length > 0) {
    const cur = queue.shift();
    if (!cur) break;
    const ep = getEndpointKeys(cur);
    if (!ep) continue;
    for (const key of [ep.fromKey, ep.toKey]) {
      for (const neighbor of endpointMap.get(key) ?? []) {
        const nid = neighbor.properties.bundle_id;
        if (visited.has(nid)) continue;
        visited.add(nid);
        queue.push(neighbor);
      }
    }
  }
  return visited;
}

/**
 * Assert that Q route in the Brooklyn bbox forms a single connected chain.
 * Excludes features that are entirely above lat 40.72 (Manhattan-side false positives).
 */
export function assertQContinuousInBrooklyn(visualFeatures: LaneFeature[], _stationsGeojson?: null) {
  const brooklynBbox: BBox = [-74.05, 40.57, -73.83, 40.72];
  const qFeatures = visualFeatures.filter((feature) => {
    const routes = feature.properties.route_ids ?? [];
    return routes.includes("Q") && featureInBbox(feature, brooklynBbox);
  });

  if (qFeatures.length === 0) {
    return { passed: false, qFeatureCount: 0, disconnectedBundleIds: [], detail: "No Q features in Brooklyn bbox" };
  }

  const visited = connectedBundleIds(qFeatures);
  const unreached = qFeatures
    .filter((f) => !visited.has(f.properties.bundle_id))
    .map((f) => f.properties.bundle_id);

  return {
    passed: unreached.length === 0,
    qFeatureCount: qFeatures.length,
    disconnectedBundleIds: unreached,
    detail: unreached.length === 0
      ? `Q forms single connected chain (${qFeatures.length} features)`
      : `Q has ${unreached.length} disconnected segment(s) in Brooklyn`,
  };
}

function hasUpstreamOrigin(
  feature: LaneFeature,
  visualFeatures: LaneFeature[],
  routeSet: string[],
  maxDistanceM: number,
): boolean {
  const ep = getEndpointKeys(feature);
  if (!ep) return true;

  for (const g of visualFeatures) {
    if (g === feature) continue;
    const gRoutes = g.properties.route_ids ?? [];
    if (!routeSet.some((routeId) => gRoutes.includes(routeId))) continue;
    const gEp = getEndpointKeys(g);
    if (!gEp) continue;
    if (gEp.toKey === ep.fromKey) return true;
    if (haversineM(gEp.toCoord, ep.fromCoord) <= maxDistanceM) return true;
  }
  return false;
}

/**
 * Assert that for each 2/3 and 4/5 feature in the Flatbush + Eastern Pkwy bbox,
 * there is an upstream feature within 90m of its origin endpoint.
 *
 * "Upstream" means: another feature carrying the same route that ends within 90m
 * of this feature's fromCoord.
 */
export function assertOriginsForRedGreenFlatbushEastern(visualFeatures: LaneFeature[]) {
  const feBbox: BBox = [-73.961, 40.659, -73.940, 40.682];
  const upstreamMaxM = 90;
  const violations = [];

  for (const routeSet of [["2", "3"], ["4", "5"]]) {
    const regionFeatures = visualFeatures.filter((feature) => {
      const routes = feature.properties.route_ids ?? [];
      return routeSet.some((routeId) => routes.includes(routeId)) && featureInBbox(feature, feBbox);
    });
    for (const f of regionFeatures) {
      if (hasUpstreamOrigin(f, visualFeatures, routeSet, upstreamMaxM)) continue;
      const ep = getEndpointKeys(f);
      if (!ep) continue;
      violations.push({
        bundle_id: f.properties.bundle_id,
        route_ids: f.properties.route_ids,
        from_stop_id: f.properties.from_stop_id,
        from_coord: ep.fromCoord,
        detail: `No upstream feature within ${upstreamMaxM}m for routes [${routeSet.join(",")}]`,
      });
    }
  }

  return {
    passed: violations.length === 0,
    missingUpstreamCount: violations.length,
    violations,
  };
}
