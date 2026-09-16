import { existsSync, mkdirSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import {
  buildStationAnchors,
  splitStationAnchorCollections,
  stripRuntimeStationAnchorDebugProperties,
} from "./build/station-anchors/index.ts";
import type { JsonValue } from "./build/types.ts";
import { isJsonObject, parsedJson } from "./build/visual-network/shared/route-config.ts";

type MarkerFeature = {
  properties?: {
    marker_type?: string;
  };
};

type JsonFeatureCollection = {
  type: "FeatureCollection";
  features: JsonValue[];
  metadata?: JsonValue;
};

const frontendRoot = process.cwd();
const publicDir = path.resolve(frontendRoot, "public");
// Engineering-only debug artifacts go OUTSIDE public/ so they are never served
// in production; only the runtime station-anchors artifact stays in public/.
const debugDir = path.resolve(frontendRoot, "artifacts/debug");
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

function featureCollectionFromJson(value: JsonValue, filePath: string): JsonFeatureCollection {
  if (
    !isJsonObject(value) ||
    value.type !== "FeatureCollection" ||
    !Array.isArray(value.features)
  ) {
    throw new Error(`${filePath} must be a FeatureCollection`);
  }
  const collection: JsonFeatureCollection = {
    type: "FeatureCollection",
    features: value.features,
  };
  if ("metadata" in value) collection.metadata = value.metadata;
  return collection;
}

async function readFeatureCollection(filePath: string): Promise<JsonFeatureCollection> {
  return featureCollectionFromJson(parsedJson(await readFile(filePath, "utf8")), filePath);
}

async function writeJson(filePath: string, value: { type?: string; features?: MarkerFeature[] }): Promise<void> {
  await writeFile(filePath, `${JSON.stringify(value)}\n`);
}

function countByMarkerType(collection: { features?: MarkerFeature[] }) {
  const counts: Record<string, number> = {};
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
    readFeatureCollection(INPUT_VISUAL),
    readFeatureCollection(INPUT_STATIONS),
  ]);

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
