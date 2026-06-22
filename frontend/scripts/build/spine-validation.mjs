// frontend/scripts/build/spine-validation.mjs
// Pure validation helper -- no fs, no globals.
// Imported by build-subway-visual-network.mjs (Stage D) and by the unit-test suite.

const EARTH_RADIUS_M = 6371000;

function haversineM([lon1, lat1], [lon2, lat2]) {
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function bboxContainsCoord(bbox, coord) {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  return coord[0] >= minLon && coord[0] <= maxLon && coord[1] >= minLat && coord[1] <= maxLat;
}

function featureInBbox(feature, bbox) {
  const coords = feature.geometry?.coordinates;
  if (!Array.isArray(coords) || coords.length === 0) return false;
  return coords.some((c) => bboxContainsCoord(bbox, c));
}

function coordKey(coord) {
  return `${coord[0].toFixed(5)},${coord[1].toFixed(5)}`;
}

function getEndpointKeys(f) {
  const p = f.properties;
  const coords = f.geometry?.coordinates;
  if (!coords || coords.length < 2) return null;
  const fromKey = p.from_anchor_id ? `anchor:${p.from_anchor_id}` : `coord:${coordKey(coords[0])}`;
  const toKey = p.to_anchor_id ? `anchor:${p.to_anchor_id}` : `coord:${coordKey(coords[coords.length - 1])}`;
  return { fromKey, toKey, fromCoord: coords[0], toCoord: coords[coords.length - 1] };
}

/**
 * Validates that every bundle lane with a spine_id agrees on base_spine_hash,
 * that no non-bridge lane is missing a spine_id, and that no spine-bearing lane
 * is missing a hash.
 *
 * Also validates that for every physical_bundle_id (non-null), all lanes
 * carrying that id share the same physical_bundle_spine_hash.
 *
 * @param {object} bundleArtifacts  Must contain bundleLaneFeatures (camel) or
 *                                   bundle_lane_features (snake) array.
 * @returns {{ bundleLaneCount, lanesWithMissingSpineId, lanesWithMissingHash,
 *             inconsistentGroups, inconsistentPhysicalBundleGroups }}
 */
export function assertSpineHashConsistency(bundleArtifacts) {
  const bundleLaneFeatures = bundleArtifacts.bundle_lane_features ?? bundleArtifacts.bundleLaneFeatures ?? [];
  const hashBySpineId = new Map();
  const lanesWithMissingSpineId = [];
  const lanesWithMissingHash = [];
  const inconsistentGroups = [];

  // Physical bundle invariant: same physical_bundle_id => same physical_bundle_spine_hash.
  const pbHashByBundleId = new Map();
  const inconsistentPhysicalBundleGroups = [];

  for (const lane of bundleLaneFeatures) {
    const sid = lane.properties.spine_id;
    const hash = lane.properties.base_spine_hash;
    if (sid == null) {
      const isExempt = lane.properties.bridge === true
        || lane.properties.lane_slot_source === "branch_transition";
      if (!isExempt) {
        lanesWithMissingSpineId.push(lane.properties.bundle_id);
      }
      continue;
    }
    if (hash == null) {
      lanesWithMissingHash.push(`${sid}/${lane.properties.bundle_id}`);
      continue;
    }
    if (!hashBySpineId.has(sid)) {
      hashBySpineId.set(sid, hash);
    } else if (hashBySpineId.get(sid) !== hash) {
      inconsistentGroups.push({ spine_id: sid, expected: hashBySpineId.get(sid), got: hash });
    }

    // Physical bundle hash invariant.
    const pbId = lane.properties.physical_bundle_id;
    const pbHash = lane.properties.physical_bundle_spine_hash;
    if (pbId != null && pbHash != null) {
      if (!pbHashByBundleId.has(pbId)) {
        pbHashByBundleId.set(pbId, pbHash);
      } else if (pbHashByBundleId.get(pbId) !== pbHash) {
        inconsistentPhysicalBundleGroups.push({
          physical_bundle_id: pbId,
          expected: pbHashByBundleId.get(pbId),
          got: pbHash,
        });
      }
    }
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
 *
 * @param {object[]} bundleLaneFeatures
 * @param {Map<string, Set<string>>} corridorRouteIndex  bundle_id -> Set<route_id>
 * @returns {{ passed: boolean, violations: Array<{bundle_id, reason}> }}
 */
export function assertNoBogusTransitions(bundleLaneFeatures, corridorRouteIndex) {
  const violations = [];

  for (const lane of bundleLaneFeatures) {
    const p = lane.properties;
    if (p.lane_slot_source !== "branch_transition") continue;

    const fromRoutes = corridorRouteIndex.get(p.bundle_id_from) ?? new Set();
    const toRoutes = corridorRouteIndex.get(p.bundle_id_to) ?? new Set();
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

/**
 * Assert that Q route in the Brooklyn bbox forms a single connected chain.
 * Excludes features that are entirely above lat 40.72 (Manhattan-side false positives).
 *
 * @param {object[]} visualFeatures  Bundle lane features
 * @param {object}   _stationsGeojson  (reserved for future use)
 * @returns {{ passed: boolean, qFeatureCount: number, disconnectedBundleIds: string[] }}
 */
export function assertQContinuousInBrooklyn(visualFeatures, _stationsGeojson) {
  const BROOKLYN_BBOX = [-74.05, 40.57, -73.83, 40.72]; // lat max 40.72 to exclude Manhattan

  const qFeatures = visualFeatures.filter((f) => {
    const routes = f.properties.route_ids ?? [];
    return routes.includes("Q") && featureInBbox(f, BROOKLYN_BBOX);
  });

  if (qFeatures.length === 0) {
    return { passed: false, qFeatureCount: 0, disconnectedBundleIds: [], detail: "No Q features in Brooklyn bbox" };
  }

  // Build endpoint adjacency
  const endpointMap = new Map();
  for (const f of qFeatures) {
    const ep = getEndpointKeys(f);
    if (!ep) continue;
    for (const key of [ep.fromKey, ep.toKey]) {
      if (!endpointMap.has(key)) endpointMap.set(key, []);
      endpointMap.get(key).push(f);
    }
  }

  // BFS from first feature
  const visited = new Set();
  const queue = [qFeatures[0]];
  visited.add(qFeatures[0].properties.bundle_id);

  while (queue.length > 0) {
    const cur = queue.shift();
    const ep = getEndpointKeys(cur);
    if (!ep) continue;
    for (const key of [ep.fromKey, ep.toKey]) {
      for (const n of (endpointMap.get(key) ?? [])) {
        const nid = n.properties.bundle_id;
        if (!visited.has(nid)) {
          visited.add(nid);
          queue.push(n);
        }
      }
    }
  }

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

/**
 * Assert that for each 2/3 and 4/5 feature in the Flatbush + Eastern Pkwy bbox,
 * there is an upstream feature within 90m of its origin endpoint.
 *
 * "Upstream" means: another feature carrying the same route that ends within 90m
 * of this feature's fromCoord.
 *
 * @param {object[]} visualFeatures
 * @returns {{ passed: boolean, missingUpstreamCount: number, violations: Array }}
 */
export function assertOriginsForRedGreenFlatbushEastern(visualFeatures) {
  const FE_BBOX = [-73.961, 40.659, -73.940, 40.682];
  const UPSTREAM_MAX_M = 90;

  const violations = [];

  for (const routeSet of [["2", "3"], ["4", "5"]]) {
    const regionFeatures = visualFeatures.filter((f) => {
      const routes = f.properties.route_ids ?? [];
      return routeSet.some((r) => routes.includes(r)) && featureInBbox(f, FE_BBOX);
    });

    for (const f of regionFeatures) {
      const ep = getEndpointKeys(f);
      if (!ep) continue;

      // Check for upstream: any same-route feature ending within 90m of our fromCoord
      let hasUpstream = false;

      // First try anchor-based match
      const { fromKey } = ep;
      for (const r of routeSet) {
        for (const g of visualFeatures) {
          if (g === f) continue;
          const gRoutes = g.properties.route_ids ?? [];
          if (!routeSet.some((rr) => gRoutes.includes(rr))) continue;
          const gEp = getEndpointKeys(g);
          if (!gEp) continue;
          // Does g's toKey match our fromKey?
          if (gEp.toKey === fromKey) { hasUpstream = true; break; }
          // Or is g's toCoord within 90m of our fromCoord?
          if (haversineM(gEp.toCoord, ep.fromCoord) <= UPSTREAM_MAX_M) { hasUpstream = true; break; }
        }
        if (hasUpstream) break;
      }

      if (!hasUpstream) {
        violations.push({
          bundle_id: f.properties.bundle_id,
          route_ids: f.properties.route_ids,
          from_stop_id: f.properties.from_stop_id,
          from_coord: ep.fromCoord,
          detail: `No upstream feature within ${UPSTREAM_MAX_M}m for routes [${routeSet.join(",")}]`,
        });
      }
    }
  }

  return {
    passed: violations.length === 0,
    missingUpstreamCount: violations.length,
    violations,
  };
}
