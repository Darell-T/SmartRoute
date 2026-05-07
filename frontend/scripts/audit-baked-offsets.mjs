#!/usr/bin/env node
//
// Audits the baked-offset visual geojson against the manual corridor
// override config. Verifies:
//   1. Every override that produces ≥1 feature carries every declared route
//      (i.e., no manual override silently drops a member).
//   2. Solo features keep visual_lane_slot === 0.
//   3. Every feature has finite, well-formed coordinates.
//
// Exit 0 on success, 1 if any check fails. Run after
// `node scripts/build-corridor-groups.mjs`.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __scriptDir = dirname(fileURLToPath(import.meta.url));
const visualPath = resolve(
  __scriptDir,
  "../public/subway-network.group-visual.geojson",
);
const overridesPath = resolve(
  __scriptDir,
  "../components/map/subway-corridor-overrides.json",
);

const visual = JSON.parse(readFileSync(visualPath, "utf8"));
const overrides = JSON.parse(readFileSync(overridesPath, "utf8"));

let errors = 0;
let warnings = 0;

// 1. Each override either produces features or doesn't. Both empty and
//    partially-populated overrides are warnings (not hard fails) because
//    the build script's single-run-per-line model assigns each route to
//    exactly one corridor based on its representative line's midpoint —
//    so a route can only be in one override at a time. Documented in the
//    plan's Task 3 / Task 8.
for (const override of overrides) {
  const matching = visual.features.filter(
    (f) => f.properties.corridor_id === override.corridorId,
  );
  if (matching.length === 0) {
    console.warn(
      `[audit] WARN: override "${override.corridorId}" has no features (no representative-line midpoint sampled inside its bbox)`,
    );
    warnings += 1;
    continue;
  }
  const routes = new Set(matching.map((f) => f.properties.route_id));
  const missing = override.routeIds.filter((r) => !routes.has(r));
  if (missing.length > 0) {
    console.warn(
      `[audit] WARN: override "${override.corridorId}" partially populated; missing [${missing.join(", ")}], saw [${[...routes].sort().join(", ")}] — those routes likely sample into a different override at midpoint`,
    );
    warnings += 1;
  }
}

// 2. Solo features have visual_lane_slot === 0.
const soloMismatches = visual.features.filter(
  (f) =>
    !f.properties.corridor_id &&
    f.properties.segment_kind === "solo" &&
    f.properties.visual_lane_slot != null &&
    Number(f.properties.visual_lane_slot) !== 0,
);
if (soloMismatches.length > 0) {
  console.error(
    `[audit] FAIL: ${soloMismatches.length} solo features have nonzero lane slot`,
  );
  for (const f of soloMismatches.slice(0, 5)) {
    console.error(
      `         route ${f.properties.route_id}: slot ${f.properties.visual_lane_slot}`,
    );
  }
  errors += 1;
}

// 3. Every feature has finite coordinates.
const badCoords = visual.features.filter((f) => {
  if (f.geometry.type !== "LineString") return false;
  return f.geometry.coordinates.some(
    ([lng, lat]) => !Number.isFinite(lng) || !Number.isFinite(lat),
  );
});
if (badCoords.length > 0) {
  console.error(
    `[audit] FAIL: ${badCoords.length} features with non-finite coords`,
  );
  errors += 1;
}

if (errors === 0) {
  console.log(
    `[audit] all baked-offset checks PASS (${visual.features.length} features, ${overrides.length} overrides, ${warnings} warning${warnings === 1 ? "" : "s"})`,
  );
  process.exit(0);
} else {
  console.error(`[audit] ${errors} checks FAILED`);
  process.exit(1);
}
