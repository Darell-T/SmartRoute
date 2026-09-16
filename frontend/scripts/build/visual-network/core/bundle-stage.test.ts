import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildCandidateDoc,
  REMAINING_UNBUNDLED_CORRIDORS,
} from "../output/artifact-metadata.ts";
import type { LineFeature } from "../shared/types.ts";
import { buildBundleArtifacts, sortVisualLanes } from "./bundle-stage.ts";

const SAMPLE_PARAMETERS = {
  minTripsPerBranch: 5,
  resampleIntervalM: 25,
  hausdorffMaxM: 15,
  overlapMinRatio: 0.6,
  tangentMaxDiffDeg: 30,
  containmentAvgDistanceMaxM: 15,
  containmentOverlapMinRatio: 0.85,
};

function corridor(
  corridorId: string,
  routeIds: string[],
  extra: LineFeature["properties"] = {},
): LineFeature {
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [
        [-73.99, 40.75],
        [-73.99, 40.76],
      ],
    },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      from_stop_id: `${corridorId}-from`,
      to_stop_id: `${corridorId}-to`,
      from_stop_name: "South",
      to_stop_name: "North",
      length_m: 1113,
      ...extra,
    },
  };
}

function candidateSummary(visualFeatures: ReturnType<typeof buildBundleArtifacts>) {
  return buildCandidateDoc({
    generatedAt: "2026-01-01T00:00:00.000Z",
    openDataSourceName: "test",
    openDataSourceDatasetId: "s692-irgq",
    perRouteStats: [],
    validationFailures: [],
    bundleArtifacts: visualFeatures,
    parameters: SAMPLE_PARAMETERS,
  }).metadata.bundle_summary;
}

test("empty corridors emit empty bundle artifacts and no unbundled collection", () => {
  const first = buildBundleArtifacts([], new Map());
  const second = buildBundleArtifacts([], new Map());
  assert.deepEqual(first, {
    bundleFeatures: [],
    bundleLaneFeatures: [],
    bundleGapFeatures: [],
    visualFeatures: [],
  });
  assert.equal("unbundledFeatures" in first, false);
  assert.equal(candidateSummary(first).remaining_unbundled_corridors, 0);
  assert.equal(REMAINING_UNBUNDLED_CORRIDORS, 0);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
});

test("a solo corridor becomes a one-slot bundle_lane, not an unbundled feature", () => {
  const result = buildBundleArtifacts([corridor("c-solo", ["1"])], new Map());
  assert.equal(result.bundleFeatures.length, 0);
  assert.equal(result.bundleLaneFeatures.length, 1);
  assert.equal(result.bundleGapFeatures.length, 0);
  assert.equal("unbundledFeatures" in result, false);
  const lane = result.bundleLaneFeatures[0];
  assert.equal(lane.properties.visual_feature_type, "bundle_lane");
  assert.equal(lane.properties.bundle_id, "solo-00001");
  assert.equal(lane.properties.route_id, "1");
  assert.equal(lane.properties.corridor_id, "c-solo");
  assert.equal(lane.properties.bundle_lane_count, 1);
  assert.equal(lane.properties.lane_slot, 0);
  assert.deepEqual(result.visualFeatures, result.bundleLaneFeatures);
  assert.equal(candidateSummary(result).remaining_unbundled_corridors, 0);
  assert.equal(candidateSummary(result).bundled_render_lane_count, 1);
  assert.equal(candidateSummary(result).bundle_count, 0);
});

test("a two-route corridor emits one bundle, color lanes, and a zero unbundled count", () => {
  const result = buildBundleArtifacts([corridor("c-shared", ["1", "A"])], new Map());
  assert.equal(result.bundleFeatures.length, 1);
  assert.equal(result.bundleFeatures[0].properties.visual_feature_type, "bundle");
  assert.equal(result.bundleFeatures[0].properties.bundle_id, "bundle-00001");
  assert.deepEqual(result.bundleFeatures[0].properties.route_ids, ["1", "A"]);
  assert.equal(result.bundleLaneFeatures.length, 2);
  assert.deepEqual(
    result.bundleLaneFeatures.map((lane) => [
      lane.properties.route_id,
      lane.properties.color,
      lane.properties.visual_feature_type,
    ]),
    [
      ["1", "#EE352E", "bundle_lane"],
      ["A", "#0A84FF", "bundle_lane"],
    ],
  );
  assert.equal("unbundledFeatures" in result, false);
  const summary = candidateSummary(result);
  assert.equal(summary.remaining_unbundled_corridors, 0);
  assert.equal(summary.bundle_count, 1);
  assert.equal(summary.bundled_render_lane_count, 2);
});

test("bundle gaps mark anchors with no same-route adjacent lane", () => {
  const result = buildBundleArtifacts(
    [
      corridor("c-shared", ["N", "Q"], {
        from_anchor_id: "anchor-south",
        to_anchor_id: "anchor-north",
        junction_anchor_ids: ["anchor-south", "anchor-north"],
      }),
    ],
    new Map(),
  );
  assert.equal(result.bundleGapFeatures.length, 2);
  assert.deepEqual(
    result.bundleGapFeatures.map((gap) => [
      gap.geometry.type,
      gap.properties.marker_type,
      gap.properties.bundle_id,
      gap.properties.anchor_id,
      gap.properties.endpoint_kind,
      gap.properties.reason,
      gap.geometry.coordinates,
    ]),
    [
      [
        "Point",
        "bundle_gap",
        "bundle-00001",
        "anchor-south",
        "entry",
        "no_same_route_adjacent_bundle_lane_at_anchor",
        [-73.99, 40.75],
      ],
      [
        "Point",
        "bundle_gap",
        "bundle-00001",
        "anchor-north",
        "exit",
        "no_same_route_adjacent_bundle_lane_at_anchor",
        [-73.99, 40.76],
      ],
    ],
  );
});

test("buildBundleArtifacts is deterministic for the same corridor list", () => {
  const features = [
    corridor("c-solo", ["G"]),
    corridor("c-shared", ["N", "Q"], {
      from_anchor_id: "anchor-south",
      to_anchor_id: "anchor-north",
      junction_anchor_ids: ["anchor-south", "anchor-north"],
    }),
  ];
  const first = buildBundleArtifacts(features, new Map());
  const second = buildBundleArtifacts(features, new Map());
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.deepEqual(
    first.visualFeatures.map((feature) => [
      feature.properties.bundle_id,
      feature.properties.route_id,
      feature.properties.lane_slot_semantic,
      feature.geometry.coordinates,
    ]),
    second.visualFeatures.map((feature) => [
      feature.properties.bundle_id,
      feature.properties.route_id,
      feature.properties.lane_slot_semantic,
      feature.geometry.coordinates,
    ]),
  );
});

test("continuous-lane materialization stamps a zero runtime slot without rebaking", () => {
  const result = buildBundleArtifacts(
    [
      corridor("c-cont", ["1"], {
        bundle_materialization_role: "continuous_lane",
        lane_slot: -1,
        lane_offset_baked: true,
      }),
    ],
    new Map(),
  );
  const lane = result.bundleLaneFeatures[0];
  assert.equal(lane.properties.lane_slot_source, "physical_bundle_continuous");
  assert.equal(lane.properties.lane_slot, 0);
  assert.equal(lane.properties.render_lane_slot, 0);
  assert.equal(lane.properties.lane_slot_semantic, -1);
  assert.equal(lane.properties.lane_offset_baked, true);
  assert.deepEqual(lane.geometry.coordinates, [
    [-73.99, 40.75],
    [-73.99, 40.76],
  ]);
});

test("fanout ramps bake offset geometry and zero-zero ramps stay unbaked", () => {
  const ramped = buildBundleArtifacts(
    [
      corridor("c-fan", ["1", "A"], {
        bundle_materialization_role: "fanout",
        fanout_from_lane_slot: -1,
        fanout_to_lane_slot: 1,
      }),
    ],
    new Map(),
  );
  assert.ok(
    ramped.bundleLaneFeatures.some((lane) => lane.properties.fanout_slot_ramp_baked === true),
  );
  assert.ok(
    ramped.bundleLaneFeatures.some(
      (lane) => JSON.stringify(lane.geometry.coordinates) !== JSON.stringify([
        [-73.99, 40.75],
        [-73.99, 40.76],
      ]),
    ),
  );

  const zeroRamp = buildBundleArtifacts(
    [
      corridor("c-fan-zero", ["1", "A"], {
        bundle_materialization_role: "fanout",
        fanout_from_lane_slot: 0,
        fanout_to_lane_slot: 0,
      }),
    ],
    new Map(),
  );
  assert.equal(
    zeroRamp.bundleLaneFeatures.some((lane) => lane.properties.fanout_slot_ramp_baked === true),
    false,
  );
});

test("an adjacent same-route lane at an anchor suppresses that color's gap marker", () => {
  const result = buildBundleArtifacts(
    [
      corridor("c-shared", ["N", "Q"], {
        from_anchor_id: "anchor-south",
        to_anchor_id: "anchor-north",
        junction_anchor_ids: ["anchor-south", "anchor-north"],
      }),
      corridor("c-n-continue", ["N"], {
        from_anchor_id: "anchor-south",
        to_anchor_id: "anchor-west",
        junction_anchor_ids: ["anchor-south"],
      }),
    ],
    new Map(),
  );
  const entryGaps = result.bundleGapFeatures.filter(
    (gap) => gap.properties.endpoint_kind === "entry" && gap.properties.bundle_id === "bundle-00001",
  );
  assert.equal(entryGaps.length, 0);
  const exitGaps = result.bundleGapFeatures.filter(
    (gap) => gap.properties.endpoint_kind === "exit" && gap.properties.bundle_id === "bundle-00001",
  );
  assert.ok(exitGaps.length >= 1);
});

test("spine stamps attach to solo lanes and sortVisualLanes falls back to corridor id", () => {
  const spines = new Map([
    ["c-solo", { spine_id: "spine-1", base_spine_hash: "abc", method: "longest" }],
  ]);
  const result = buildBundleArtifacts([corridor("c-solo", ["G"])], spines);
  assert.equal(result.bundleLaneFeatures[0].properties.spine_id, "spine-1");
  assert.equal(result.bundleLaneFeatures[0].properties.base_spine_hash, "abc");
  assert.equal(result.bundleLaneFeatures[0].properties.base_geometry_selection, "longest");

  const sorted = sortVisualLanes([
    {
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[-73.99, 40.75], [-73.99, 40.76]] },
      properties: { corridor_id: "z", lane_slot_semantic: 1 },
    },
    {
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[-73.99, 40.75], [-73.99, 40.76]] },
      properties: { corridor_id: "a", lane_slot: 0 },
    },
  ]);
  assert.deepEqual(
    sorted.map((lane) => lane.properties.corridor_id),
    ["a", "z"],
  );
});

test("empty route ids still emit a solo lane with an empty route id", () => {
  const result = buildBundleArtifacts([corridor("c-empty", [])], new Map());
  assert.equal(result.bundleLaneFeatures.length, 1);
  assert.equal(result.bundleLaneFeatures[0].properties.route_id, "");
});

test("fanout ramps pick the larger absolute slot and ignore non-numeric slot values", () => {
  const ramped = buildBundleArtifacts(
    [
      corridor("c-fan-abs", ["1", "A"], {
        bundle_materialization_role: "fanout",
        fanout_from_lane_slot: 1,
        fanout_to_lane_slot: -2,
      }),
    ],
    new Map(),
  );
  const baked = ramped.bundleLaneFeatures.find((lane) => lane.properties.fanout_slot_ramp_baked === true);
  assert.ok(baked);
  assert.equal(baked.properties.lane_slot_semantic, -2);

  const invalid = buildBundleArtifacts(
    [
      corridor("c-fan-nan", ["1", "A"], {
        bundle_materialization_role: "fanout",
        fanout_from_lane_slot: "x",
        fanout_to_lane_slot: 1,
      }),
    ],
    new Map(),
  );
  assert.equal(
    invalid.bundleLaneFeatures.some((lane) => lane.properties.fanout_slot_ramp_baked === true),
    false,
  );
});

test("sortVisualLanes falls back to route_id and both colors suppress an entry gap", () => {
  const sorted = sortVisualLanes([
    {
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[-73.99, 40.75], [-73.99, 40.76]] },
      properties: { route_id: "z" },
    },
    {
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[-73.99, 40.75], [-73.99, 40.76]] },
      properties: { route_id: "a" },
    },
  ]);
  assert.deepEqual(
    sorted.map((lane) => lane.properties.route_id),
    ["a", "z"],
  );

  const result = buildBundleArtifacts(
    [
      corridor("c-shared", ["N", "Q"], {
        from_anchor_id: "anchor-south",
        to_anchor_id: "anchor-north",
        junction_anchor_ids: ["anchor-south", "anchor-north"],
      }),
      corridor("c-n-continue", ["N"], {
        from_anchor_id: "anchor-south",
        to_anchor_id: "anchor-west",
        junction_anchor_ids: ["anchor-south"],
      }),
      corridor("c-q-continue", ["Q"], {
        from_anchor_id: "anchor-south",
        to_anchor_id: "anchor-east",
        junction_anchor_ids: ["anchor-south"],
      }),
    ],
    new Map(),
  );
  const entryGaps = result.bundleGapFeatures.filter(
    (gap) => gap.properties.endpoint_kind === "entry" && gap.properties.bundle_id === "bundle-00001",
  );
  assert.equal(entryGaps.length, 0);
});
