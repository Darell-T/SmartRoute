import { test } from "node:test";
import assert from "node:assert/strict";
import { materializePhysicalBundles, type CorridorFeature } from "./physical-bundle-materialization.ts";
import type { Position } from "./types.ts";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / (111320 * Math.cos((40.68 * Math.PI) / 180));

const ROUTE_COLORS: Record<string, string> = {
  B: "#FF6319",
  Q: "#FCCC0A",
  G: "#6CBE45",
};

function routeColorFor(routeId: string): string {
  return ROUTE_COLORS[routeId] ?? "#808183";
}

function compareRouteIds(a: string, b: string): number {
  return a.localeCompare(b, "en", { numeric: true });
}

function orderColorsForBundle(colors: string[]): { colors: string[]; overrideApplied: boolean } {
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
      source_shape_ids: [],
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
    feature("w", ["W"], shared.map((c) => [...c] as Position)),
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
