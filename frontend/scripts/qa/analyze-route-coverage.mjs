/**
 * analyze-route-coverage.mjs
 *
 * Pure analysis script (no Playwright) for programmatic visual.geojson checks.
 * Reads subway-network.visual.geojson + subway-network.stations.geojson.
 * Outputs:
 *   frontend/artifacts/debug/subway-network.visual-debug-route-qa.json
 *   frontend/artifacts/debug/subway-network.visual-debug-nonparallel.geojson
 *
 * Usage:
 *   node frontend/scripts/qa/analyze-route-coverage.mjs
 */

import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..", "..");
const publicDir = resolve(frontendRoot, "public");
const debugDir = resolve(frontendRoot, "artifacts", "debug");
mkdirSync(debugDir, { recursive: true });

// ---------------------------------------------------------------------------
// Load GeoJSON
// ---------------------------------------------------------------------------
console.log("[analyze] loading visual.geojson...");
const visualGJ = JSON.parse(
  readFileSync(resolve(publicDir, "subway-network.visual.geojson"), "utf8")
);
const stationsGJ = JSON.parse(
  readFileSync(resolve(publicDir, "subway-network.stations.geojson"), "utf8")
);

const visualFeatures = visualGJ.features;
console.log("[analyze] visual features:", visualFeatures.length);
console.log("[analyze] station features:", stationsGJ.features.length);

// ---------------------------------------------------------------------------
// Haversine
// ---------------------------------------------------------------------------
const DEG2RAD = Math.PI / 180;
function haversineM(lon1, lat1, lon2, lat2) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * DEG2RAD;
  const dLon = (lon2 - lon1) * DEG2RAD;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * DEG2RAD) * Math.cos(lat2 * DEG2RAD) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function lineStringLengthM(coords) {
  let len = 0;
  for (let i = 1; i < coords.length; i++) {
    len += haversineM(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]);
  }
  return len;
}

function lineStringCentroid(coords) {
  let sumLon = 0, sumLat = 0;
  for (const [lon, lat] of coords) { sumLon += lon; sumLat += lat; }
  return [sumLon / coords.length, sumLat / coords.length];
}

// ---------------------------------------------------------------------------
// Point-in-bbox helper
// ---------------------------------------------------------------------------
function coordInBbox(lon, lat, bbox) {
  // bbox: [minLon, minLat, maxLon, maxLat]
  return lon >= bbox[0] && lon <= bbox[2] && lat >= bbox[1] && lat <= bbox[3];
}

function featureIntersectsBbox(coords, bbox) {
  return coords.some(([lon, lat]) => coordInBbox(lon, lat, bbox));
}

// ---------------------------------------------------------------------------
// All routes we care about
// ---------------------------------------------------------------------------
const ALL_ROUTES = [
  "1","2","3","4","5","6","6X","7","7X",
  "A","B","C","D","E","F","FX","G","J","Z",
  "L","M","N","Q","R","W",
  "FS","H","SI"
];

// ---------------------------------------------------------------------------
// Per-route stats
// ---------------------------------------------------------------------------
const routeStats = {};
for (const r of ALL_ROUTES) {
  routeStats[r] = {
    route: r,
    feature_count: 0,
    total_length_m: 0,
    bbox: [Infinity, Infinity, -Infinity, -Infinity], // minLon, minLat, maxLon, maxLat
    features: [], // store feature indices for parallelism analysis
  };
}

for (let fi = 0; fi < visualFeatures.length; fi++) {
  const feat = visualFeatures[fi];
  const props = feat.properties;
  const routeIds = props.route_ids || [];
  const coords = feat.geometry.coordinates;
  const len = lineStringLengthM(coords);

  for (const r of routeIds) {
    if (!routeStats[r]) {
      // Unknown route -- create entry
      routeStats[r] = {
        route: r,
        feature_count: 0,
        total_length_m: 0,
        bbox: [Infinity, Infinity, -Infinity, -Infinity],
        features: [],
      };
    }
    const rs = routeStats[r];
    rs.feature_count++;
    rs.total_length_m += len;
    rs.features.push(fi);
    for (const [lon, lat] of coords) {
      if (lon < rs.bbox[0]) rs.bbox[0] = lon;
      if (lat < rs.bbox[1]) rs.bbox[1] = lat;
      if (lon > rs.bbox[2]) rs.bbox[2] = lon;
      if (lat > rs.bbox[3]) rs.bbox[3] = lat;
    }
  }
}

// ---------------------------------------------------------------------------
// Q-specific Brooklyn analysis
// ---------------------------------------------------------------------------
const BROOKLYN_BBOX = [-74.05, 40.57, -73.83, 40.74];
const SOUTH_OF_PROSPECT_PARK_LAT = 40.66;
const BRIGHTON_BBOX = [-73.97, 40.57, -73.95, 40.66];

let qFeaturesInBrooklyn = 0;
let qFeaturesSouthOfProspectPark = 0;
let qFeaturesInBrightonCorridor = 0;

for (const feat of visualFeatures) {
  const props = feat.properties;
  if (!props.route_ids || !props.route_ids.includes("Q")) continue;
  const coords = feat.geometry.coordinates;

  const inBrooklyn = featureIntersectsBbox(coords, BROOKLYN_BBOX);
  if (inBrooklyn) qFeaturesInBrooklyn++;

  const southOfProspect = coords.some(([, lat]) => lat < SOUTH_OF_PROSPECT_PARK_LAT);
  if (southOfProspect) qFeaturesSouthOfProspectPark++;

  const inBrighton = featureIntersectsBbox(coords, BRIGHTON_BBOX);
  if (inBrighton) qFeaturesInBrightonCorridor++;
}

console.log("[analyze] Q features in Brooklyn bbox:", qFeaturesInBrooklyn);
console.log("[analyze] Q features south of Prospect Park:", qFeaturesSouthOfProspectPark);
console.log("[analyze] Q features in Brighton corridor:", qFeaturesInBrightonCorridor);

// ---------------------------------------------------------------------------
// Per-route flags
// ---------------------------------------------------------------------------
const ROUTE_FLAGS = {};
const ROUTE_LENGTH_THRESHOLD_M = 500; // flag if < 500m total
for (const [r, rs] of Object.entries(routeStats)) {
  const flags = [];
  if (rs.feature_count === 0) {
    flags.push("route_missing");
  } else if (rs.total_length_m < ROUTE_LENGTH_THRESHOLD_M) {
    flags.push("route_length_suspiciously_low");
  }
  if (r === "Q" && qFeaturesInBrooklyn === 0) {
    flags.push("q_missing_brooklyn");
  }
  ROUTE_FLAGS[r] = flags;
}

// ---------------------------------------------------------------------------
// Expected-parallel corridor analysis
// ---------------------------------------------------------------------------
const EXPECTED_PARALLEL = [
  { name: "1/2/3 Manhattan",               routes: ["1","2","3"],           bbox: [-74.01, 40.70, -73.96, 40.85] },
  { name: "4/5/6 Lex",                     routes: ["4","5","6"],           bbox: [-74.00, 40.70, -73.85, 40.85] },
  { name: "A/C/E 8th Av",                  routes: ["A","C","E"],           bbox: [-74.01, 40.70, -73.97, 40.81] },
  { name: "B/D/F/M 6th Av",               routes: ["B","D","F","M"],       bbox: [-74.01, 40.70, -73.96, 40.82] },
  { name: "N/Q/R/W Broadway",             routes: ["N","Q","R","W"],       bbox: [-74.02, 40.70, -73.96, 40.78] },
  { name: "B/Q Brighton",                  routes: ["B","Q"],               bbox: [-73.97, 40.57, -73.95, 40.69] },
  { name: "F/G Culver",                    routes: ["F","G"],               bbox: [-74.00, 40.64, -73.96, 40.70] },
  { name: "2/5 Bronx",                     routes: ["2","5"],               bbox: [-73.93, 40.83, -73.83, 40.92] },
  { name: "2/3/4/5 Atlantic-Eastern",     routes: ["2","3","4","5"],       bbox: [-73.98, 40.66, -73.93, 40.70] },
  { name: "B/D/N/Q/R DeKalb-MB",         routes: ["B","D","N","Q","R","W"], bbox: [-73.99, 40.68, -73.97, 40.72] },
  { name: "J/Z Broadway BK",              routes: ["J","Z"],               bbox: [-73.95, 40.69, -73.78, 40.72] },
  { name: "7/7X Queens",                  routes: ["7"],                   bbox: [-73.94, 40.74, -73.82, 40.77] },
  { name: "A Rockaways",                  routes: ["A","H"],               bbox: [-73.86, 40.58, -73.74, 40.63] },
];

// For each EXPECTED_PARALLEL group:
// - Find features in bbox whose route_ids intersect routes
// - Group by physical_bundle_id ?? spine_id
// - If multiple distinct bundle/spine IDs => non_parallel_shared_direction finding
// - Compute average centroid distance between distinct bundles

function featureGroupKey(props) {
  return props.physical_bundle_id ?? props.spine_id ?? props.bundle_id ?? "unknown";
}

const parallelFindings = [];
const nonParallelFeatures = []; // GeoJSON features for the debug artifact

for (const group of EXPECTED_PARALLEL) {
  // Find all features in bbox whose route_ids intersect group.routes
  const matchingFeatures = visualFeatures.filter((feat) => {
    const props = feat.properties;
    const routeIds = props.route_ids || [];
    const inBbox = featureIntersectsBbox(feat.geometry.coordinates, group.bbox);
    if (!inBbox) return false;
    return group.routes.some((r) => routeIds.includes(r));
  });

  if (matchingFeatures.length === 0) {
    parallelFindings.push({
      group: group.name,
      routes: group.routes,
      status: "FAIL",
      reason: "no_features_in_region",
      feature_count: 0,
      distinct_bundles: 0,
      findings: [],
    });
    continue;
  }

  // Group by bundle key
  const bundleMap = new Map(); // bundleKey => { coords[], routes: Set, features[] }
  for (const feat of matchingFeatures) {
    const key = featureGroupKey(feat.properties);
    if (!bundleMap.has(key)) {
      bundleMap.set(key, { key, coords: [], routes: new Set(), features: [] });
    }
    const entry = bundleMap.get(key);
    for (const c of feat.geometry.coordinates) entry.coords.push(c);
    for (const r of (feat.properties.route_ids || [])) entry.routes.add(r);
    entry.features.push(feat);
  }

  const bundles = [...bundleMap.values()];

  // Compute centroid per bundle
  for (const b of bundles) {
    b.centroid = lineStringCentroid(b.coords);
    b.total_length_m = b.features.reduce(
      (sum, f) => sum + lineStringLengthM(f.geometry.coordinates),
      0
    );
  }

  // Pairwise: find distinct bundles with overlapping routes that are nearby
  const pairFindings = [];
  if (bundles.length >= 2) {
    for (let i = 0; i < bundles.length; i++) {
      for (let j = i + 1; j < bundles.length; j++) {
        const a = bundles[i];
        const b = bundles[j];

        // Check if they share any of the expected routes
        const aRoutes = [...a.routes].filter((r) => group.routes.includes(r));
        const bRoutes = [...b.routes].filter((r) => group.routes.includes(r));
        if (aRoutes.length === 0 || bRoutes.length === 0) continue;

        // Compute pairwise centroid distance
        const distM = haversineM(a.centroid[0], a.centroid[1], b.centroid[0], b.centroid[1]);

        // Compute nearest-point avg distance between polylines (sample from a to b)
        // Sample up to 30 points from a, find nearest in b
        const sampleA = a.coords.filter((_, i2) => i2 % Math.max(1, Math.floor(a.coords.length / 30)) === 0);
        let minDistSum = 0;
        let minDistCount = 0;
        for (const ptA of sampleA) {
          let nearest = Infinity;
          for (const ptB of b.coords) {
            const d = haversineM(ptA[0], ptA[1], ptB[0], ptB[1]);
            if (d < nearest) nearest = d;
          }
          minDistSum += nearest;
          minDistCount++;
        }
        const avgNearestDistM = minDistCount > 0 ? minDistSum / minDistCount : Infinity;

        // Flag if distinct bundle IDs are both large and close
        const isNonParallel = a.total_length_m > 100 && b.total_length_m > 100 && avgNearestDistM >= 250;
        pairFindings.push({
          bundle_id_a: a.key,
          bundle_id_b: b.key,
          routes_a: aRoutes.sort(),
          routes_b: bRoutes.sort(),
          region: group.name,
          centroid_dist_m: Math.round(distM),
          avg_nearest_dist_m: Math.round(avgNearestDistM),
          length_a_m: Math.round(a.total_length_m),
          length_b_m: Math.round(b.total_length_m),
          non_parallel: isNonParallel,
        });

        if (isNonParallel) {
          // Add a LineString for the debug geojson
          nonParallelFeatures.push({
            type: "Feature",
            geometry: {
              type: "LineString",
              coordinates: [a.centroid, b.centroid],
            },
            properties: {
              bundle_id_a: a.key,
              bundle_id_b: b.key,
              routes_a: aRoutes.join(","),
              routes_b: bRoutes.join(","),
              region: group.name,
              centroid_dist_m: Math.round(distM),
              avg_nearest_dist_m: Math.round(avgNearestDistM),
              finding_type: "non_parallel_shared_direction",
            },
          });
        }
      }
    }
  }

  // Determine overall status for this group
  const hasAllRoutes = group.routes.every((r) =>
    matchingFeatures.some((f) => (f.properties.route_ids || []).includes(r))
  );
  const nonParallelCount = pairFindings.filter((p) => p.non_parallel).length;

  let status = "PASS";
  if (!hasAllRoutes) status = "FAIL";
  else if (nonParallelCount > 0) status = "WARN";

  parallelFindings.push({
    group: group.name,
    routes: group.routes,
    status,
    has_all_routes: hasAllRoutes,
    feature_count: matchingFeatures.length,
    distinct_bundles: bundles.length,
    non_parallel_pair_count: nonParallelCount,
    findings: pairFindings,
  });
}

// ---------------------------------------------------------------------------
// Summarize per-route stats (clean up internal fields)
// ---------------------------------------------------------------------------
const routeStatsSummary = {};
for (const [r, rs] of Object.entries(routeStats)) {
  routeStatsSummary[r] = {
    route: r,
    feature_count: rs.feature_count,
    total_length_m: Math.round(rs.total_length_m),
    bbox: rs.bbox.map((v) => Math.round(v * 10000) / 10000),
    flags: ROUTE_FLAGS[r] || [],
    status:
      rs.feature_count === 0
        ? "FAIL"
        : rs.total_length_m < ROUTE_LENGTH_THRESHOLD_M
        ? "WARN"
        : "PASS",
  };
}

// Override Q status based on Brooklyn check
if (routeStatsSummary["Q"]) {
  if (qFeaturesInBrooklyn === 0) {
    routeStatsSummary["Q"].status = "FAIL";
    routeStatsSummary["Q"].flags.push("q_missing_brooklyn");
  }
}

// ---------------------------------------------------------------------------
// Top 20 non-parallel findings globally
// ---------------------------------------------------------------------------
const allPairFindings = parallelFindings
  .flatMap((g) => g.findings || [])
  .filter((f) => f.non_parallel)
  .sort((a, b) => b.avg_nearest_dist_m - a.avg_nearest_dist_m)
  .slice(0, 20);

// ---------------------------------------------------------------------------
// Print summary
// ---------------------------------------------------------------------------
console.log("\n[analyze] === PER-ROUTE SUMMARY ===");
for (const r of ALL_ROUTES) {
  const rs = routeStatsSummary[r];
  if (!rs) {
    console.log(`  ${r.padEnd(4)} MISSING from stats`);
    continue;
  }
  const flagStr = rs.flags.length > 0 ? "  FLAGS: " + rs.flags.join(", ") : "";
  console.log(
    `  ${r.padEnd(4)} ${rs.status.padEnd(5)}  features=${String(rs.feature_count).padEnd(4)}  length=${String(rs.total_length_m).padEnd(8)}m${flagStr}`
  );
}

console.log("\n[analyze] === Q BROOKLYN SPECIFIC ===");
console.log("  Q features in Brooklyn bbox:", qFeaturesInBrooklyn);
console.log("  Q features south of Prospect Park:", qFeaturesSouthOfProspectPark);
console.log("  Q features in Brighton corridor:", qFeaturesInBrightonCorridor);
const qVerdict = qFeaturesInBrooklyn > 5 && qFeaturesInBrightonCorridor > 0 ? "PASS" : qFeaturesInBrooklyn > 0 ? "WARN" : "FAIL";
console.log("  Q Brooklyn verdict:", qVerdict);

console.log("\n[analyze] === PARALLELISM FINDINGS ===");
for (const pf of parallelFindings) {
  console.log(`  ${pf.group.padEnd(35)} ${pf.status.padEnd(5)}  bundles=${pf.distinct_bundles}  non_parallel_pairs=${pf.non_parallel_pair_count ?? 0}`);
}

console.log("\n[analyze] === TOP 20 NON-PARALLEL PAIR FINDINGS ===");
for (let i = 0; i < allPairFindings.length; i++) {
  const f = allPairFindings[i];
  console.log(
    `  ${String(i + 1).padEnd(3)} region=${f.region.padEnd(35)} avg_dist=${String(f.avg_nearest_dist_m).padEnd(6)}m  bundles: ${f.bundle_id_a} vs ${f.bundle_id_b}`
  );
}

// ---------------------------------------------------------------------------
// Build final QA report object
// ---------------------------------------------------------------------------
const qaReport = {
  generated_at: new Date().toISOString(),
  visual_feature_count: visualFeatures.length,
  station_count: stationsGJ.features.length,
  per_route: routeStatsSummary,
  q_brooklyn: {
    q_features_in_brooklyn: qFeaturesInBrooklyn,
    q_features_south_of_prospect_park: qFeaturesSouthOfProspectPark,
    q_features_in_brighton_corridor: qFeaturesInBrightonCorridor,
    brooklyn_bbox: BROOKLYN_BBOX,
    verdict: qVerdict,
  },
  parallelism: parallelFindings.map((pf) => ({
    group: pf.group,
    routes: pf.routes,
    status: pf.status,
    has_all_routes: pf.has_all_routes,
    feature_count: pf.feature_count,
    distinct_bundles: pf.distinct_bundles,
    non_parallel_pair_count: pf.non_parallel_pair_count ?? 0,
    top_findings: (pf.findings || []).filter((f) => f.non_parallel).slice(0, 5),
  })),
  top_nonparallel_findings: allPairFindings,
};

// ---------------------------------------------------------------------------
// Write outputs
// ---------------------------------------------------------------------------
const qaReportPath = resolve(debugDir, "subway-network.visual-debug-route-qa.json");
writeFileSync(qaReportPath, JSON.stringify(qaReport, null, 2));
console.log("\n[analyze] wrote", qaReportPath);

const nonParallelGJ = {
  type: "FeatureCollection",
  features: nonParallelFeatures,
};
const nonParallelPath = resolve(debugDir, "subway-network.visual-debug-nonparallel.geojson");
writeFileSync(nonParallelPath, JSON.stringify(nonParallelGJ, null, 2));
console.log("[analyze] wrote", nonParallelPath, "(" + nonParallelFeatures.length + " non-parallel findings)");
console.log("[analyze] DONE");
