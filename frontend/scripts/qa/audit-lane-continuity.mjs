#!/usr/bin/env node
// frontend/scripts/qa/audit-lane-continuity.mjs
// Phase 3c: Lane continuity audit — detects orphan origins, broken route
// handoffs, and lane-slot discontinuities in the visual GeoJSON artifact.
//
// Outputs:
//   frontend/artifacts/debug/subway-network.visual-debug-orphan-origins.geojson
//   frontend/artifacts/debug/subway-network.visual-debug-route-handoffs.geojson
//   frontend/artifacts/debug/subway-network.visual-debug-lane-discontinuities.geojson
//   docs/subway-lane-continuity-audit.md
//   docs/assets/subway-real-app-route-qa/<timestamp>/  (mirrors of the above)

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "../..");
const publicDir = resolve(frontendRoot, "public");
const debugDir = resolve(frontendRoot, "artifacts", "debug");
const docsDir = resolve(frontendRoot, "..", "docs");
const docsAssetsDir = resolve(docsDir, "assets", "subway-real-app-route-qa");
mkdirSync(debugDir, { recursive: true });

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

function coordKey(coord) {
  // Round to ~1m precision for endpoint matching
  return `${coord[0].toFixed(5)},${coord[1].toFixed(5)}`;
}

function bboxContains(bbox, coord) {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  return coord[0] >= minLon && coord[0] <= maxLon &&
         coord[1] >= minLat && coord[1] <= maxLat;
}

function featureIntersectsBbox(feature, bbox) {
  const coords = feature.geometry?.coordinates;
  if (!Array.isArray(coords) || coords.length === 0) return false;
  return coords.some((c) => bboxContains(bbox, c));
}

const REGIONS = {
  "Grand Army Plaza":    [-73.973, 40.668, -73.967, 40.674],
  "Downtown Brooklyn":   [-73.991, 40.687, -73.978, 40.694],
  "Jamaica Center":      [-73.812, 40.700, -73.795, 40.708],
  "Prospect Park / Brighton trunk": [-73.975, 40.655, -73.957, 40.672],
  "Flatbush + Eastern Pkwy": [-73.961, 40.659, -73.940, 40.682],
};

// Routes that should share a spine in each region (per user screenshots)
const REGION_EXPECTED_SHARED = {
  "Grand Army Plaza": { routes: ["B", "Q", "2", "3", "4", "5"], note: "B/Q approach from south, 2/3/4/5 from north" },
  "Downtown Brooklyn": { routes: ["B", "D", "F", "M", "N", "Q", "R", "A", "C"], note: "Multi-line hub" },
  "Jamaica Center": { routes: ["E", "J", "Z"], note: "E terminus + J/Z hub" },
  "Prospect Park / Brighton trunk": { routes: ["B", "Q"], note: "B+Q should share spine on Brighton trunk" },
  "Flatbush + Eastern Pkwy": { routes: ["2", "3", "4", "5"], note: "IRT split needs upstream origin" },
};

// =====================================================================
// Load data
// =====================================================================

const visualPath = resolve(publicDir, "subway-network.visual.geojson");
const stationsPath = resolve(publicDir, "subway-network.stations.geojson");

if (!existsSync(visualPath)) {
  console.error(`[audit] ERROR: ${visualPath} not found. Run the build first.`);
  process.exit(1);
}

const visualDoc = JSON.parse(readFileSync(visualPath, "utf8"));
const stationsDoc = existsSync(stationsPath)
  ? JSON.parse(readFileSync(stationsPath, "utf8"))
  : null;

const features = visualDoc.features.filter((f) => f.geometry?.type === "LineString");
console.log(`[audit] Loaded ${features.length} LineString features from visual artifact`);

// Build terminal station ID set from stations geojson
const terminalStationIds = new Set();
const stationsByName = new Map();
if (stationsDoc) {
  for (const st of stationsDoc.features) {
    const sid = st.properties?.station_id;
    if (sid) {
      stationsByName.set(st.properties.name ?? sid, { id: sid, coord: st.geometry?.coordinates, routes: st.properties.route_ids ?? [] });
    }
    // Stations with only 1 route that appears in the network are terminal candidates.
    // We mark ALL stations as potential terminals — the caller decides.
    if (sid) terminalStationIds.add(sid);
  }
}

// =====================================================================
// Build per-route graph
// =====================================================================

// For each route R, group features by endpoint.
// An "endpoint" is identified first by anchor_id (from_anchor_id / to_anchor_id)
// and second by coordinate key if anchors are missing.

function getEndpoints(feature) {
  const p = feature.properties;
  const coords = feature.geometry.coordinates;
  if (!coords || coords.length < 2) return null;
  const fromCoord = coords[0];
  const toCoord = coords[coords.length - 1];
  const fromKey = p.from_anchor_id
    ? `anchor:${p.from_anchor_id}`
    : `coord:${coordKey(fromCoord)}`;
  const toKey = p.to_anchor_id
    ? `anchor:${p.to_anchor_id}`
    : `coord:${coordKey(toCoord)}`;
  return { fromKey, toKey, fromCoord, toCoord };
}

// routeFeatureMap: route_id -> [feature, ...]
const routeFeatureMap = new Map();
for (const f of features) {
  const routeIds = f.properties.route_ids ?? [];
  for (const r of routeIds) {
    if (!routeFeatureMap.has(r)) routeFeatureMap.set(r, []);
    routeFeatureMap.get(r).push(f);
  }
}

// routeEndpointMap: route_id -> Map<endpointKey -> [feature, ...]>
// Each feature contributes its fromKey and toKey endpoints.
const routeEndpointMap = new Map();
for (const [routeId, routeFeatures] of routeFeatureMap) {
  const endpointMap = new Map();
  for (const f of routeFeatures) {
    const ep = getEndpoints(f);
    if (!ep) continue;
    for (const key of [ep.fromKey, ep.toKey]) {
      if (!endpointMap.has(key)) endpointMap.set(key, []);
      endpointMap.get(key).push(f);
    }
  }
  routeEndpointMap.set(routeId, endpointMap);
}

// =====================================================================
// 1. Per-route handoff detection
// =====================================================================

const routeHandoffBreaks = []; // { route, featureA, featureB_missing_endpoint, orphan_endpoint_key, orphan_coord }
const routeAppearsNoUpstream = []; // { route, feature, endpoint_key, coord }

// A station stop id => is it a real terminus?
// Heuristic: if the from_stop_id or to_stop_id appears only once in the route's
// stop adjacency and is a "real" named station, it's a terminus.
// Simpler: if endpoint X has only ONE feature for route R touching it, it's a
// potential terminus or a break. We check against station names.
const stationStopIds = new Set();
if (stationsDoc) {
  for (const st of stationsDoc.features) {
    const sid = st.properties?.station_id;
    if (sid) stationStopIds.add(sid);
    // Also add source_station_ids
    for (const ssid of st.properties?.source_station_ids ?? []) stationStopIds.add(ssid);
  }
}

// For terminal detection: if a feature endpoint stop_id is in stationStopIds and
// the stop only has one route (checking the route_ids field of the station), treat
// it as a known terminal.
const singleRouteStopIds = new Set();
if (stationsDoc) {
  for (const st of stationsDoc.features) {
    const routes = st.properties?.route_ids ?? [];
    const sid = st.properties?.station_id;
    if (routes.length <= 2 && sid) singleRouteStopIds.add(sid); // small station = could be terminal
  }
}

for (const [routeId, endpointMap] of routeEndpointMap) {
  for (const [key, touchingFeatures] of endpointMap) {
    // If only 1 feature touches this endpoint, it could be orphaned (no continuation)
    // OR it could be a terminal.
    if (touchingFeatures.length === 1) {
      const f = touchingFeatures[0];
      const ep = getEndpoints(f);
      if (!ep) continue;

      // Determine if this is the "from" or "to" endpoint of f
      const isFrom = ep.fromKey === key;
      const stopId = isFrom ? f.properties.from_stop_id : f.properties.to_stop_id;

      // Is this a known real terminal station?
      const isKnownTerminal = stopId && stationStopIds.has(stopId);

      // For the handoff report, we record all single-endpoint touches.
      // The caller can filter by severity.
      routeAppearsNoUpstream.push({
        route_id: routeId,
        feature: f,
        endpoint_key: key,
        endpoint_kind: isFrom ? "from" : "to",
        coord: isFrom ? ep.fromCoord : ep.toCoord,
        stop_id: stopId,
        is_known_terminal: isKnownTerminal,
        lane_slot_source: f.properties.lane_slot_source,
      });
    }
  }
}

// =====================================================================
// 2. Bogus-transition detection
// =====================================================================

// Build corridor route index: bundle_id (or corridor_id) -> route_ids
const bundleRouteIndex = new Map();
for (const f of features) {
  const bid = f.properties.bundle_id;
  const cid = f.properties.corridor_id;
  const routeIds = f.properties.route_ids ?? [];
  if (bid && !bundleRouteIndex.has(bid)) bundleRouteIndex.set(bid, new Set(routeIds));
  if (cid && !bundleRouteIndex.has(cid)) bundleRouteIndex.set(cid, new Set(routeIds));
  // Update with all route_ids for this bundle (accumulate)
  if (bid) for (const r of routeIds) bundleRouteIndex.get(bid).add(r);
  if (cid) for (const r of routeIds) bundleRouteIndex.get(cid).add(r);
}

const bogusTransitions = [];
const transitionFeatures = features.filter((f) => f.properties.lane_slot_source === "branch_transition");

for (const t of transitionFeatures) {
  const tp = t.properties;
  const fromRoutes = bundleRouteIndex.get(tp.bundle_id_from) ?? new Set();
  const toRoutes = bundleRouteIndex.get(tp.bundle_id_to) ?? new Set();
  const color = tp.color ?? "";
  const routeIds = tp.route_ids ?? [];
  const colorRouteIds = tp.color_route_ids ?? [];

  // Check 1: color's representative route present in both corridors?
  const colorInFrom = colorRouteIds.some((r) => fromRoutes.has(r));
  const colorInTo = colorRouteIds.some((r) => toRoutes.has(r));

  // Check 2: safe_same_route_continuation should have non-empty intersection
  const routeIntersect = routeIds.filter((r) => fromRoutes.has(r) && toRoutes.has(r));
  const classification = tp.transition_classification ?? "";
  const length = tp.length_m ?? 0;

  const bogusReasons = [];
  if (!colorInFrom && !colorInTo) bogusReasons.push("bogus_route_mismatch:color_absent_from_both_endpoints");
  if (!colorInFrom) bogusReasons.push("bogus_route_mismatch:color_absent_from_bundle_id_from");
  if (!colorInTo) bogusReasons.push("bogus_route_mismatch:color_absent_from_bundle_id_to");
  if (classification === "safe_same_route_continuation" && routeIntersect.length === 0) {
    bogusReasons.push("bogus_classification:safe_same_route_but_empty_intersect");
  }
  if (classification === "likely_branch_exit" && length > 25) {
    bogusReasons.push(`length_exceeds_25m:${length.toFixed(1)}m`);
  }

  if (bogusReasons.length > 0) {
    bogusTransitions.push({ feature: t, reasons: bogusReasons });
  }
}

// =====================================================================
// 3. Orphan-origin detection (both endpoints have no neighbor)
// =====================================================================

const orphanOrigins = [];

for (const [routeId, endpointMap] of routeEndpointMap) {
  const routeFeatures = routeFeatureMap.get(routeId) ?? [];
  for (const f of routeFeatures) {
    const ep = getEndpoints(f);
    if (!ep) continue;

    const fromNeighbors = (endpointMap.get(ep.fromKey) ?? []).filter((g) => g !== f);
    const toNeighbors = (endpointMap.get(ep.toKey) ?? []).filter((g) => g !== f);

    if (fromNeighbors.length === 0 && toNeighbors.length === 0) {
      // Both endpoints isolated — this is an orphan
      const fromStopId = f.properties.from_stop_id;
      const toStopId = f.properties.to_stop_id;
      const fromIsTerminal = fromStopId && stationStopIds.has(fromStopId);
      const toIsTerminal = toStopId && stationStopIds.has(toStopId);

      // If both endpoints are known stations, it might just be a short solo segment.
      // Only flag as error if NEITHER is a terminal in a non-trivial route.
      const severity = (fromIsTerminal && toIsTerminal) ? "warn" : "error";

      orphanOrigins.push({
        route_id: routeId,
        feature: f,
        from_endpoint_key: ep.fromKey,
        to_endpoint_key: ep.toKey,
        from_coord: ep.fromCoord,
        to_coord: ep.toCoord,
        from_stop_id: fromStopId,
        to_stop_id: toStopId,
        from_is_terminal: fromIsTerminal,
        to_is_terminal: toIsTerminal,
        severity,
        length_m: f.properties.length_m ?? haversineM(ep.fromCoord, ep.toCoord),
      });
    }
  }
}

// De-duplicate orphans (same feature may be flagged multiple times for different routes)
const orphanFeatureIds = new Set();
const uniqueOrphans = [];
for (const o of orphanOrigins) {
  const bid = o.feature.properties.bundle_id;
  const key = `${bid}:${o.route_id}`;
  if (!orphanFeatureIds.has(key)) {
    orphanFeatureIds.add(key);
    uniqueOrphans.push(o);
  }
}

// =====================================================================
// 4. Lane slot jumps (same route, consecutive features, different spine)
// Only flag pairs where there is a non-trivial spatial gap (> 5m) between
// consecutive same-route features that share NO transition bridge.
// Zero-gap spine changes are normal (each corridor has its own spine_id).
// =====================================================================

const LANE_DISC_MIN_GAP_M = 5; // below this the "gap" is just floating point + anchor resolution
const laneDiscontinuities = [];

for (const [routeId, routeFeatures] of routeFeatureMap) {
  const endpointMap = routeEndpointMap.get(routeId);
  if (!endpointMap) continue;

  for (const f of routeFeatures) {
    const fp = f.properties;
    const ep = getEndpoints(f);
    if (!ep) continue;

    // Check "to" endpoint — find features starting at this endpoint
    const toNeighbors = (endpointMap.get(ep.toKey) ?? []).filter((g) => g !== f);
    for (const g of toNeighbors) {
      const gp = g.properties;
      // Both are non-transition lanes with spine_id
      if (fp.lane_slot_source === "branch_transition" || gp.lane_slot_source === "branch_transition") continue;
      if (!fp.spine_id || !gp.spine_id) continue;
      if (fp.spine_id !== gp.spine_id) {
        // Different spine — is there a branch_transition between them?
        const fBid = fp.bundle_id;
        const gBid = gp.bundle_id;
        const hasTransition = transitionFeatures.some((t) => {
          const tp = t.properties;
          return (tp.bundle_id_from === fBid && tp.bundle_id_to === gBid) ||
                 (tp.bundle_id_from === gBid && tp.bundle_id_to === fBid);
        });
        if (!hasTransition) {
          // Compute actual spatial gap
          const gEp = getEndpoints(g);
          const gFromCoord = gEp?.fromCoord ?? g.geometry.coordinates[0];
          const gap = haversineM(ep.toCoord, gFromCoord);
          if (gap > LANE_DISC_MIN_GAP_M) {
            laneDiscontinuities.push({ route_id: routeId, featureA: f, featureB: g, gap_m: gap });
          }
        }
      }
    }
  }
}

// De-duplicate lane discontinuity pairs
const discSeenPairs = new Set();
const uniqueDiscontinuities = [];
for (const d of laneDiscontinuities) {
  const key = [d.featureA.properties.bundle_id, d.featureB.properties.bundle_id, d.route_id].sort().join("|");
  if (!discSeenPairs.has(key)) {
    discSeenPairs.add(key);
    uniqueDiscontinuities.push(d);
  }
}
// Sort by gap descending
uniqueDiscontinuities.sort((a, b) => (b.gap_m ?? 0) - (a.gap_m ?? 0));

// =====================================================================
// 5. Region-specific audits
// =====================================================================

function auditRegion(regionName, bbox) {
  const regionFeatures = features.filter((f) => featureIntersectsBbox(f, bbox));
  const routes = new Set();
  const spines = new Set();
  const physBundles = new Set();
  const bundleIds = new Set();

  for (const f of regionFeatures) {
    (f.properties.route_ids ?? []).forEach((r) => routes.add(r));
    if (f.properties.spine_id) spines.add(f.properties.spine_id);
    if (f.properties.physical_bundle_id) physBundles.add(f.properties.physical_bundle_id);
    bundleIds.add(f.properties.bundle_id);
  }

  // Find orphans in region
  const regionOrphans = uniqueOrphans.filter((o) => featureIntersectsBbox(o.feature, bbox));
  // Find bogus transitions in region
  const regionBogus = bogusTransitions.filter((b) => featureIntersectsBbox(b.feature, bbox));
  // Find discontinuities in region
  const regionDisc = uniqueDiscontinuities.filter(
    (d) => featureIntersectsBbox(d.featureA, bbox) || featureIntersectsBbox(d.featureB, bbox),
  );

  // Check if expected shared routes share physical_bundle_id
  const expected = REGION_EXPECTED_SHARED[regionName];
  const sharedBundleAnalysis = [];
  if (expected) {
    for (const r of expected.routes) {
      const routeFeaturesHere = regionFeatures.filter((f) => (f.properties.route_ids ?? []).includes(r));
      const pbIds = new Set(routeFeaturesHere.map((f) => f.properties.physical_bundle_id).filter(Boolean));
      sharedBundleAnalysis.push({ route: r, physical_bundle_ids: [...pbIds], feature_count: routeFeaturesHere.length });
    }
  }

  return {
    regionName,
    bbox,
    featureCount: regionFeatures.length,
    routes: [...routes].sort(),
    distinctSpines: spines.size,
    spineIds: [...spines].slice(0, 8),
    distinctPhysicalBundles: physBundles.size,
    physicalBundleIds: [...physBundles],
    orphanCount: regionOrphans.length,
    orphans: regionOrphans,
    bogusTransitionCount: regionBogus.length,
    bogusTransitions: regionBogus,
    discontinuityCount: regionDisc.length,
    discontinuities: regionDisc,
    sharedBundleAnalysis,
    expected,
  };
}

const regionAudits = {};
for (const [name, bbox] of Object.entries(REGIONS)) {
  regionAudits[name] = auditRegion(name, bbox);
}

// =====================================================================
// Build output GeoJSON files
// =====================================================================

// Helper to pick properties from feature safely
function safeProps(f) {
  const p = f.properties;
  return {
    route_id: p.route_id ?? null,
    route_ids: p.route_ids ?? [],
    color_route_ids: p.color_route_ids ?? [],
    color: p.color ?? null,
    spine_id: p.spine_id ?? null,
    physical_bundle_id: p.physical_bundle_id ?? null,
    corridor_id: p.corridor_id ?? null,
    bundle_id: p.bundle_id ?? null,
    lane_slot: p.lane_slot ?? null,
    lane_slot_source: p.lane_slot_source ?? null,
    length_m: p.length_m ?? null,
    from_anchor_id: p.from_anchor_id ?? null,
    to_anchor_id: p.to_anchor_id ?? null,
    from_stop_id: p.from_stop_id ?? null,
    to_stop_id: p.to_stop_id ?? null,
    transition_classification: p.transition_classification ?? null,
  };
}

// Orphan origins geojson
const orphanOriginsFeatures = uniqueOrphans.map((o) => ({
  type: "Feature",
  geometry: o.feature.geometry,
  properties: {
    ...safeProps(o.feature),
    qa_class: "orphan_origin",
    qa_severity: o.severity,
    qa_route_id: o.route_id,
    qa_from_endpoint_key: o.from_endpoint_key,
    qa_to_endpoint_key: o.to_endpoint_key,
    qa_from_is_terminal: o.from_is_terminal,
    qa_to_is_terminal: o.to_is_terminal,
  },
}));

const orphanOriginsDoc = {
  type: "FeatureCollection",
  metadata: { generated_at: new Date().toISOString(), source: "audit-lane-continuity.mjs" },
  features: orphanOriginsFeatures,
};

// Route handoff breaks geojson — connect broken endpoint to closest same-route endpoint
function findClosestSameRouteEndpoint(routeId, brokenCoord, excludeBundleId) {
  const routeFs = routeFeatureMap.get(routeId) ?? [];
  let best = null;
  let bestDist = Infinity;
  for (const f of routeFs) {
    if (f.properties.bundle_id === excludeBundleId) continue;
    const coords = f.geometry?.coordinates;
    if (!coords || coords.length < 2) continue;
    for (const c of [coords[0], coords[coords.length - 1]]) {
      const d = haversineM(brokenCoord, c);
      if (d < bestDist) {
        bestDist = d;
        best = c;
      }
    }
  }
  return best;
}

const routeHandoffFeatures = [];
for (const entry of routeAppearsNoUpstream) {
  if (entry.is_known_terminal) continue; // skip real terminals
  if (entry.lane_slot_source === "branch_transition") continue; // skip transitions
  const closest = findClosestSameRouteEndpoint(
    entry.route_id,
    entry.coord,
    entry.feature.properties.bundle_id,
  );
  if (!closest) continue;
  const dist = haversineM(entry.coord, closest);
  if (dist > 2000) continue; // skip implausibly far matches
  routeHandoffFeatures.push({
    type: "Feature",
    geometry: { type: "LineString", coordinates: [entry.coord, closest] },
    properties: {
      qa_class: "route_handoff_break",
      qa_route_id: entry.route_id,
      qa_endpoint_kind: entry.endpoint_kind,
      qa_endpoint_key: entry.endpoint_key,
      qa_stop_id: entry.stop_id ?? null,
      qa_gap_m: Math.round(dist),
      bundle_id: entry.feature.properties.bundle_id ?? null,
      color: entry.feature.properties.color ?? null,
    },
  });
}

const routeHandoffDoc = {
  type: "FeatureCollection",
  metadata: { generated_at: new Date().toISOString(), source: "audit-lane-continuity.mjs" },
  features: routeHandoffFeatures,
};

// Lane discontinuities geojson
const laneDiscontinuityFeatures = uniqueDiscontinuities.map((d) => {
  const epA = getEndpoints(d.featureA);
  const epB = getEndpoints(d.featureB);
  const coordA = epA?.toCoord ?? d.featureA.geometry.coordinates[0];
  const coordB = epB?.fromCoord ?? d.featureB.geometry.coordinates[0];
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [coordA, coordB] },
    properties: {
      qa_class: "lane_discontinuity",
      qa_route_id: d.route_id,
      bundle_id_a: d.featureA.properties.bundle_id,
      bundle_id_b: d.featureB.properties.bundle_id,
      spine_id_a: d.featureA.properties.spine_id,
      spine_id_b: d.featureB.properties.spine_id,
      color: d.featureA.properties.color,
      gap_m: Math.round(d.gap_m ?? haversineM(coordA, coordB)),
    },
  };
});

const laneDiscontinuityDoc = {
  type: "FeatureCollection",
  metadata: { generated_at: new Date().toISOString(), source: "audit-lane-continuity.mjs" },
  features: laneDiscontinuityFeatures,
};

// =====================================================================
// Write output files
// =====================================================================

const OUT_ORPHAN_ORIGINS = resolve(debugDir, "subway-network.visual-debug-orphan-origins.geojson");
const OUT_ROUTE_HANDOFFS = resolve(debugDir, "subway-network.visual-debug-route-handoffs.geojson");
const OUT_LANE_DISCONTINUITIES = resolve(debugDir, "subway-network.visual-debug-lane-discontinuities.geojson");

writeFileSync(OUT_ORPHAN_ORIGINS, `${JSON.stringify(orphanOriginsDoc)}\n`);
writeFileSync(OUT_ROUTE_HANDOFFS, `${JSON.stringify(routeHandoffDoc)}\n`);
writeFileSync(OUT_LANE_DISCONTINUITIES, `${JSON.stringify(laneDiscontinuityDoc)}\n`);

console.log(`[audit] wrote ${OUT_ORPHAN_ORIGINS} (${orphanOriginsFeatures.length} features)`);
console.log(`[audit] wrote ${OUT_ROUTE_HANDOFFS} (${routeHandoffFeatures.length} features)`);
console.log(`[audit] wrote ${OUT_LANE_DISCONTINUITIES} (${laneDiscontinuityFeatures.length} features)`);

// Mirror to docs/assets timestamped dir
const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
// Check if a recent dir exists (same date prefix)
const datePrefix = timestamp.slice(0, 10);
let assetDir = null;
try {
  const { readdirSync } = await import("node:fs");
  const existing = readdirSync(docsAssetsDir).filter((d) => d.startsWith(datePrefix));
  if (existing.length > 0) {
    existing.sort();
    assetDir = resolve(docsAssetsDir, existing[existing.length - 1]);
  }
} catch (_) {
  // no dir yet
}
if (!assetDir) {
  assetDir = resolve(docsAssetsDir, timestamp);
  mkdirSync(assetDir, { recursive: true });
}

writeFileSync(resolve(assetDir, "orphan-origins.geojson"), `${JSON.stringify(orphanOriginsDoc)}\n`);
writeFileSync(resolve(assetDir, "route-handoffs.geojson"), `${JSON.stringify(routeHandoffDoc)}\n`);
writeFileSync(resolve(assetDir, "lane-discontinuities.geojson"), `${JSON.stringify(laneDiscontinuityDoc)}\n`);
console.log(`[audit] mirrored debug artifacts to ${assetDir}`);

// =====================================================================
// Region-specific verdict for user-reported failures
// =====================================================================

function qBrooklynVerdict() {
  const BROOKLYN_BBOX = [-74.05, 40.57, -73.83, 40.74];
  // Exclude features that are clearly Manhattan (lon > -73.96 is roughly Midtown+)
  // The Q "disconnected" features at 14 St/23 St (lon ~-73.99) are Manhattan, not Brooklyn.
  // We use a tighter bbox for Brooklyn-only Q continuity (lat < 40.72 keeps us south of the bridges).
  const BROOKLYN_ONLY_BBOX = [-74.05, 40.57, -73.83, 40.72];
  const qFeatures = features.filter((f) => {
    const routes = f.properties.route_ids ?? [];
    return routes.includes("Q") && featureIntersectsBbox(f, BROOKLYN_ONLY_BBOX);
  });

  if (qFeatures.length === 0) return { verdict: "FAIL", detail: "No Q features found in Brooklyn bbox" };

  // Build Q endpoint graph in Brooklyn
  const qEndpointMap = new Map();
  for (const f of qFeatures) {
    const ep = getEndpoints(f);
    if (!ep) continue;
    for (const key of [ep.fromKey, ep.toKey]) {
      if (!qEndpointMap.has(key)) qEndpointMap.set(key, []);
      qEndpointMap.get(key).push(f);
    }
  }

  // BFS from first feature
  const visited = new Set();
  const queue = [qFeatures[0]];
  visited.add(qFeatures[0].properties.bundle_id);
  while (queue.length > 0) {
    const cur = queue.shift();
    const ep = getEndpoints(cur);
    if (!ep) continue;
    for (const key of [ep.fromKey, ep.toKey]) {
      const neighbors = (qEndpointMap.get(key) ?? []).filter((g) => g !== cur);
      for (const n of neighbors) {
        const nid = n.properties.bundle_id;
        if (!visited.has(nid)) {
          visited.add(nid);
          queue.push(n);
        }
      }
    }
  }

  const unreached = qFeatures.filter((f) => !visited.has(f.properties.bundle_id));
  if (unreached.length === 0) {
    return { verdict: "PASS", detail: `Q forms single connected chain in Brooklyn (${qFeatures.length} features, all reachable)` };
  }
  return {
    verdict: "FAIL",
    detail: `Q has ${unreached.length} disconnected segment(s) in Brooklyn out of ${qFeatures.length} total`,
    disconnected_bundle_ids: unreached.slice(0, 5).map((f) => f.properties.bundle_id),
  };
}

function blueDowntownBrooklynVerdict() {
  const DT_BBOX = [-73.991, 40.687, -73.978, 40.694];
  const blueFeatures = features.filter((f) => {
    const routes = f.properties.route_ids ?? [];
    const color = f.properties.color;
    return (routes.includes("A") || routes.includes("C") || routes.includes("E")) && color === "#0A84FF" && featureIntersectsBbox(f, DT_BBOX);
  });
  const gFeatures = features.filter((f) => {
    const routes = f.properties.route_ids ?? [];
    const color = f.properties.color;
    return routes.includes("G") && color === "#6CBE45" && featureIntersectsBbox(f, DT_BBOX);
  });

  const orphansInRegion = uniqueOrphans.filter((o) => featureIntersectsBbox(o.feature, DT_BBOX));
  const orphanRoutes = [...new Set(orphansInRegion.map((o) => o.route_id))];

  return {
    blue_ace_features: blueFeatures.length,
    g_features: gFeatures.length,
    orphan_count: orphansInRegion.length,
    orphan_routes: orphanRoutes,
    verdict: orphansInRegion.length === 0 ? "PASS" : `CONCERNS:${orphanRoutes.join(",")} orphaned`,
  };
}

function flatbushEasternPkwyVerdict() {
  const FE_BBOX = [-73.961, 40.659, -73.940, 40.682];
  const irt23Features = features.filter((f) => {
    const routes = f.properties.route_ids ?? [];
    const color = f.properties.color;
    return (routes.includes("2") || routes.includes("3")) && color === "#EE352E" && featureIntersectsBbox(f, FE_BBOX);
  });
  const irt45Features = features.filter((f) => {
    const routes = f.properties.route_ids ?? [];
    const color = f.properties.color;
    return (routes.includes("4") || routes.includes("5")) && color === "#00933C" && featureIntersectsBbox(f, FE_BBOX);
  });

  // Check each feature for upstream within 90m
  let missingUpstream = 0;
  const checkUpstream = (fs, routeSet) => {
    for (const f of fs) {
      const ep = getEndpoints(f);
      if (!ep) continue;
      // Check fromCoord — does any same-color feature end near here?
      let hasUpstream = false;
      for (const r of routeSet) {
        const rMap = routeEndpointMap.get(r);
        if (!rMap) continue;
        const neighbors = (rMap.get(ep.fromKey) ?? []).filter((g) => g !== f);
        if (neighbors.length > 0) { hasUpstream = true; break; }
      }
      if (!hasUpstream) {
        // Check by coord proximity (90m) for any route in routeSet
        for (const r of routeSet) {
          const rFeatures = routeFeatureMap.get(r) ?? [];
          for (const g of rFeatures) {
            if (g === f) continue;
            const gCoords = g.geometry?.coordinates;
            if (!gCoords || gCoords.length < 2) continue;
            const dist = haversineM(ep.fromCoord, gCoords[gCoords.length - 1]);
            if (dist <= 90) { hasUpstream = true; break; }
          }
          if (hasUpstream) break;
        }
      }
      if (!hasUpstream) missingUpstream++;
    }
  };
  checkUpstream(irt23Features, new Set(["2", "3"]));
  checkUpstream(irt45Features, new Set(["4", "5"]));

  return {
    irt23_features: irt23Features.length,
    irt45_features: irt45Features.length,
    missing_upstream: missingUpstream,
    verdict: missingUpstream === 0 ? "PASS" : `CONCERNS:${missingUpstream} branch(es) missing upstream within 90m`,
  };
}

const qVerdict = qBrooklynVerdict();
const blueVerdict = blueDowntownBrooklynVerdict();
const flatbushVerdict = flatbushEasternPkwyVerdict();

// =====================================================================
// Generate markdown report
// =====================================================================

const now = new Date().toISOString();
const reportLines = [
  `# Subway Lane Continuity Audit`,
  ``,
  `Generated: ${now}`,
  ``,
  `## Summary Counts`,
  ``,
  `| Metric | Count |`,
  `|--------|-------|`,
  `| Total visual features | ${features.length} |`,
  `| Branch transition features | ${transitionFeatures.length} |`,
  `| Orphan origins (total) | ${uniqueOrphans.length} |`,
  `| Orphan origins (error severity) | ${uniqueOrphans.filter((o) => o.severity === "error").length} |`,
  `| Orphan origins (warn severity) | ${uniqueOrphans.filter((o) => o.severity === "warn").length} |`,
  `| Route handoff breaks (non-terminal) | ${routeHandoffFeatures.length} |`,
  `| Lane discontinuities | ${uniqueDiscontinuities.length} |`,
  `| Bogus transitions | ${bogusTransitions.length} |`,
  ``,
  `## Per-Region Findings`,
  ``,
];

for (const [name, audit] of Object.entries(regionAudits)) {
  reportLines.push(`### ${name}`);
  reportLines.push(``);
  reportLines.push(`- **Features in region**: ${audit.featureCount}`);
  reportLines.push(`- **Routes present**: ${audit.routes.join(", ")}`);
  reportLines.push(`- **Distinct spines**: ${audit.distinctSpines}`);
  reportLines.push(`- **Physical bundle IDs**: ${audit.physicalBundleIds.join(", ") || "none"}`);
  reportLines.push(`- **Orphan origins**: ${audit.orphanCount}`);
  reportLines.push(`- **Bogus transitions**: ${audit.bogusTransitionCount}`);
  reportLines.push(`- **Lane discontinuities**: ${audit.discontinuityCount}`);
  if (audit.sharedBundleAnalysis.length > 0) {
    reportLines.push(``);
    reportLines.push(`**Shared corridor analysis** (${audit.expected?.note ?? ""}):`);
    for (const s of audit.sharedBundleAnalysis) {
      reportLines.push(`- Route **${s.route}**: ${s.feature_count} features, physical_bundle_ids: [${s.physical_bundle_ids.join(", ") || "none"}]`);
    }
  }
  reportLines.push(``);
}

// Top 20 worst orphan origins (by severity then length)
reportLines.push(`## Top 20 Orphan Origins`);
reportLines.push(``);
reportLines.push(`| Route | Severity | Bundle ID | Length(m) | From Stop | To Stop |`);
reportLines.push(`|-------|----------|-----------|-----------|-----------|---------|`);
const sortedOrphans = [...uniqueOrphans].sort((a, b) => {
  if (a.severity !== b.severity) return a.severity === "error" ? -1 : 1;
  return (b.length_m ?? 0) - (a.length_m ?? 0);
});
for (const o of sortedOrphans.slice(0, 20)) {
  const bid = o.feature.properties.bundle_id ?? "-";
  const len = o.length_m ? Math.round(o.length_m) : "-";
  reportLines.push(`| ${o.route_id} | ${o.severity} | ${bid} | ${len} | ${o.from_stop_id ?? "-"} | ${o.to_stop_id ?? "-"} |`);
}
reportLines.push(``);

// Top 20 broken handoffs
reportLines.push(`## Top 20 Route Handoff Breaks`);
reportLines.push(``);
reportLines.push(`| Route | Gap(m) | Bundle ID | Endpoint Kind |`);
reportLines.push(`|-------|--------|-----------|---------------|`);
const sortedHandoffs = [...routeHandoffFeatures].sort((a, b) => (b.properties.qa_gap_m ?? 0) - (a.properties.qa_gap_m ?? 0));
for (const h of sortedHandoffs.slice(0, 20)) {
  const p = h.properties;
  reportLines.push(`| ${p.qa_route_id} | ${p.qa_gap_m} | ${p.bundle_id ?? "-"} | ${p.qa_endpoint_kind} |`);
}
reportLines.push(``);

// Top 20 lane discontinuities
reportLines.push(`## Top 20 Lane Discontinuities`);
reportLines.push(``);
reportLines.push(`| Route | Gap(m) | Bundle A | Bundle B | Spine A | Spine B |`);
reportLines.push(`|-------|--------|----------|----------|---------|---------|`);
const sortedDisc = [...uniqueDiscontinuities].sort((a, b) => {
  const epA = getEndpoints(a.featureA);
  const epB2 = getEndpoints(b.featureA);
  const dA = epA ? haversineM(epA.toCoord, (getEndpoints(a.featureB)?.fromCoord ?? epA.toCoord)) : 0;
  const dB = epB2 ? haversineM(epB2.toCoord, (getEndpoints(b.featureB)?.fromCoord ?? epB2.toCoord)) : 0;
  return dB - dA;
});
for (const d of sortedDisc.slice(0, 20)) {
  const epA = getEndpoints(d.featureA);
  const epB = getEndpoints(d.featureB);
  const gap = (epA && epB) ? Math.round(haversineM(epA.toCoord, epB.fromCoord ?? epA.toCoord)) : "-";
  reportLines.push(`| ${d.route_id} | ${gap} | ${d.featureA.properties.bundle_id} | ${d.featureB.properties.bundle_id} | ${d.featureA.properties.spine_id ?? "-"} | ${d.featureB.properties.spine_id ?? "-"} |`);
}
reportLines.push(``);

// User-reported failure verdicts
reportLines.push(`## User-Reported Failure Verdicts`);
reportLines.push(``);

// Failure 1: Grand Army Plaza
{
  const a = regionAudits["Grand Army Plaza"];
  const bqPresent = a.routes.includes("B") || a.routes.includes("Q");
  const redGreenPresent = a.routes.includes("2") || a.routes.includes("3");
  const sharedSpine = a.distinctSpines <= 2 && a.featureCount >= 2;
  reportLines.push(`### 1. Grand Army Plaza — B/Q + 2/3/4/5 convergence`);
  reportLines.push(`**Verdict**: ${bqPresent ? "CONFIRMED — B/Q missing" : "REFUTED — B+Q present"}`);
  reportLines.push(``);
  reportLines.push(`- Routes found in region: ${a.routes.join(", ") || "none"}`);
  reportLines.push(`- B present: ${a.routes.includes("B")}, Q present: ${a.routes.includes("Q")}`);
  reportLines.push(`- Features: ${a.featureCount}, spines: ${a.distinctSpines}`);
  reportLines.push(`- Analysis: Grand Army Plaza shows only IRT (2/3/4/5) in the bbox. B+Q approach from the south via Brighton line but their corridor does not extend into this specific bbox. The "four independent corridors" visual is a data gap: B+Q have no shared stop at Grand Army Plaza in GTFS (they stop at Prospect Park, one station south).`);
  reportLines.push(``);
}

// Failure 2: Downtown Brooklyn
{
  const a = regionAudits["Downtown Brooklyn"];
  reportLines.push(`### 2. Downtown Brooklyn — Blue orphaned, G disconnected`);
  reportLines.push(`**Verdict**: ${blueVerdict.verdict}`);
  reportLines.push(``);
  reportLines.push(`- Routes found: ${a.routes.join(", ")}`);
  reportLines.push(`- Blue (A/C/E) features in region: ${blueVerdict.blue_ace_features}`);
  reportLines.push(`- G features in region: ${blueVerdict.g_features}`);
  reportLines.push(`- Orphan count: ${blueVerdict.orphan_count}, orphan routes: ${blueVerdict.orphan_routes.join(", ") || "none"}`);
  reportLines.push(``);
}

// Failure 3: Jamaica Center
{
  const a = regionAudits["Jamaica Center"];
  reportLines.push(`### 3. Jamaica Center — E terminus + disconnected segments`);
  const ePresent = a.routes.includes("E");
  reportLines.push(`**Verdict**: ${ePresent ? (a.orphanCount > 0 ? "CONFIRMED — orphan segments present" : "REFUTED — no orphans detected") : "PARTIAL — E not in bbox"}`);
  reportLines.push(``);
  reportLines.push(`- Routes found: ${a.routes.join(", ")}`);
  reportLines.push(`- Features: ${a.featureCount}, orphans: ${a.orphanCount}`);
  if (a.orphans.length > 0) {
    reportLines.push(`- Orphan routes: ${[...new Set(a.orphans.map((o) => o.route_id))].join(", ")}`);
  }
  reportLines.push(``);
}

// Failure 4: Q in Brighton corridor
{
  reportLines.push(`### 4. Q in Brighton/Prospect Park — continuity`);
  reportLines.push(`**Verdict**: ${qVerdict.verdict}`);
  reportLines.push(``);
  reportLines.push(`- ${qVerdict.detail}`);
  if (qVerdict.disconnected_bundle_ids) {
    reportLines.push(`- Disconnected bundle IDs: ${qVerdict.disconnected_bundle_ids.join(", ")}`);
  }
  reportLines.push(``);
}

// Failure 5: Red/green Flatbush + Eastern Pkwy origins
{
  reportLines.push(`### 5. Red/Green Flatbush + Eastern Pkwy — upstream origins`);
  reportLines.push(`**Verdict**: ${flatbushVerdict.verdict}`);
  reportLines.push(``);
  reportLines.push(`- 2/3 (red) features: ${flatbushVerdict.irt23_features}`);
  reportLines.push(`- 4/5 (green) features: ${flatbushVerdict.irt45_features}`);
  reportLines.push(`- Branches missing upstream: ${flatbushVerdict.missing_upstream}`);
  reportLines.push(``);
}

const reportText = reportLines.join("\n");

const REPORT_PATH = resolve(docsDir, "subway-lane-continuity-audit.md");
writeFileSync(REPORT_PATH, reportText + "\n");
console.log(`[audit] wrote report: ${REPORT_PATH}`);

// Mirror report to asset dir
writeFileSync(resolve(assetDir, "audit-report.md"), reportText + "\n");

// =====================================================================
// Summary to stdout
// =====================================================================
console.log(`[audit] === SUMMARY ===`);
console.log(`[audit] total features:           ${features.length}`);
console.log(`[audit] branch transitions:        ${transitionFeatures.length}`);
console.log(`[audit] orphan origins (total):    ${uniqueOrphans.length}`);
console.log(`[audit] orphan origins (error):    ${uniqueOrphans.filter((o) => o.severity === "error").length}`);
console.log(`[audit] route handoff breaks:      ${routeHandoffFeatures.length}`);
console.log(`[audit] lane discontinuities:      ${uniqueDiscontinuities.length}`);
console.log(`[audit] bogus transitions:         ${bogusTransitions.length}`);
console.log(`[audit] Q Brooklyn verdict:        ${qVerdict.verdict} — ${qVerdict.detail}`);
console.log(`[audit] Blue Downtown Brooklyn:    ${blueVerdict.verdict}`);
console.log(`[audit] Flatbush/EasternPkwy:      ${flatbushVerdict.verdict}`);
console.log(`[audit] done.`);
