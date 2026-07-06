/**
 * screenshot-real-app-routes.mjs
 *
 * Playwright-driven route-by-route visual QA for SmartRoute subway layer.
 * Uses window.__smartRouteMap (exposed by smart-route-map.tsx when ?qa-map=1 in dev).
 *
 * Usage:
 *   node frontend/scripts/qa/screenshot-real-app-routes.mjs
 *
 * Requires: dev server running at http://localhost:3000/
 */

import { chromium } from "playwright";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdirSync, writeFileSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..", "..");

// e.g. "2026-05-23T19-45-00"
const TIMESTAMP = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
const OUT_DIR = resolve(
  frontendRoot,
  "..",
  "docs",
  "assets",
  "subway-real-app-route-qa",
  TIMESTAMP
);

mkdirSync(OUT_DIR, { recursive: true });
console.log("[qa-routes] output dir:", OUT_DIR);

// ---------------------------------------------------------------------------
// SCENES
// Deduped by name. Each scene: { name, lng, lat, zoom, notes? }
// Covers all 28 NYC subway routes: 1,2,3,4,5,6,6X,7,7X,A,B,C,D,E,F,FX,G,J,Z,L,M,N,Q,R,W,S(FS),H,SI
// ---------------------------------------------------------------------------
const SCENES_RAW = [
  // --- Q Brooklyn (flagship concern) ---
  { name: "q-broadway-canal",            lng: -74.0001, lat: 40.7193, zoom: 15, notes: "Q/N/R/W Broadway at Canal St, Manhattan" },
  { name: "q-manhattan-bridge-dekalb",   lng: -73.9849, lat: 40.6905, zoom: 15, notes: "Manhattan Bridge approach to DeKalb Av" },
  { name: "q-atlantic-barclays",         lng: -73.9772, lat: 40.6843, zoom: 15, notes: "Atlantic Av-Barclays Center -- Q/B/D/N/R/2/3/4/5" },
  { name: "q-prospect-park",             lng: -73.9620, lat: 40.6618, zoom: 15, notes: "Prospect Park station, Brighton Line" },
  { name: "q-brighton-corridor",         lng: -73.9612, lat: 40.6090, zoom: 14, notes: "Brighton Line mid-corridor B/Q" },
  { name: "q-brighton-beach-coney",      lng: -73.9586, lat: 40.5774, zoom: 14, notes: "Brighton Beach / Coney Island terminal" },

  // --- Trunk parallelism ---
  { name: "irt-123-trunk-manhattan",     lng: -73.9650, lat: 40.7975, zoom: 14, notes: "1/2/3 Upper Manhattan trunk" },
  { name: "irt-456-trunk-lex",           lng: -73.9680, lat: 40.7580, zoom: 14, notes: "4/5/6 Lexington trunk Midtown" },
  { name: "ind-ace-8av",                 lng: -73.9956, lat: 40.7560, zoom: 14, notes: "A/C/E 8th Av Midtown" },
  { name: "bmt-bdfm-6av",               lng: -73.9871, lat: 40.7574, zoom: 14, notes: "B/D/F/M 6th Av Midtown" },
  { name: "bmt-nqrw-broadway",          lng: -73.9871, lat: 40.7569, zoom: 14, notes: "N/Q/R/W Broadway Midtown" },
  { name: "fg-culver",                   lng: -73.9763, lat: 40.6510, zoom: 14, notes: "F/G Culver Line Brooklyn" },
  { name: "irt-25-bronx",               lng: -73.8730, lat: 40.8650, zoom: 13, notes: "2/5 Bronx Jerome/White Plains Rd" },
  { name: "atlantic-eastern-pkwy-23-45", lng: -73.9560, lat: 40.6730, zoom: 14, notes: "2/3/4/5 Atlantic + Eastern Pkwy" },
  { name: "dekalb-bdnqrw",              lng: -73.9817, lat: 40.6905, zoom: 15, notes: "B/D/N/Q/R/W at DeKalb / Manhattan Bridge" },
  { name: "jz-broadway-brooklyn",       lng: -73.9425, lat: 40.6976, zoom: 14, notes: "J/Z Broadway Brooklyn" },
  { name: "seven-queens",               lng: -73.9180, lat: 40.7470, zoom: 13, notes: "7/7X Queens LIC to Flushing" },
  { name: "a-rockaways",                lng: -73.8160, lat: 40.6010, zoom: 12, notes: "A Far Rockaway / Rockaway Park branches" },
  { name: "a-lefferts",                 lng: -73.8330, lat: 40.6845, zoom: 14, notes: "A Ozone Park-Lefferts Blvd branch" },

  // --- Terminals ---
  { name: "terminal-coney-island-stillwell", lng: -73.9586, lat: 40.5774, zoom: 15, notes: "Coney Island-Stillwell Av terminal (F/D/N/Q)" },
  { name: "terminal-far-rockaway",       lng: -73.7556, lat: 40.6035, zoom: 14, notes: "Far Rockaway-Mott Av terminal (A)" },
  { name: "terminal-inwood-207",         lng: -74.0190, lat: 40.8680, zoom: 15, notes: "Inwood-207 St terminal (A/1)" },
  { name: "terminal-jamaica-center",     lng: -73.8000, lat: 40.7028, zoom: 14, notes: "Jamaica Center terminal (E/J/Z)" },
  { name: "terminal-pelham-bay-park",    lng: -73.8281, lat: 40.8527, zoom: 14, notes: "Pelham Bay Park terminal (6)" },
  { name: "terminal-wakefield-241",      lng: -73.8403, lat: 40.9032, zoom: 14, notes: "Wakefield-241 St terminal (2/5)" },
  { name: "terminal-flushing-main",      lng: -73.8298, lat: 40.7596, zoom: 14, notes: "Flushing-Main St terminal (7/7X)" },
  { name: "terminal-astoria-ditmars",    lng: -73.9120, lat: 40.7750, zoom: 14, notes: "Astoria-Ditmars Blvd terminal (N/W)" },
  { name: "terminal-forest-hills-71",    lng: -73.8460, lat: 40.7220, zoom: 14, notes: "Forest Hills-71 Av terminal (E/F/M/R)" },
  { name: "terminal-wtc-e",             lng: -74.0096, lat: 40.7126, zoom: 15, notes: "World Trade Center terminal (E)" },

  // --- Junctions / interchanges ---
  { name: "times-sq-42",                lng: -73.9858, lat: 40.7560, zoom: 15, notes: "Times Sq 42 St -- 1/2/3/7/N/Q/R/W/S" },
  { name: "herald-sq-34",               lng: -73.9881, lat: 40.7484, zoom: 15, notes: "34 St Herald Sq -- B/D/F/M/N/Q/R/W" },
  { name: "union-sq-14",                lng: -73.9912, lat: 40.7350, zoom: 15, notes: "14 St Union Sq -- 4/5/6/L/N/Q/R/W" },
  { name: "chambers-st-irt-bmt",        lng: -74.0050, lat: 40.7138, zoom: 15, notes: "Chambers St -- IRT (1/2/3) + BMT (J/Z/A/C/E)" },
  { name: "downtown-brooklyn-jay-st",   lng: -73.9877, lat: 40.6926, zoom: 14, notes: "Downtown Brooklyn / Jay St-Metrotech" },

  // --- L train ---
  { name: "l-train-canarsie",           lng: -73.9023, lat: 40.6463, zoom: 13, notes: "L Canarsie Line outer Brooklyn" },
  { name: "l-train-8av-brooklyn-junction", lng: -73.9444, lat: 40.7062, zoom: 14, notes: "L 8 Av end + Bedford/Graham junction" },

  // --- Staten Island Railway ---
  { name: "si-staten-island-trunk",     lng: -74.1170, lat: 40.6240, zoom: 12, notes: "SI Railway trunk" },
  { name: "si-st-george",               lng: -74.0738, lat: 40.6435, zoom: 14, notes: "St George terminal (SI)" },

  // --- Shuttles ---
  { name: "shuttle-franklin-fs",        lng: -73.9580, lat: 40.6700, zoom: 15, notes: "Franklin Av Shuttle (FS/S)" },
  { name: "shuttle-42st-gs",            lng: -73.9858, lat: 40.7560, zoom: 16, notes: "42 St Shuttle (GS/S) -- higher zoom" },
  { name: "shuttle-rockaway-h",         lng: -73.8324, lat: 40.5860, zoom: 13, notes: "Rockaway Park Shuttle (H)" },

  // --- Specific problem zones ---
  { name: "g-fulton-st-outlier",        lng: -73.9754, lat: 40.6871, zoom: 16, notes: "G outlier at Fulton St transition (42m gap site)" },
];

// Dedupe by name (keep first occurrence)
const seen = new Set();
const SCENES = SCENES_RAW.filter((s) => {
  if (seen.has(s.name)) return false;
  seen.add(s.name);
  return true;
});

console.log("[qa-routes] total scenes:", SCENES.length);

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
const BASE_URL = "http://localhost:3000/?subway-visual=1&qa-map=1";

function queryFileName(sceneName) {
  return "qa-real-" + sceneName + ".source-features.json";
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();

page.on("pageerror", (e) => console.error("[pageerror]", e.message));
page.on("console", (m) => {
  const t = m.type();
  if (t === "error") {
    console.error("[page-console-error]", m.text().slice(0, 300));
  } else if (t === "warning") {
    // Only log subway-related warnings (skip deck.gl race which is pre-existing)
    const txt = m.text();
    if (!txt.includes("deck-layer-group-before:sr-subway-casing")) {
      console.warn("[page-console-warn]", txt.slice(0, 300));
    }
  }
});

console.log("[qa-routes] navigating to", BASE_URL);
await page.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 60000 });

// Wait for window.__smartRouteMap to be exposed
console.log("[qa-routes] waiting for window.__smartRouteMap (timeout 60s)...");
await page.waitForFunction(() => typeof window.__smartRouteMap !== "undefined", {
  timeout: 60000,
  polling: 500,
});
console.log("[qa-routes] window.__smartRouteMap is set");

// Wait for subway visual layer to load
console.log("[qa-routes] waiting 6s for subway visual layer to settle...");
await page.waitForTimeout(6000);

// Take a default-view screenshot first
const defaultScreenshot = OUT_DIR + "/qa-real-default-view.png";
await page.screenshot({ path: defaultScreenshot, fullPage: false });
console.log("[qa-routes] wrote qa-real-default-view.png");

// ---------------------------------------------------------------------------
// Scene loop
// ---------------------------------------------------------------------------
let successCount = 0;
let errorCount = 0;

for (const scene of SCENES) {
  try {
    // Use jumpTo via window.__smartRouteMap
    await page.evaluate(
      ({ lng, lat, zoom }) => {
        window.__smartRouteMap.jumpTo({
          center: [lng, lat],
          zoom,
          pitch: 0,
          bearing: 0,
        });
      },
      { lng: scene.lng, lat: scene.lat, zoom: scene.zoom }
    );

    // Wait for map to render
    await page.waitForTimeout(2500);

    const screenshotPath = OUT_DIR + "/qa-real-" + scene.name + ".png";
    await page.screenshot({ path: screenshotPath, fullPage: false });

    const sourceFeatures = await page.evaluate(() => {
      const map = window.__smartRouteMap;
      const sourceId = "sr-subway-network";
      const features = map.querySourceFeatures(sourceId, {
        sourceLayer: undefined,
      });
      const routeCounts = {};
      const familyCounts = {};
      const simplified = [];

      for (const feature of features) {
        const properties = feature.properties ?? {};
        const routeIds = Array.isArray(properties.route_ids)
          ? properties.route_ids
          : String(properties.route_ids ?? "").split(",").filter(Boolean);
        for (const routeId of routeIds) {
          routeCounts[routeId] = (routeCounts[routeId] ?? 0) + 1;
        }
        const color = String(properties.color ?? "missing");
        familyCounts[color] = (familyCounts[color] ?? 0) + 1;

        simplified.push({
          id: feature.id ?? null,
          route_id: properties.route_id ?? null,
          route_ids: routeIds,
          color: properties.color ?? null,
          lane_slot: properties.lane_slot ?? null,
          lane_group_id: properties.lane_group_id ?? null,
          visual_z_order: properties.visual_z_order ?? null,
          physical_bundle_id: properties.physical_bundle_id ?? null,
          materialized_bundle_id: properties.materialized_bundle_id ?? null,
          bundle_materialization_role: properties.bundle_materialization_role ?? null,
          visual_feature_type: properties.visual_feature_type ?? null,
          length_m: properties.length_m ?? null,
          corridor_id: properties.corridor_id ?? null,
          source_corridor_id: properties.source_corridor_id ?? null,
          coordinates_sample: feature.geometry?.type === "LineString"
            ? {
                first: feature.geometry.coordinates[0] ?? null,
                last: feature.geometry.coordinates[feature.geometry.coordinates.length - 1] ?? null,
                count: feature.geometry.coordinates.length,
              }
            : null,
        });
      }

      return {
        source_id: sourceId,
        feature_count: features.length,
        route_counts: routeCounts,
        family_counts: familyCounts,
        features: simplified,
      };
    });

    writeFileSync(
      OUT_DIR + "/" + queryFileName(scene.name),
      JSON.stringify({ scene, source_features: sourceFeatures }, null, 2),
    );

    console.log("[qa-routes] [" + (successCount + 1) + "/" + SCENES.length + "] wrote qa-real-" + scene.name + ".png");
    successCount++;
  } catch (err) {
    console.error("[qa-routes] ERROR on scene", scene.name + ":", err.message);
    errorCount++;
  }
}

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------
const manifest = {
  timestamp: TIMESTAMP,
  base_url: BASE_URL,
  viewport: { width: 1600, height: 1000 },
  scenes: SCENES.map((s) => ({
    ...s,
    screenshot: "qa-real-" + s.name + ".png",
    source_features: queryFileName(s.name),
  })),
  summary: {
    total_scenes: SCENES.length,
    success_count: successCount,
    error_count: errorCount,
  },
};

writeFileSync(
  OUT_DIR + "/qa-real-manifest.json",
  JSON.stringify(manifest, null, 2)
);
console.log("[qa-routes] wrote qa-real-manifest.json");

await browser.close();

console.log("[qa-routes] DONE -- " + successCount + " scenes captured, " + errorCount + " errors");
console.log("[qa-routes] output dir:", OUT_DIR);
