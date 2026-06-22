import { existsSync, mkdirSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  buildStationAnchors,
  splitStationAnchorCollections,
  stripRuntimeStationAnchorDebugProperties,
} from "./build/subway-station-anchors.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const publicDir = path.resolve(__dirname, "../public");
// Engineering-only debug artifacts go OUTSIDE public/ so they are never served
// in production; only the runtime station-anchors artifact stays in public/.
const debugDir = path.resolve(__dirname, "../artifacts/debug");
mkdirSync(debugDir, { recursive: true });

const INPUT_VISUAL = path.join(publicDir, "subway-network.visual.geojson");
const INPUT_STATIONS = path.join(publicDir, "subway-network.stations.geojson");

const OUTPUT_ANCHORS = path.join(publicDir, "subway-network.station-anchors.geojson");
const OUTPUT_DEBUG_ANCHORS = path.join(
  debugDir,
  "subway-network.station-anchors-debug-runtime.geojson",
);
const OUTPUT_RAW = path.join(
  debugDir,
  "subway-network.station-anchors-debug-raw.geojson",
);
const OUTPUT_SNAPS = path.join(
  debugDir,
  "subway-network.station-anchors-debug-snaps.geojson",
);
const OUTPUT_REJECTED = path.join(
  debugDir,
  "subway-network.station-anchors-debug-rejected.geojson",
);
const OUTPUT_AMBIGUOUS = path.join(
  debugDir,
  "subway-network.station-anchors-debug-ambiguous.geojson",
);

async function readJson(filePath) {
  return JSON.parse(await readFile(filePath, "utf8"));
}

async function writeJson(filePath, value) {
  await writeFile(filePath, `${JSON.stringify(value)}\n`);
}

function countByMarkerType(collection) {
  const counts = {};
  for (const feature of collection.features ?? []) {
    const type = feature.properties?.marker_type ?? "missing";
    counts[type] = (counts[type] ?? 0) + 1;
  }
  return counts;
}

async function main() {
  if (!existsSync(INPUT_STATIONS)) {
    throw new Error(
      `${INPUT_STATIONS} is required input and currently has no generator; keep the checked-in station artifact present before running transit builds.`,
    );
  }

  const [visual, stations] = await Promise.all([
    readJson(INPUT_VISUAL),
    readJson(INPUT_STATIONS),
  ]);

  if (visual.type !== "FeatureCollection") {
    throw new Error(`${INPUT_VISUAL} must be a FeatureCollection`);
  }
  if (stations.type !== "FeatureCollection") {
    throw new Error(`${INPUT_STATIONS} must be a FeatureCollection`);
  }

  const result = buildStationAnchors({ visual, stations });
  const collections = splitStationAnchorCollections(result.anchors);
  const runtimeAnchors = stripRuntimeStationAnchorDebugProperties(result.anchors);

  await Promise.all([
    writeJson(OUTPUT_ANCHORS, runtimeAnchors),
    writeJson(OUTPUT_DEBUG_ANCHORS, result.anchors),
    writeJson(OUTPUT_RAW, result.raw),
    writeJson(OUTPUT_SNAPS, result.snaps),
    writeJson(OUTPUT_REJECTED, result.rejected),
    writeJson(OUTPUT_AMBIGUOUS, result.ambiguous),
  ]);

  console.info("[build-subway-station-anchors] complete", {
    stationCount: stations.features.length,
    visualFeatureCount: visual.features.length,
    anchorFeatureCount: result.anchors.features.length,
    markerCounts: countByMarkerType(result.anchors),
    singleStopDotCount: collections.dots.features.length,
    sharedStopFeatureCount: collections.sharedStops.features.length,
    stationLabelCount: collections.labels.features.length,
    stationRouteBadgeCount: collections.badges.features.length,
    debugAnchorCount: result.anchors.features.length,
    rawDebugCount: result.raw.features.length,
    snapDebugCount: result.snaps.features.length,
    rejectedDebugCount: result.rejected.features.length,
    ambiguousDebugCount: result.ambiguous.features.length,
  });
}

main().catch((error) => {
  console.error("[build-subway-station-anchors] failed", error);
  process.exitCode = 1;
});
