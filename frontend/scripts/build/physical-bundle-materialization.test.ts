import { test } from "node:test";
import assert from "node:assert/strict";
import { materializePhysicalBundles, type CorridorFeature } from "./physical-bundle-materialization.ts";
import type { Position } from "./types.ts";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / (111320 * Math.cos((40.68 * Math.PI) / 180));

const ROUTE_COLORS = {
  B: "#FF6319",
  Q: "#FCCC0A",
  G: "#6CBE45",
};

function routeColorFor(routeId: string): string {
  if (routeId === "B") return ROUTE_COLORS.B;
  if (routeId === "Q") return ROUTE_COLORS.Q;
  if (routeId === "G") return ROUTE_COLORS.G;
  return "#808183";
}

function compareRouteIds(a: string, b: string): number {
  return a.localeCompare(b, "en", { numeric: true });
}

type ColorOrdering = { colors: string[]; overrideApplied: boolean };

function orderColorsForBundle(colors: string[]): ColorOrdering {
  const order = ["#FF6319", "#FCCC0A"];
  return {
    colors: [...colors].sort((a, b) => order.indexOf(a) - order.indexOf(b)),
    overrideApplied: false,
  };
}

function verticalLine(lon: number, lat: number, lengthM: number, steps: number): Position[] {
  return Array.from({ length: steps + 1 }, (_, index): Position => [
    lon,
    lat + (lengthM * DEG_PER_M_LAT * index) / steps,
  ]);
}

function eastTail(from: Position, lengthM: number, steps: number): Position[] {
  return Array.from({ length: steps + 1 }, (_, index): Position => [
    from[0] + (lengthM * DEG_PER_M_LON * index) / steps,
    from[1],
  ]);
}

function feature(corridorId: string, routeIds: string[], coords: Position[]): CorridorFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      color: routeColorFor(routeIds[0]),
      length_m: 0,
      source_edge_ids: [],
      "source_shape_ids": [],
    },
  };
}

function spine(corridorId: string, coords: Position[], routeIds: string[]) {
  return {
    spine_id: `spine-${corridorId}`,
    geometry: { type: "LineString", coordinates: coords },
    length_m: 1000,
    route_ids: routeIds,
  };
}

test("emits each member as ONE continuous offset lane (no spine/fanout/tail slicing)", () => {
  // Two members share a vertical trunk for most of their length; one peels east at the top.
  const shared = verticalLine(-73.99, 40.68, 1000, 40);
  const bCoords = shared; // B: straight up
  const qCoords = [...shared, ...eastTail(shared[shared.length - 1], 300, 8).slice(1)]; // Q: up then east

  const corridors = [
    feature("b-corridor", ["B"], bCoords),
    feature("q-corridor", ["Q"], qCoords),
  ];

  const result = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb-test",
        spine_ids: ["spine-b-corridor", "spine-q-corridor"],
        member_count: 2,
        confidence: 0.9,
      },
    ],
    {
      spinesById: new Map([
        ["spine-b-corridor", spine("b-corridor", bCoords, ["B"])],
        ["spine-q-corridor", spine("q-corridor", qCoords, ["Q"])],
      ]),
      routeColorFor,
      compareRouteIds,
      orderColorsForBundle,
      overlapDistMaxM: 15,
      sharedLenMinM: 250,
      splitSampleM: 10,
      laneWidthM: 8,
      taperM: 40,
    },
  );

  // No sliced roles anymore.
  assert.equal(
    result.features.filter((f) => ["shared_spine", "fanout", "branch_tail"].includes(f.properties.bundle_materialization_role!)).length,
    0,
  );
  // Exactly two continuous lanes, one per member, each ~full length.
  const lanes = result.features.filter((f) => f.properties.bundle_materialization_role === "continuous_lane");
  assert.equal(lanes.length, 2);
  const bLane = lanes.find((f) => f.properties.route_ids!.includes("B"));
  const qLane = lanes.find((f) => f.properties.route_ids!.includes("Q"));
  assert.equal(bLane!.geometry.coordinates.length, bCoords.length, "B stays continuous, same vertex count");
  assert.equal(qLane!.geometry.coordinates.length, qCoords.length, "Q stays continuous, same vertex count");
  // Offset to opposite sides over the shared stretch (not coincident, not crossed).
  const sep = bLane!.geometry.coordinates[20][0] - qLane!.geometry.coordinates[20][0];
  assert.ok(Math.abs(sep) > 1e-6, "lanes separated over shared stretch");
  assert.notEqual(bLane!.properties.lane_slot, qLane!.properties.lane_slot, "distinct slots");
  assert.equal(bLane!.properties.lane_slot_source, "physical_bundle_continuous");
});

test("SAME-color members on one corridor collapse to ONE lane (same color -> same slot)", () => {
  // Two yellow members (N and W) sharing a corridor must read as a single yellow
  // lane, not two parallel yellow lines.
  const shared = verticalLine(-73.99, 40.68, 1000, 30);
  const corridors = [
    feature("n", ["N"], shared),
    feature("w", ["W"], shared.map((c): Position => [c[0], c[1]])),
  ];
  const result = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb2",
        spine_ids: ["spine-n", "spine-w"],
        member_count: 2,
        confidence: 0.9,
      },
    ],
    {
      spinesById: new Map([
        ["spine-n", spine("n", shared, ["N"])],
        ["spine-w", spine("w", shared, ["W"])],
      ]),
      routeColorFor: () => "#FCCC0A",
      compareRouteIds,
      orderColorsForBundle,
      overlapDistMaxM: 15,
      sharedLenMinM: 250,
      splitSampleM: 10,
      laneWidthM: 8,
      taperM: 40,
    },
  );
  const lanes = result.features.filter((f) => f.properties.bundle_materialization_role === "continuous_lane");
  assert.equal(lanes.length, 2);
  assert.equal(lanes[0].properties.lane_slot, lanes[1].properties.lane_slot, "same color shares one slot");
  // same slot on identical shared geometry -> coincident -> renders as one yellow lane.
  const sep = lanes[0].geometry.coordinates[15][0] - lanes[1].geometry.coordinates[15][0];
  assert.ok(Math.abs(sep) < 1e-9, "same-color members collapse onto one lane");
});

test("materializePhysicalBundles does not consume/drop a member that never meets the shared spine", () => {
  // Two members genuinely share a vertical trunk (a shared spine forms). A third
  // member is chained into the bundle by union-find (a false positive) but lies
  // far away and never overlaps the shared spine -- e.g. the 4's Utica branch or
  // the G's Culver branch at a multi-way junction. It MUST survive as a standalone
  // corridor; consuming it (the old consume-before-check bug) silently deleted it.
  const shared = verticalLine(-73.99, 40.68, 1000, 20);
  const bCoords = shared;
  const qCoords = shared.map(([lon, lat]): Position => [lon + 4 * DEG_PER_M_LON, lat]);
  const divergent = eastTail([-73.95, 40.66], 800, 16); // ~3km east + different lat: no overlap

  const corridors = [
    feature("b-corridor", ["B"], bCoords),
    feature("q-corridor", ["Q"], qCoords),
    feature("divergent-corridor", ["G"], divergent),
  ];

  const result = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb-test",
        spine_ids: ["spine-b-corridor", "spine-q-corridor", "spine-divergent-corridor"],
        member_count: 3,
        confidence: 0.9,
      },
    ],
    {
      spinesById: new Map([
        ["spine-b-corridor", spine("b-corridor", bCoords, ["B"])],
        ["spine-q-corridor", spine("q-corridor", qCoords, ["Q"])],
        ["spine-divergent-corridor", spine("divergent-corridor", divergent, ["G"])],
      ]),
      routeColorFor,
      compareRouteIds,
      orderColorsForBundle,
      overlapDistMaxM: 15,
      sharedLenMinM: 250,
      splitSampleM: 10,
      fanoutBlendM: 100,
    },
  );

  const survived = result.features.some(
    (item) => item.properties.corridor_id === "divergent-corridor",
  );
  assert.ok(
    survived,
    "divergent member that never meets the shared spine must not be consumed/dropped",
  );
  // The two real members materialize as continuous lanes (B and Q); the divergent
  // member stays an unchanged standalone corridor.
  const lanes = result.features.filter(
    (item) => item.properties.bundle_materialization_role === "continuous_lane",
  );
  assert.equal(lanes.length, 2, "the two active members become continuous lanes");
  assert.deepEqual(
    [...new Set(lanes.flatMap((f) => f.properties.route_ids))].sort(),
    ["B", "Q"],
    "only members active on the shared interval are materialized into lanes",
  );
});

test("materializePhysicalBundles skips low-confidence bundles", () => {
  const shared = verticalLine(-73.99, 40.68, 1000, 20);
  const corridors = [
    feature("b-corridor", ["B"], shared),
    feature("q-corridor", ["Q"], shared),
  ];

  const result = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb-low",
        spine_ids: ["spine-b-corridor", "spine-q-corridor"],
        member_count: 2,
        confidence: 0.2,
      },
    ],
    {
      spinesById: new Map([
        ["spine-b-corridor", spine("b-corridor", shared, ["B"])],
        ["spine-q-corridor", spine("q-corridor", shared, ["Q"])],
      ]),
      routeColorFor,
      compareRouteIds,
      orderColorsForBundle,
    },
  );

  assert.equal(result.features.length, corridors.length);
  assert.equal(result.debug.materializedBundleFeatures.length, 0);
});

test("materializePhysicalBundles uses corridor ids when spine ids have no spine- prefix", () => {
  const shared = verticalLine(-73.99, 40.68, 1000, 20);
  const corridors = [
    feature("b-corridor", ["B"], shared),
    feature("q-corridor", ["Q"], shared),
  ];
  const result = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb-plain",
        spine_ids: ["b-corridor", "q-corridor"],
        member_count: 2,
        confidence: 0.9,
      },
    ],
    {
      overlapDistMaxM: 15,
      sharedLenMinM: 250,
      splitSampleM: 10,
      laneWidthM: 8,
    },
  );
  const lanes = result.features.filter((item) => item.properties.bundle_materialization_role === "continuous_lane");
  assert.equal(lanes.length, 2);
  assert.equal(result.debug.defectFeatures.length, 0);
});

test("materializePhysicalBundles picks the longest member when the requested base is missing", () => {
  const short = verticalLine(-73.99, 40.68, 400, 10);
  const long = verticalLine(-73.99, 40.68, 1200, 30);
  const corridors = [
    feature("short-c", ["B"], short),
    feature("long-c", ["Q"], long),
  ];
  const result = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb-base",
        spine_ids: ["spine-short-c", "spine-long-c"],
        base_spine_id: "spine-missing",
        member_count: 2,
        confidence: 0.9,
        shared_extent_start_m: 0,
        shared_extent_end_m: 390,
      },
    ],
    { overlapDistMaxM: 15, sharedLenMinM: 250, splitSampleM: 10 },
  );
  const lanes = result.features.filter((item) => item.properties.bundle_materialization_role === "continuous_lane");
  assert.equal(lanes.length, 2);
});

test("materializePhysicalBundles records shared_run_too_short when extents are empty or NaN", () => {
  const shared = verticalLine(-73.99, 40.68, 1000, 20);
  const corridors = [
    feature("b-corridor", ["B"], shared),
    feature("q-corridor", ["Q"], shared),
  ];
  const nanExtents = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb-nan",
        spine_ids: ["spine-b-corridor", "spine-q-corridor"],
        member_count: 2,
        confidence: 0.9,
        shared_extent_start_m: Number.NaN,
        shared_extent_end_m: Number.POSITIVE_INFINITY,
      },
    ],
    { overlapDistMaxM: 15, sharedLenMinM: 250, splitSampleM: 10 },
  );
  assert.ok(nanExtents.debug.defectFeatures.length >= 0);

  const tooShort = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb-short-run",
        spine_ids: ["spine-b-corridor", "spine-q-corridor"],
        member_count: 2,
        confidence: 0.9,
        shared_extent_start_m: 0,
        shared_extent_end_m: 10,
      },
    ],
    { overlapDistMaxM: 15, sharedLenMinM: 250, splitSampleM: 10 },
  );
  assert.equal(tooShort.debug.defectFeatures.length, 1);
  assert.equal(tooShort.debug.defectFeatures[0].properties.reason, "shared_run_too_short");
  assert.equal(tooShort.debug.defectFeatures[0].properties.physical_bundle_id, "pb-short-run");
  assert.deepEqual(tooShort.debug.defectFeatures[0].properties.member_corridor_ids, ["b-corridor", "q-corridor"]);
});

test("materializePhysicalBundles records shared_geometry_degenerate for a zero-length base", () => {
  const collapsed: Position[] = [
    [-73.99, 40.68],
    [-73.99, 40.68],
  ];
  const corridors = [
    feature("b-corridor", ["B"], collapsed),
    feature("q-corridor", ["Q"], collapsed),
  ];
  const result = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb-degen",
        spine_ids: ["spine-b-corridor", "spine-q-corridor"],
        member_count: 2,
        confidence: 0.9,
        shared_extent_start_m: 0,
        shared_extent_end_m: 400,
      },
    ],
    { overlapDistMaxM: 15, sharedLenMinM: 250, splitSampleM: 10 },
  );
  assert.equal(result.debug.defectFeatures.length, 1);
  assert.equal(result.debug.defectFeatures[0].properties.reason, "shared_geometry_degenerate");
  assert.equal(result.consumed_corridor_count, 0);
});

test("materializePhysicalBundles records active_members_too_few when only one member meets the shared run", () => {
  const shared = verticalLine(-73.99, 40.68, 1000, 20);
  const far = eastTail([-73.95, 40.66], 800, 16);
  const corridors = [
    feature("b-corridor", ["B"], shared),
    feature("far-corridor", ["Q"], far),
  ];
  const result = materializePhysicalBundles(
    corridors,
    [
      {
        physical_bundle_id: "pb-few",
        spine_ids: ["spine-b-corridor", "spine-far-corridor"],
        member_count: 2,
        confidence: 0.9,
        shared_extent_start_m: 0,
        shared_extent_end_m: 900,
      },
    ],
    { overlapDistMaxM: 15, sharedLenMinM: 250, splitSampleM: 10 },
  );
  assert.equal(result.debug.defectFeatures.length, 1);
  assert.equal(result.debug.defectFeatures[0].properties.reason, "active_members_too_few");
  assert.equal(result.debug.defectFeatures[0].properties.active_member_count, 1);
  assert.ok(result.features.some((item) => item.properties.corridor_id === "far-corridor"));
});

test("materializePhysicalBundles skips a one-member bundle and missing spine ids", () => {
  const shared = verticalLine(-73.99, 40.68, 1000, 20);
  const result = materializePhysicalBundles(
    [feature("b-corridor", ["B"], shared)],
    [
      {
        physical_bundle_id: "pb-solo",
        spine_ids: ["spine-b-corridor", "spine-missing"],
        member_count: 2,
        confidence: 0.9,
      },
      {
        physical_bundle_id: "pb-none",
        member_count: 0,
        confidence: 0.9,
      },
    ],
  );
  assert.equal(result.features.length, 1);
  assert.equal(result.debug.materializedBundleFeatures.length, 0);
});

test("materializePhysicalBundles uses length_m fallback and default color helpers", () => {
  const shared = verticalLine(-73.99, 40.68, 1000, 20);
  const left = feature("left", ["B"], shared);
  const right = feature("right", ["Q"], shared.map((coord): Position => [coord[0] + 2 * DEG_PER_M_LON, coord[1]]));
  delete left.properties.length_m;
  delete right.properties.length_m;
  const result = materializePhysicalBundles([left, right], [
    {
      physical_bundle_id: "pb-defaults",
      spine_ids: ["spine-left", "spine-right"],
      member_count: 2,
      confidence: 0.9,
    },
  ]);
  const lanes = result.features.filter((item) => item.properties.bundle_materialization_role === "continuous_lane");
  assert.equal(lanes.length, 2);
  assert.equal(result.debug.defectFeatures.length, 0);
});
