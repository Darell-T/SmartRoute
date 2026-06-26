import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// Guards the MTA color palette after the single-source consolidation.
// All route -> hex literals now live in lib/mta-colors.json; every renderer,
// popup, and build script imports from it. This check proves (1) the source of
// truth is correct, (2) no consumer reintroduces an inline map or the old navy
// blue, and (3) the color z-order tables, route bullets, and baked artifacts
// stay on the Apple blue.
const ROOT = path.resolve(import.meta.dirname, "../..");
const APPLE_BLUE = "#0A84FF";
const OLD_BLUE = "#0039A6";

function read(relPath) {
  return fs.readFileSync(path.join(ROOT, relPath), "utf8");
}

// 1. Single source of truth: lib/mta-colors.json.
const tokens = JSON.parse(read("lib/mta-colors.json"));
for (const route of ["A", "C", "E"]) {
  assert.equal(
    tokens[route],
    APPLE_BLUE,
    `mta-colors.json should map ${route} to ${APPLE_BLUE}`,
  );
}
const oldBlueTokens = Object.entries(tokens).filter(
  ([, hex]) => String(hex).toUpperCase() === OLD_BLUE,
);
assert.equal(
  oldBlueTokens.length,
  0,
  `mta-colors.json should not use ${OLD_BLUE}; found ${JSON.stringify(oldBlueTokens)}`,
);
assert.ok(tokens.SI, "mta-colors.json should define a single SI color");

// 2. Every color consumer imports the shared source (no inline route map) and
//    never carries the old navy blue.
const SHARED_IMPORT = /mta-colors(\.(mjs|ts))?["']/;
const consumers = [
  "components/map/route-layers.ts",
  "components/map/incidents/incident-popup.ts",
  "components/smart-route/left-rail/types.ts",
  "scripts/build-subway-visual-network.mjs",
  "scripts/build/opendata-subway-lines.mjs",
  "scripts/build/station-anchors/index.ts",
  "scripts/regenerate-canonical-from-gtfs.mjs",
];
for (const relPath of consumers) {
  const source = read(relPath);
  assert.match(
    source,
    SHARED_IMPORT,
    `${relPath} should import the shared mta-colors source`,
  );
  assert.ok(
    !source.toUpperCase().includes(OLD_BLUE),
    `${relPath} should not contain ${OLD_BLUE}`,
  );
}

// 3. Color z-order tables (color -> rank) keep the Apple blue, not the old blue.
const laneOrderSource = read("scripts/build/lane-order.ts");
assert.match(
  laneOrderSource,
  new RegExp(`"${APPLE_BLUE}"`),
  `lane order should include ${APPLE_BLUE}`,
);
assert.doesNotMatch(
  laneOrderSource,
  new RegExp(`"${OLD_BLUE}"`),
  `lane order should not keep ${OLD_BLUE}`,
);

const subwayNetworkSource = read("components/map/subway-network.ts");
assert.match(
  subwayNetworkSource,
  new RegExp(`"${APPLE_BLUE}":\\s*7`),
  `subway-network color rank should include ${APPLE_BLUE}`,
);
assert.doesNotMatch(
  subwayNetworkSource,
  new RegExp(`"${OLD_BLUE}":\\s*7`),
  `subway-network color rank should not keep ${OLD_BLUE}`,
);

// 4. A/C/E route bullets (SVGs) match the line color.
for (const slug of ["a", "c", "e"]) {
  const svg = read(`public/mta-bullets/${slug}.svg`).toLowerCase();
  assert.ok(svg.includes("#0a84ff"), `mta-bullets/${slug}.svg should use #0a84ff`);
  assert.ok(
    !svg.includes("#0039a6"),
    `mta-bullets/${slug}.svg should not use #0039a6`,
  );
}

// 5. Baked artifacts carry the Apple blue, never the old blue.
const visual = JSON.parse(read("public/subway-network.visual.geojson"));
let oldVisualBlueCount = 0;
let newVisualBlueCount = 0;
for (const feature of visual.features ?? []) {
  const color = String(feature.properties?.color ?? "").toUpperCase();
  if (color === OLD_BLUE) oldVisualBlueCount += 1;
  if (color === APPLE_BLUE) newVisualBlueCount += 1;
}
assert.equal(
  oldVisualBlueCount,
  0,
  `visual artifact should not contain ${OLD_BLUE}; found ${oldVisualBlueCount}`,
);
assert.ok(newVisualBlueCount > 0, `visual artifact should contain ${APPLE_BLUE}`);

const stationAnchors = JSON.parse(
  read("public/subway-network.station-anchors.geojson"),
);
let aceBadgeCount = 0;
for (const feature of stationAnchors.features ?? []) {
  if (
    feature.properties?.marker_type === "station_route_badge" &&
    ["A", "C", "E"].includes(String(feature.properties?.route_id ?? ""))
  ) {
    aceBadgeCount += 1;
  }
}
assert.ok(
  aceBadgeCount > 0,
  "station anchors should still emit A/C/E route badges that use the updated SVG icons",
);

console.log(
  JSON.stringify(
    {
      source: "lib/mta-colors.json",
      appleBlue: APPLE_BLUE,
      si: tokens.SI,
      visualBlueFeatures: newVisualBlueCount,
      stationAnchorAceBadges: aceBadgeCount,
    },
    null,
    2,
  ),
);
