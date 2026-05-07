import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const helperPath = new URL("./subway-lane-separation.ts", import.meta.url);
const helperSource = readFileSync(helperPath, "utf8");
const helperModule = { exports: {} };
const warnings = [];
const transpiled = ts.transpileModule(helperSource, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
});

vm.runInNewContext(transpiled.outputText, {
  console: {
    ...console,
    warn: (...args) => warnings.push(args),
  },
  exports: helperModule.exports,
  module: helperModule,
  process,
  require,
  structuredClone,
}, { filename: helperPath.pathname });

const {
  prepareSubwayNetworkForLaneSeparation,
  normalizeSubwayRouteId,
  SUBWAY_ROUTE_FAMILY,
  MANUAL_CORRIDOR_OVERRIDES,
  resolveSubwayLaneRenderMode,
} = helperModule.exports;

function lineFeature(routeId, coordinates, extra = {}) {
  return {
    type: "Feature",
    properties: { route_id: routeId, ...extra },
    geometry: { type: "LineString", coordinates },
  };
}

function multiLineFeature(routeId, coordinates) {
  return {
    type: "Feature",
    properties: { route_id: routeId },
    geometry: { type: "MultiLineString", coordinates },
  };
}

function coordinateOnSegment(coordinate, start, end) {
  const [lng, lat] = coordinate;
  const [startLng, startLat] = start;
  const [endLng, endLat] = end;
  const dx = endLng - startLng;
  const dy = endLat - startLat;
  const lengthSquared = dx * dx + dy * dy;

  if (lengthSquared === 0) {
    return Math.abs(lng - startLng) < 1e-9 && Math.abs(lat - startLat) < 1e-9;
  }

  const t = ((lng - startLng) * dx + (lat - startLat) * dy) / lengthSquared;
  if (t < -1e-9 || t > 1 + 1e-9) return false;

  const projectedLng = startLng + t * dx;
  const projectedLat = startLat + t * dy;
  return (
    Math.abs(projectedLng - lng) < 1e-9 &&
    Math.abs(projectedLat - lat) < 1e-9
  );
}

function coordinateOnPolyline(coordinate, polyline) {
  for (let index = 1; index < polyline.length; index += 1) {
    if (coordinateOnSegment(coordinate, polyline[index - 1], polyline[index])) {
      return true;
    }
  }

  return false;
}

const canonical = {
  type: "FeatureCollection",
  features: [
    lineFeature(" 2 ", [
      [-73.98, 40.8],
      [-73.98, 40.82],
    ]),
    lineFeature("3", [
      [-73.98, 40.82],
      [-73.98, 40.8],
    ]),
    lineFeature("unknown", [
      [-73.9, 40.7],
      [-73.8, 40.7],
    ]),
    lineFeature("Q", [[-73.9, 40.7]]),
    multiLineFeature("7X", [
      [
        [-73.94, 40.75],
        [-73.95, 40.75],
      ],
      [
        [-73.95, 40.75],
        [-73.92, 40.75],
        [-73.9, 40.75],
      ],
    ]),
  ],
};

const originalJson = JSON.stringify(canonical);
const disabled = prepareSubwayNetworkForLaneSeparation(canonical, {
  enabled: false,
});

assert.equal(disabled, canonical, "disabled mode should return canonical data");
assert.equal(
  resolveSubwayLaneRenderMode(),
  "family-visual",
  "default lane render mode should use the clean family visual baseline",
);
assert.equal(resolveSubwayLaneRenderMode("visual"), "visual-no-lanes");
assert.equal(resolveSubwayLaneRenderMode("lanes"), "corridor-lanes-4b");
assert.equal(resolveSubwayLaneRenderMode("junctions"), "junction-transitions-4c");
assert.equal(resolveSubwayLaneRenderMode("family-visual"), "family-visual");
assert.equal(resolveSubwayLaneRenderMode("schematic-family-pilot"), "schematic-family-pilot");
assert.equal(resolveSubwayLaneRenderMode("group-corridors"), "group-corridors");

const prepared = prepareSubwayNetworkForLaneSeparation(canonical, {
  mode: "corridor-lanes-4b",
});

assert.notEqual(prepared, canonical, "enabled mode should return a render copy");
assert.equal(
  JSON.stringify(canonical),
  originalJson,
  "canonical feature collection should not be mutated",
);
assert.equal(normalizeSubwayRouteId(" 6d "), "6X");
assert.equal(normalizeSubwayRouteId("fx"), "F");
assert.equal(SUBWAY_ROUTE_FAMILY.B, "B-D-F-M");
assert.equal(SUBWAY_ROUTE_FAMILY.Q, "N-Q-R-W");
assert.equal(
  Array.isArray(MANUAL_CORRIDOR_OVERRIDES),
  true,
  "manual corridor overrides should be exported for reviewable lane ordering",
);

const familyVisualPrepared = prepareSubwayNetworkForLaneSeparation(canonical, {
  mode: "family-visual",
});
assert.equal(
  familyVisualPrepared,
  canonical,
  "family-visual must bypass the runtime 4B/4C lane preparation path entirely",
);
assert.equal(
  familyVisualPrepared.features.some(
    (feature) =>
      feature.properties?.corridor_id ||
      String(feature.properties?.segment_kind ?? "").startsWith("junction") ||
      feature.properties?.visual_lane_slot !== undefined,
  ),
  false,
  "family-visual must not inject corridor, transition, or lane metadata at runtime",
);

const schematicPilotPrepared = prepareSubwayNetworkForLaneSeparation(canonical, {
  mode: "schematic-family-pilot",
});
assert.equal(
  schematicPilotPrepared,
  canonical,
  "schematic-family-pilot must bypass the runtime 4B/4C lane preparation path entirely",
);
assert.equal(
  schematicPilotPrepared.features.some(
    (feature) =>
      feature.properties?.corridor_id ||
      String(feature.properties?.segment_kind ?? "").startsWith("junction") ||
      feature.properties?.visual_lane_slot !== undefined,
  ),
  false,
  "schematic-family-pilot must not inject legacy corridor, transition, or visual_lane_slot metadata at runtime",
);

const groupCorridorsPrepared = prepareSubwayNetworkForLaneSeparation(canonical, {
  mode: "group-corridors",
});
assert.equal(
  groupCorridorsPrepared,
  canonical,
  "group-corridors must bypass the runtime 4B/4C bbox and transition preparation path entirely",
);
assert.equal(
  groupCorridorsPrepared.features.some(
    (feature) =>
      feature.properties?.corridor_id ||
      String(feature.properties?.segment_kind ?? "").startsWith("junction") ||
      feature.properties?.transition_length_meters !== undefined,
  ),
  false,
  "group-corridors must not inject legacy corridor or transition metadata at runtime",
);

const dekalbOverride = MANUAL_CORRIDOR_OVERRIDES.find(
  (override) => override.corridorId === "dekalb-atlantic",
);
const orangeOverride = MANUAL_CORRIDOR_OVERRIDES.find(
  (override) => override.corridorId === "6av-orange-trunk",
);
const yellowOverride = MANUAL_CORRIDOR_OVERRIDES.find(
  (override) => override.corridorId === "broadway-yellow-trunk",
);
const brightonOverride = MANUAL_CORRIDOR_OVERRIDES.find(
  (override) => override.corridorId === "brighton-bq-brooklyn",
);
const nassauJzOverride = MANUAL_CORRIDOR_OVERRIDES.find(
  (override) => override.corridorId === "nassau-jz-les",
);
const montagueOverride = MANUAL_CORRIDOR_OVERRIDES.find(
  (override) => override.corridorId === "montague-nrw-tunnel",
);
const sixtyThirdOverride = MANUAL_CORRIDOR_OVERRIDES.find(
  (override) => override.corridorId === "63st-yellow-nq",
);

assert.ok(dekalbOverride, "DeKalb / Atlantic override should exist");
assert.ok(orangeOverride, "6 Av orange trunk override should exist");
assert.ok(yellowOverride, "Broadway yellow trunk override should exist");
assert.ok(brightonOverride, "Brighton B/Q Brooklyn override should exist");
assert.ok(nassauJzOverride, "Nassau / LES J/Z corridor override should exist");
assert.ok(montagueOverride, "Montague tunnel N/R/W corridor override should exist");
assert.ok(sixtyThirdOverride, "63 St N/Q corridor override should exist");
assert.deepEqual(
  Array.from(orangeOverride.laneOrder),
  ["B", "D", "F", "M"],
  "6 Av orange trunk should keep a stable B/D/F/M lane order",
);
assert.deepEqual(
  Array.from(brightonOverride.laneOrder),
  ["B", "Q"],
  "Brighton Brooklyn corridor should keep B visible beside Q",
);
assert.deepEqual(
  Array.from(nassauJzOverride.laneOrder),
  ["J", "Z"],
  "Nassau / LES corridor should keep J and Z as service lanes",
);
assert.deepEqual(
  Array.from(montagueOverride.laneOrder),
  ["N", "R", "W"],
  "Montague tunnel corridor should explicitly cover N/R/W service lanes",
);
assert.deepEqual(
  Array.from(sixtyThirdOverride.laneOrder),
  ["N", "Q"],
  "63 St connector should cover the N/Q branch without widening Broadway globally",
);

assert.equal(prepared.features[0].properties.visual_lane_slot, 0);
assert.equal(prepared.features[0].properties.visual_z_order > 0, true);
assert.equal(prepared.features[0].properties.visual_family, "1-2-3");
assert.equal(
  prepared.features[1].properties.visual_lane_slot,
  0,
  "opposite-direction fallback features should stay centered in corridor-lanes-4b",
);
assert.equal(prepared.features[2].properties.visual_lane_slot, 0);
assert.equal(prepared.features[2].properties.visual_z_order, 0);
assert.equal(prepared.features[2].properties.visual_family, "solo");
assert.equal(
  prepared.features[3].properties.visual_lane_slot,
  0,
  "short fallback geometry should stay centered in corridor-lanes-4b",
);
assert.equal(
  prepared.features[4].properties.visual_lane_slot,
  0,
  "MultiLineString fallback geometry should stay centered in corridor-lanes-4b",
);
assert.equal(warnings.length, 1, "invalid geometry should warn only in dev");

const globalPrepared = prepareSubwayNetworkForLaneSeparation(canonical, {
  mode: "global-lanes",
});
assert.equal(
  globalPrepared.features[1].properties.visual_lane_slot,
  -1,
  "global-lanes diagnostic mode should preserve old direction-based slot flipping",
);
assert.equal(
  globalPrepared.features[3].properties.visual_lane_slot,
  -0.5,
  "global-lanes diagnostic mode should preserve old short-geometry route-family slots",
);
assert.equal(
  globalPrepared.features[4].properties.visual_lane_slot,
  0.5,
  "global-lanes diagnostic mode should use longest MultiLineString direction",
);

const corridorSample = {
  type: "FeatureCollection",
  features: [
    lineFeature("R", [
      [-73.997, 40.686],
      [-73.986, 40.689],
      [-73.976, 40.69],
      [-73.966, 40.704],
    ]),
  ],
};

const corridorPrepared = prepareSubwayNetworkForLaneSeparation(corridorSample, {
  mode: "corridor-lanes-4b",
});
const routeSegments = corridorPrepared.features.filter(
  (feature) => feature.properties?.route_id === "R",
);
const dekalbSegment = routeSegments.find(
  (feature) =>
    feature.properties?.corridor_id === "dekalb-atlantic" &&
    feature.properties?.segment_kind === "corridor",
);
const fallbackSegment = routeSegments.find(
  (feature) => feature.properties?.segment_kind === "fallback",
);

assert.equal(
  routeSegments.length >= 2,
  true,
  "features crossing a corridor override should split into render-only segments",
);
assert.ok(dekalbSegment, "inside-bounds segment should receive corridor metadata");
assert.ok(fallbackSegment, "outside-bounds segment should keep fallback metadata");
assert.equal(dekalbSegment.properties.corridor_override, true);
assert.equal(dekalbSegment.properties.transition_length_meters, 30);
assert.equal(
  dekalbSegment.properties.visual_lane_slot,
  dekalbOverride.laneSlots.R,
  "corridor-specific lane slot should override global route-family slot",
);
assert.equal(
  Math.abs(dekalbSegment.geometry.coordinates[0][0] - dekalbOverride.bounds.minLng) < 1e-9,
  true,
  "corridor segment should start at the exact bbox boundary, not the previous outside vertex",
);
assert.equal(
  JSON.stringify(fallbackSegment.geometry.coordinates.at(-1)),
  JSON.stringify(dekalbSegment.geometry.coordinates[0]),
  "corridor boundary split should reuse the exact same endpoint coordinate on both sides",
);

const junctionSegments = routeSegments.filter((feature) =>
  String(feature.properties?.segment_kind ?? "").startsWith("junction"),
);
assert.equal(
  junctionSegments.length,
  0,
  "Phase 4B stable mode should not generate junction transition fragments",
);

const corridorPreparedWithTransitions = prepareSubwayNetworkForLaneSeparation(
  corridorSample,
  { mode: "junction-transitions-4c" },
);
const routeSegmentsWithTransitions = corridorPreparedWithTransitions.features.filter(
  (feature) => feature.properties?.route_id === "R",
);
const transitionFallbackSegment = routeSegmentsWithTransitions.find(
  (feature) => feature.properties?.segment_kind === "fallback",
);
const transitionCorridorSegment = routeSegmentsWithTransitions.find(
  (feature) => feature.properties?.segment_kind === "corridor",
);
const explicitJunctionSegments = routeSegmentsWithTransitions.filter((feature) =>
  String(feature.properties?.segment_kind ?? "").startsWith("junction"),
);
assert.equal(
  explicitJunctionSegments.length > 0,
  true,
  "4C mode should remain available behind an explicit transition flag",
);

const fallbackSlot = Number(fallbackSegment.properties.visual_lane_slot);
const corridorSlot = Number(dekalbSegment.properties.visual_lane_slot);
const transitionSlots = explicitJunctionSegments.map((feature) =>
  Number(feature.properties?.visual_lane_slot),
);
assert.equal(
  transitionSlots.some(
    (slot) =>
      slot > Math.min(fallbackSlot, corridorSlot) &&
      slot < Math.max(fallbackSlot, corridorSlot),
  ),
  true,
  "junction transition slots should interpolate between corridor and fallback lanes",
);
assert.ok(transitionFallbackSegment, "4C mode should preserve fallback segments");
assert.ok(transitionCorridorSegment, "4C mode should preserve corridor segments");

const mergeSample = {
  type: "FeatureCollection",
  features: [
    lineFeature(
      "A",
      [
        [-73.99, 40.7],
        [-73.98, 40.7],
      ],
      { shape_id: "A.merge-1" },
    ),
    lineFeature(
      "A",
      [
        [-73.98, 40.7],
        [-73.97, 40.7],
      ],
      { shape_id: "A.merge-2" },
    ),
  ],
};
const mergedSample = prepareSubwayNetworkForLaneSeparation(mergeSample, {
  mode: "corridor-lanes-4b",
});
assert.equal(
  mergedSample.features.length,
  1,
  "adjacent same-route/same-lane visual features should merge before rendering",
);
assert.equal(
  JSON.stringify(mergedSample.features[0].geometry.coordinates),
  JSON.stringify([
    [-73.99, 40.7],
    [-73.98, 40.7],
    [-73.97, 40.7],
  ]),
  "merged visual feature should preserve the original canonical path",
);

const nonAdjacentMergeSample = {
  type: "FeatureCollection",
  features: [
    lineFeature(
      "A",
      [
        [-73.99, 40.71],
        [-73.98, 40.71],
      ],
      { shape_id: "A.non-adjacent-1" },
    ),
    lineFeature(
      "C",
      [
        [-73.7, 40.71],
        [-73.69, 40.71],
      ],
      { shape_id: "C.interleaved" },
    ),
    lineFeature(
      "A",
      [
        [-73.98, 40.71],
        [-73.97, 40.71],
      ],
      { shape_id: "A.non-adjacent-2" },
    ),
  ],
};
const nonAdjacentMergedSample = prepareSubwayNetworkForLaneSeparation(nonAdjacentMergeSample, {
  mode: "corridor-lanes-4b",
});
const nonAdjacentMergedA = nonAdjacentMergedSample.features.filter(
  (feature) => feature.properties?.route_id === "A",
);
assert.equal(
  nonAdjacentMergedA.length,
  1,
  "same-route/same-lane contiguous render segments should merge even when interleaved by another route",
);
assert.equal(
  JSON.stringify(nonAdjacentMergedA[0].geometry.coordinates),
  JSON.stringify([
    [-73.99, 40.71],
    [-73.98, 40.71],
    [-73.97, 40.71],
  ]),
  "non-adjacent merge should preserve the continuous canonical path",
);

const fallbackVariantSample = {
  type: "FeatureCollection",
  features: [
    lineFeature(
      "R",
      [
        [-73.965, 40.762],
        [-73.955, 40.766],
      ],
      { shape_id: "R..N63R" },
    ),
    lineFeature(
      "R",
      [
        [-73.955, 40.766],
        [-73.965, 40.762],
      ],
      { shape_id: "R..S63R" },
    ),
  ],
};
const fallbackVariantPrepared = prepareSubwayNetworkForLaneSeparation(fallbackVariantSample, {
  mode: "corridor-lanes-4b",
});
const fallbackVariantR = fallbackVariantPrepared.features.filter(
  (feature) => feature.properties?.route_id === "R",
);
assert.equal(
  fallbackVariantR.length,
  1,
  "problem-area fallback direction variants should collapse to one centered render strand",
);
assert.equal(
  fallbackVariantR[0].properties.visual_lane_slot,
  0,
  "collapsed fallback variant should remain centered, not inherit a corridor lane",
);

const duplicateDirectionCorridorSample = {
  type: "FeatureCollection",
  features: [
    lineFeature(
      "R",
      [
        [-74.0, 40.684],
        [-73.988, 40.689],
      ],
      { shape_id: "R..N27R" },
    ),
    lineFeature(
      "R",
      [
        [-73.988, 40.689],
        [-74.0, 40.684],
      ],
      { shape_id: "R..S27R" },
    ),
    lineFeature(
      "Q",
      [
        [-74.0, 40.684],
        [-73.988, 40.689],
      ],
      { shape_id: "Q..N19R" },
    ),
  ],
};
const duplicateDirectionPrepared = prepareSubwayNetworkForLaneSeparation(
  duplicateDirectionCorridorSample,
  { mode: "corridor-lanes-4b" },
);
const duplicateRSegments = duplicateDirectionPrepared.features.filter(
  (feature) =>
    feature.properties?.corridor_id === "dekalb-atlantic" &&
    feature.properties?.route_id === "R",
);

assert.equal(
  duplicateRSegments.length,
  1,
  "corridor lanes should collapse duplicate direction/shape variants to one rendered service lane",
);
assert.equal(
  duplicateRSegments[0].properties.visual_lane_slot,
  dekalbOverride.laneSlots.R,
  "corridor-specific service lane should not flip sides by direction",
);

const brightonSample = {
  type: "FeatureCollection",
  features: [
    lineFeature(
      "B",
      [
        [-73.96149, 40.57762],
        [-73.96074, 40.61762],
        [-73.96288, 40.6503],
        [-73.97237, 40.67705],
      ],
      { shape_id: "B..S65R" },
    ),
    lineFeature(
      "Q",
      [
        [-73.96149, 40.57762],
        [-73.96074, 40.61762],
        [-73.96288, 40.6503],
        [-73.97237, 40.67705],
      ],
      { shape_id: "Q..S65R" },
    ),
  ],
};
const brightonPrepared = prepareSubwayNetworkForLaneSeparation(brightonSample, {
  mode: "corridor-lanes-4b",
});
const brightonB = brightonPrepared.features.find(
  (feature) =>
    feature.properties?.route_id === "B" &&
    feature.properties?.corridor_id === "brighton-bq-brooklyn",
);
const brightonQ = brightonPrepared.features.find(
  (feature) =>
    feature.properties?.route_id === "Q" &&
    feature.properties?.corridor_id === "brighton-bq-brooklyn",
);
assert.ok(brightonB, "B should receive corridor metadata on the Brooklyn Brighton shared path");
assert.ok(brightonQ, "Q should receive corridor metadata on the Brooklyn Brighton shared path");
assert.equal(
  brightonB.properties.visual_lane_slot,
  brightonOverride.laneSlots.B,
  "B should get a visible Brighton corridor lane instead of disappearing under Q",
);
assert.equal(
  brightonQ.properties.visual_lane_slot,
  brightonOverride.laneSlots.Q,
  "Q should get the paired Brighton corridor lane",
);

const targetedCorridorSample = {
  type: "FeatureCollection",
  features: [
    lineFeature(
      "J",
      [
        [-74.011056, 40.706476],
        [-74.009276, 40.708706],
        [-73.9901, 40.7156],
      ],
      { shape_id: "J.targeted-les" },
    ),
    lineFeature(
      "Z",
      [
        [-74.011056, 40.706476],
        [-74.009276, 40.708706],
        [-73.9901, 40.7156],
      ],
      { shape_id: "Z.targeted-les" },
    ),
    lineFeature(
      "N",
      [
        [-74.012994, 40.703087],
        [-74.01025, 40.7],
        [-73.994, 40.694710784659705],
      ],
      { shape_id: "N.targeted-montague" },
    ),
    lineFeature(
      "R",
      [
        [-74.012994, 40.703087],
        [-74.01025, 40.7],
        [-73.994, 40.694710784659705],
      ],
      { shape_id: "R.targeted-montague" },
    ),
    lineFeature(
      "Q",
      [
        [-73.9782102160804, 40.768],
        [-73.966113, 40.764629],
        [-73.947656, 40.783471],
      ],
      { shape_id: "Q.targeted-63st" },
    ),
    lineFeature(
      "N",
      [
        [-73.9782102160804, 40.768],
        [-73.966113, 40.764629],
        [-73.947656, 40.783471],
      ],
      { shape_id: "N.targeted-63st" },
    ),
  ],
};
const targetedCorridorPrepared = prepareSubwayNetworkForLaneSeparation(targetedCorridorSample, {
  mode: "corridor-lanes-4b",
});
const targetedJ = targetedCorridorPrepared.features.find(
  (feature) =>
    feature.properties?.route_id === "J" &&
    feature.properties?.corridor_id === "nassau-jz-les",
);
const targetedZ = targetedCorridorPrepared.features.find(
  (feature) =>
    feature.properties?.route_id === "Z" &&
    feature.properties?.corridor_id === "nassau-jz-les",
);
const targetedNMontague = targetedCorridorPrepared.features.find(
  (feature) =>
    feature.properties?.shape_id === "N.targeted-montague" &&
    feature.properties?.corridor_id === "montague-nrw-tunnel",
);
const targetedRMontague = targetedCorridorPrepared.features.find(
  (feature) =>
    feature.properties?.shape_id === "R.targeted-montague" &&
    feature.properties?.corridor_id === "montague-nrw-tunnel",
);
const targetedNSixtyThird = targetedCorridorPrepared.features.find(
  (feature) =>
    feature.properties?.shape_id === "N.targeted-63st" &&
    feature.properties?.corridor_id === "63st-yellow-nq",
);
const targetedQSixtyThird = targetedCorridorPrepared.features.find(
  (feature) =>
    feature.properties?.shape_id === "Q.targeted-63st" &&
    feature.properties?.corridor_id === "63st-yellow-nq",
);
assert.ok(targetedJ, "J should receive targeted Nassau / LES corridor metadata");
assert.ok(targetedZ, "Z should receive targeted Nassau / LES corridor metadata");
assert.equal(targetedJ.properties.visual_lane_slot, nassauJzOverride.laneSlots.J);
assert.equal(targetedZ.properties.visual_lane_slot, nassauJzOverride.laneSlots.Z);
assert.ok(targetedNMontague, "N should receive targeted Montague tunnel metadata");
assert.ok(targetedRMontague, "R should receive targeted Montague tunnel metadata");
assert.equal(targetedNMontague.properties.visual_lane_slot, montagueOverride.laneSlots.N);
assert.equal(targetedRMontague.properties.visual_lane_slot, montagueOverride.laneSlots.R);
assert.ok(targetedNSixtyThird, "N should receive targeted 63 St branch metadata");
assert.ok(targetedQSixtyThird, "Q should receive targeted 63 St branch metadata");
assert.equal(targetedNSixtyThird.properties.visual_lane_slot, sixtyThirdOverride.laneSlots.N);
assert.equal(targetedQSixtyThird.properties.visual_lane_slot, sixtyThirdOverride.laneSlots.Q);

const originalCoordinates = new Set(
  corridorSample.features[0].geometry.coordinates.map((coordinate) =>
    JSON.stringify(coordinate),
  ),
);
for (const segment of routeSegments) {
  for (const coordinate of segment.geometry.coordinates) {
    assert.equal(
      originalCoordinates.has(JSON.stringify(coordinate)) ||
        coordinateOnPolyline(coordinate, corridorSample.features[0].geometry.coordinates),
      true,
      "corridor splitting must keep any interpolated transition points on canonical geometry",
    );
  }
}

const groupCorridorsPreBakedSlotData = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: {
        route_id: "B",
        display_route: "B",
        visual_lane_slot: -1.5,
        visual_z_order: 220,
        visual_family: "B-D-F-M",
      },
      geometry: {
        type: "LineString",
        coordinates: [
          [-73.99, 40.74],
          [-73.98, 40.75],
        ],
      },
    },
  ],
};
const groupCorridorsPreBakedSlotResult = prepareSubwayNetworkForLaneSeparation(
  groupCorridorsPreBakedSlotData,
  { mode: "group-corridors" },
);
assert.equal(
  groupCorridorsPreBakedSlotResult.features[0].properties.visual_lane_slot,
  -1.5,
  "group-corridors mode must preserve pre-baked visual_lane_slot via early exit",
);
assert.equal(
  groupCorridorsPreBakedSlotResult.features[0].properties.visual_z_order,
  220,
  "group-corridors mode must preserve pre-baked visual_z_order via early exit",
);

const baseLaneMetadataPreBakedData = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: {
        route_id: "Q",
        visual_lane_slot: 99, // sentinel; ROUTE_LANE_SLOTS["Q"] is -0.5
        visual_z_order: 150,
        visual_family: "N-Q-R-W",
      },
      geometry: {
        type: "LineString",
        coordinates: [
          [-73.99, 40.74],
          [-73.98, 40.75],
        ],
      },
    },
  ],
};
const baseLaneMetadataPreBakedResult = prepareSubwayNetworkForLaneSeparation(
  baseLaneMetadataPreBakedData,
  { enabled: true, mode: "global-lanes" },
);
assert.equal(
  baseLaneMetadataPreBakedResult.features[0].properties.visual_lane_slot,
  99,
  "pre-baked slot must win over ROUTE_LANE_SLOTS in global-lanes mode",
);

console.log("subway lane separation checks passed");
