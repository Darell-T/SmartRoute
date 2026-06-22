import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const workspaceRoot = resolve(frontendRoot, "..");

const scriptPath = resolve(here, "regenerate-canonical-from-gtfs.mjs");
const canonicalPath = resolve(frontendRoot, "public/subway-network.canonical.geojson");
const packagePath = resolve(frontendRoot, "package.json");
const gitignorePath = resolve(workspaceRoot, ".gitignore");
const jarvisMapPath = resolve(frontendRoot, "components/jarvis-map.tsx");

assert.ok(existsSync(scriptPath), "Phase 1 GTFS regeneration script should exist");

const script = readFileSync(scriptPath, "utf8");
assert.match(script, /google_transit\.zip/, "script should target MTA subway GTFS static zip");
assert.match(script, /EXPECTED_ROUTES[\s\S]*"B"/, "script validation should require B route output");
assert.match(script, /subway-network\.canonical\.geojson/, "script should emit the canonical network artifact");
assert.doesNotMatch(script, /subway-network\.visual\.geojson/, "script must not emit visual polyline artifacts");

const pkg = JSON.parse(readFileSync(packagePath, "utf8"));
assert.equal(
  pkg.scripts?.["build:network"],
  "node scripts/regenerate-canonical-from-gtfs.mjs",
  "package.json should expose npm run build:network for Phase 1 regeneration",
);

const gitignore = readFileSync(gitignorePath, "utf8");
assert.match(gitignore, /\.gtfs-cache\//, "downloaded GTFS cache should be ignored");

assert.ok(existsSync(canonicalPath), "canonical subway network artifact should exist");
const canonical = JSON.parse(readFileSync(canonicalPath, "utf8"));
assert.equal(canonical.type, "FeatureCollection", "canonical artifact should be a GeoJSON FeatureCollection");
assert.ok(Array.isArray(canonical.features), "canonical artifact should contain features");
assert.ok(
  canonical.features.some((feature) => feature?.properties?.route_id === "B"),
  "canonical artifact should include B route features",
);
assert.ok(
  canonical.features.every((feature) =>
    feature?.type === "Feature" &&
    feature?.geometry?.type === "LineString" &&
    typeof feature?.properties?.route_id === "string" &&
    typeof feature?.properties?.shape_id === "string" &&
    typeof feature?.properties?.color === "string"
  ),
  "canonical features should preserve route_id, shape_id, color, and LineString geometry",
);

const jarvisMap = readFileSync(jarvisMapPath, "utf8");
assert.match(
  jarvisMap,
  /loadCanonicalSubwayNetwork\(/,
  "JarvisMap should load canonical network data directly for train snapping",
);
assert.doesNotMatch(
  jarvisMap,
  /addSubwayNetwork|addSubwayStops|addSubwayLineStopDotLayer|loadVisualNetwork/,
  "JarvisMap must not register subway visualization overlays",
);

console.log("canonical GTFS regeneration checks passed");
