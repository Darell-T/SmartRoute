// frontend/scripts/build/spine-validation.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  assertSpineHashConsistency,
  assertNoBogusTransitions,
  assertQContinuousInBrooklyn,
  assertOriginsForRedGreenFlatbushEastern,
} from "./spine-validation.ts";
import type { Position } from "./types.ts";

type LaneTestProperties = {
  spine_id?: string | null;
  base_spine_hash?: string | null;
  bundle_id?: string;
  physical_bundle_id?: string;
  physical_bundle_spine_hash?: string;
  from_anchor_id?: string | null;
  to_anchor_id?: string | null;
  from_stop_id?: string | null;
  to_stop_id?: string | null;
  color?: string;
  route_ids?: string[];
  color_route_ids?: string[];
  visual_feature_type?: string;
  bundle_id_from?: string;
  bundle_id_to?: string;
  classification?: string;
  lane_slot_source?: string;
  transition_classification?: string;
  length_m?: number;
  bridge?: boolean;
};

type Fixture = {
  type: string;
  geometry: { type: string; coordinates: Position[] };
  properties: LaneTestProperties;
};

function lane(props: LaneTestProperties): Fixture {
  return { type: "Feature", geometry: { type: "LineString", coordinates: [[0,0],[0,1]] }, properties: props };
}

test("assertSpineHashConsistency reports zero issues on a consistent bundle set", () => {
  const result = assertSpineHashConsistency({
    bundleLaneFeatures: [
      lane({ spine_id: "spine-a", base_spine_hash: "h1", bundle_id: "b1" }),
      lane({ spine_id: "spine-a", base_spine_hash: "h1", bundle_id: "b1" }),
      lane({ spine_id: "spine-b", base_spine_hash: "h2", bundle_id: "b2" }),
    ],
  });
  assert.equal(result.lanesWithMissingSpineId.length, 0);
  assert.equal(result.lanesWithMissingHash.length, 0);
  assert.equal(result.inconsistentGroups.length, 0);
  assert.equal(result.bundleLaneCount, 3);
});

test("assertSpineHashConsistency detects a hash mismatch on the same spine_id", () => {
  const result = assertSpineHashConsistency({
    bundleLaneFeatures: [
      lane({ spine_id: "spine-a", base_spine_hash: "h1", bundle_id: "b1" }),
      lane({ spine_id: "spine-a", base_spine_hash: "h2", bundle_id: "b1" }),
    ],
  });
  assert.equal(result.inconsistentGroups.length, 1);
  assert.equal(result.inconsistentGroups[0].spine_id, "spine-a");
  assert.equal(result.inconsistentGroups[0].expected, "h1");
  assert.equal(result.inconsistentGroups[0].got, "h2");
});

test("assertSpineHashConsistency skips bridge lanes lacking spine_id", () => {
  const result = assertSpineHashConsistency({
    bundleLaneFeatures: [
      lane({ spine_id: "spine-a", base_spine_hash: "h1", bundle_id: "b1" }),
      lane({ spine_id: null, base_spine_hash: null, bundle_id: "br1", bridge: true }),
    ],
  });
  assert.equal(result.lanesWithMissingSpineId.length, 0);
  assert.equal(result.inconsistentGroups.length, 0);
});

test("assertSpineHashConsistency flags non-bridge lanes lacking spine_id", () => {
  const result = assertSpineHashConsistency({
    bundleLaneFeatures: [
      lane({ spine_id: null, base_spine_hash: null, bundle_id: "rogue" }),
    ],
  });
  assert.equal(result.lanesWithMissingSpineId.length, 1);
  assert.equal(result.lanesWithMissingSpineId[0], "rogue");
});

test("assertSpineHashConsistency exempts branch_transition lanes lacking spine_id", () => {
  const result = assertSpineHashConsistency({
    bundleLaneFeatures: [
      lane({ spine_id: "spine-a", base_spine_hash: "h1", bundle_id: "b1" }),
      lane({
        spine_id: null,
        base_spine_hash: null,
        bundle_id: "transition-x",
        lane_slot_source: "branch_transition",
      }),
    ],
  });
  assert.equal(result.lanesWithMissingSpineId.length, 0);
  assert.equal(result.inconsistentGroups.length, 0);
});

test("assertSpineHashConsistency treats an empty camelCase lane list as consistent", () => {
  const result = assertSpineHashConsistency({ bundleLaneFeatures: [] });
  assert.equal(result.bundleLaneCount, 0);
  assert.equal(result.lanesWithMissingSpineId.length, 0);
  assert.equal(result.inconsistentGroups.length, 0);
});

test("assertSpineHashConsistency: two lanes with same physical_bundle_id and same hash => zero inconsistencies", () => {
  const result = assertSpineHashConsistency({
    bundleLaneFeatures: [
      lane({ spine_id: "spine-a", base_spine_hash: "h1", bundle_id: "b1", physical_bundle_id: "pb-00001", physical_bundle_spine_hash: "pbh1" }),
      lane({ spine_id: "spine-b", base_spine_hash: "h2", bundle_id: "b2", physical_bundle_id: "pb-00001", physical_bundle_spine_hash: "pbh1" }),
    ],
  });
  assert.equal(result.inconsistentPhysicalBundleGroups.length, 0);
});

test("assertSpineHashConsistency: two lanes with same physical_bundle_id but different hashes => one inconsistency", () => {
  const result = assertSpineHashConsistency({
    bundleLaneFeatures: [
      lane({ spine_id: "spine-a", base_spine_hash: "h1", bundle_id: "b1", physical_bundle_id: "pb-00001", physical_bundle_spine_hash: "pbh1" }),
      lane({ spine_id: "spine-b", base_spine_hash: "h2", bundle_id: "b2", physical_bundle_id: "pb-00001", physical_bundle_spine_hash: "pbh2" }),
    ],
  });
  assert.equal(result.inconsistentPhysicalBundleGroups.length, 1);
  assert.equal(result.inconsistentPhysicalBundleGroups[0].physical_bundle_id, "pb-00001");
  assert.equal(result.inconsistentPhysicalBundleGroups[0].expected, "pbh1");
  assert.equal(result.inconsistentPhysicalBundleGroups[0].got, "pbh2");
});

// ---------------------------------------------------------------------------
// assertNoBogusTransitions
// ---------------------------------------------------------------------------

function makeBundleLane(props: LaneTestProperties): Fixture {
  return { type: "Feature", geometry: { type: "LineString", coordinates: [[0, 0], [0, 1]] }, properties: props };
}

function makeTransitionLane(
  fromBid: string,
  toBid: string,
  colorRouteIds: string[],
  routeIds: string[],
  classification: string,
  lengthM: number,
  color?: string,
): Fixture {
  return makeBundleLane({
    lane_slot_source: "branch_transition",
    bundle_id: `transition-${fromBid}-${toBid}`,
    bundle_id_from: fromBid,
    bundle_id_to: toBid,
    color: color ?? "#0A84FF",
    color_route_ids: colorRouteIds,
    route_ids: routeIds,
    transition_classification: classification,
    length_m: lengthM,
  });
}

test("assertNoBogusTransitions: valid transition passes", () => {
  const t = makeTransitionLane("b1", "b2", ["A", "C"], ["A", "C"], "safe_same_route_continuation", 5);
  const index = new Map([
    ["b1", new Set(["A", "C"])],
    ["b2", new Set(["A", "C"])],
  ]);
  const result = assertNoBogusTransitions([t], index);
  assert.equal(result.passed, true);
  assert.equal(result.violations.length, 0);
});

test("assertNoBogusTransitions: transition with color absent from both corridors fails", () => {
  const t = makeTransitionLane("b1", "b2", ["Q"], ["N", "Q"], "likely_branch_exit", 10, "#FCCC0A");
  const index = new Map([
    ["b1", new Set(["A"])],
    ["b2", new Set(["1", "2"])],
  ]);
  const result = assertNoBogusTransitions([t], index);
  assert.equal(result.passed, false);
  assert.equal(result.violations.length, 1);
  assert.ok(result.violations[0].reason.includes("absent from both corridors"));
});

test("assertNoBogusTransitions: non-transition lanes are ignored", () => {
  const b = makeBundleLane({ lane_slot_source: "bundle", bundle_id: "b1", color: "#EE352E", color_route_ids: ["1"], route_ids: ["1"] });
  const index = new Map([["b1", new Set(["1"])]]);
  const result = assertNoBogusTransitions([b], index);
  assert.equal(result.passed, true);
});

// ---------------------------------------------------------------------------
// assertQContinuousInBrooklyn
// ---------------------------------------------------------------------------

function qLine(
  fromCoord: Position,
  toCoord: Position,
  bundleId: string,
  fromAnchor?: string | null,
  toAnchor?: string | null,
): Fixture {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [fromCoord, toCoord] },
    properties: {
      route_ids: ["Q"],
      bundle_id: bundleId,
      from_anchor_id: fromAnchor ?? null,
      to_anchor_id: toAnchor ?? null,
    },
  };
}

test("assertQContinuousInBrooklyn: single connected chain passes", () => {
  // Two connected Q features inside Brooklyn bbox (lat < 40.72)
  const f1 = qLine([-73.95, 40.60], [-73.96, 40.61], "q-1", "anc-a", "anc-b");
  const f2 = qLine([-73.96, 40.61], [-73.97, 40.62], "q-2", "anc-b", "anc-c");
  const result = assertQContinuousInBrooklyn([f1, f2], null);
  assert.equal(result.passed, true);
  assert.equal(result.qFeatureCount, 2);
  assert.deepEqual(result.disconnectedBundleIds, []);
});

test("assertQContinuousInBrooklyn: disconnected Q segment fails", () => {
  const f1 = qLine([-73.95, 40.60], [-73.96, 40.61], "q-1", "anc-a", "anc-b");
  const f2 = qLine([-74.00, 40.65], [-74.01, 40.66], "q-2", "anc-x", "anc-y"); // not connected
  const result = assertQContinuousInBrooklyn([f1, f2], null);
  assert.equal(result.passed, false);
  assert.equal(result.disconnectedBundleIds.length, 1);
  assert.equal(result.disconnectedBundleIds[0], "q-2");
});

test("assertQContinuousInBrooklyn: Q features above lat 40.72 excluded", () => {
  // Manhattan feature (lat > 40.72) should be excluded from the bbox
  const manhattan = qLine([-73.99, 40.73], [-73.99, 40.74], "q-manhattan", "anc-m1", "anc-m2");
  const brooklyn = qLine([-73.95, 40.60], [-73.96, 40.61], "q-brooklyn", "anc-a", "anc-b");
  const result = assertQContinuousInBrooklyn([manhattan, brooklyn], null);
  // Only brooklyn feature is in the bbox
  assert.equal(result.qFeatureCount, 1);
  assert.equal(result.passed, true);
});

test("assertQContinuousInBrooklyn: no Q features returns failed result", () => {
  const result = assertQContinuousInBrooklyn([], null);
  assert.equal(result.passed, false);
});

// ---------------------------------------------------------------------------
// assertOriginsForRedGreenFlatbushEastern
// ---------------------------------------------------------------------------

function irtLine(
  coords: Position[],
  bundleId: string,
  routeIds: string[],
  fromAnchor?: string | null,
  toAnchor?: string | null,
  fromStop?: string | null,
): Fixture {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      route_ids: routeIds,
      bundle_id: bundleId,
      from_anchor_id: fromAnchor ?? null,
      to_anchor_id: toAnchor ?? null,
      from_stop_id: fromStop ?? null,
      to_stop_id: null,
    },
  };
}

// Coordinates for Flatbush + Eastern Pkwy bbox [-73.961, 40.659, -73.940, 40.682]
// We need an upstream feature OUTSIDE the bbox that ends near a feature inside.
const _FE_OUTSIDE: Position = [-73.965, 40.655]; // just outside the bbox
const FE_INSIDE_1: Position = [-73.960, 40.661]; // just inside the bbox (lon = -73.960 > -73.961)
const FE_INSIDE_2: Position = [-73.955, 40.665];
const FE_INSIDE_3: Position = [-73.945, 40.675];

test("assertOriginsForRedGreenFlatbushEastern: feature with anchor-matched upstream passes", () => {
  // Use the same anchor key so the fromKey of f_inside matches the toKey of upstream.
  // The anchor-based key `anchor:anc-shared` will be identical for both.
  const FAR_OUTSIDE: Position = [-73.970, 40.650]; // well outside bbox
  const SHARED_COORD: Position = [-73.965, 40.655]; // outside bbox (lon < -73.961 AND lat < 40.659)
  // Both features share anchor "anc-shared" at SHARED_COORD
  const upstream = irtLine([FAR_OUTSIDE, SHARED_COORD], "irt-upstream", ["2", "3"], "anc-out", "anc-shared");
  const f_inside = irtLine([SHARED_COORD, FE_INSIDE_2], "irt-in1", ["2", "3"], "anc-shared", "anc-in2");

  // Verify SHARED_COORD is outside bbox
  assert.ok(SHARED_COORD[0] < -73.961, "shared coord should be outside bbox lon");
  assert.ok(SHARED_COORD[1] < 40.659, "shared coord should be outside bbox lat");

  const result = assertOriginsForRedGreenFlatbushEastern([upstream, f_inside]);
  // f_inside starts at SHARED_COORD which is OUTSIDE the bbox.
  // FE_INSIDE_2 is inside the bbox.
  // f_inside intersects bbox (has a coord inside bbox).
  // Its fromKey is "anchor:anc-shared" = upstream's toKey.
  // haversineM(SHARED_COORD, SHARED_COORD) = 0 <= 90m => upstream found.
  // upstream is also in region (has FE_INSIDE_2 area? no — FAR_OUTSIDE and SHARED_COORD are both outside).
  // Actually upstream will NOT be in region features (neither coord is in bbox).
  assert.equal(result.passed, true, `expected pass but got violations: ${JSON.stringify(result.violations)}`);
  assert.equal(result.missingUpstreamCount, 0);
});

test("assertOriginsForRedGreenFlatbushEastern: isolated feature in bbox fails", () => {
  // f2 starts at FE_INSIDE_3 which has no upstream within 90m from any other feature
  const f1 = irtLine([FE_INSIDE_1, FE_INSIDE_2], "irt-1", ["2", "3"], "anc-a", "anc-b");
  const f2 = irtLine([FE_INSIDE_3, [-73.942, 40.678]], "irt-isolated", ["2", "3"], "anc-z", "anc-w");
  // FE_INSIDE_2 to FE_INSIDE_3 is ~697m > 90m, so f2 has no upstream
  const result = assertOriginsForRedGreenFlatbushEastern([f1, f2]);
  assert.equal(result.passed, false);
  assert.ok(result.missingUpstreamCount > 0);
});

test("assertOriginsForRedGreenFlatbushEastern: empty feature list passes vacuously", () => {
  const result = assertOriginsForRedGreenFlatbushEastern([]);
  assert.equal(result.passed, true);
  assert.equal(result.missingUpstreamCount, 0);
});

test("assertSpineHashConsistency flags a present spine_id with a missing hash", () => {
  const result = assertSpineHashConsistency({
    bundleLaneFeatures: [
      lane({ spine_id: "spine-a", base_spine_hash: null, bundle_id: "b1" }),
    ],
  });
  assert.deepEqual(result.lanesWithMissingHash, ["spine-a/b1"]);
  assert.equal(result.inconsistentGroups.length, 0);
});

test("assertQContinuousInBrooklyn uses coordinate keys when anchors are absent", () => {
  const shared: Position = [-73.95, 40.61];
  const f1 = qLine([-73.95, 40.60], shared, "q-1");
  const f2 = qLine(shared, [-73.94, 40.62], "q-2");
  const result = assertQContinuousInBrooklyn([f1, f2], null);
  assert.equal(result.passed, true);
  assert.equal(result.qFeatureCount, 2);
  const again = assertQContinuousInBrooklyn([f1, f2], null);
  assert.deepEqual(again.disconnectedBundleIds, result.disconnectedBundleIds);
});

test("assertQContinuousInBrooklyn ignores a one-point Q geometry", () => {
  const stubCoord: Position = [-73.95, 40.60];
  const stub = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [stubCoord] },
    properties: { route_ids: ["Q"], bundle_id: "q-stub" },
  };
  const connected = qLine([-73.95, 40.60], [-73.94, 40.61], "q-ok", "anc-a", "anc-b");
  const result = assertQContinuousInBrooklyn([connected, stub], null);
  assert.equal(result.qFeatureCount, 2);
  assert.equal(result.passed, false);
  assert.deepEqual(result.disconnectedBundleIds, ["q-stub"]);
});

test("assertNoBogusTransitions treats a color present on one corridor as valid", () => {
  const t = makeTransitionLane("b1", "b2", ["A"], ["A"], "likely_branch_exit", 8);
  const index = new Map([
    ["b1", new Set(["A"])],
    ["b2", new Set(["C"])],
  ]);
  const result = assertNoBogusTransitions([t], index);
  assert.equal(result.passed, true);
});

test("assertNoBogusTransitions treats missing corridor keys and missing color routes as empty sets", () => {
  const t = makeBundleLane({
    lane_slot_source: "branch_transition",
    bundle_id: "transition-bare",
    color: "#0A84FF",
  });
  const result = assertNoBogusTransitions([t], new Map());
  assert.equal(result.passed, false);
  assert.equal(result.violations.length, 1);
});

test("assertQContinuousInBrooklyn ignores a Q with no coordinates and a missing route list", () => {
  const missingGeom = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [] },
    properties: { route_ids: ["Q"], bundle_id: "q-empty" },
  };
  const noRoutes = qLine([-73.95, 40.60], [-73.94, 40.61], "q-noroute");
  noRoutes.properties.route_ids = undefined;
  const brooklyn = qLine([-73.95, 40.60], [-73.94, 40.61], "q-ok", "anc-a", "anc-b");
  const result = assertQContinuousInBrooklyn([missingGeom, noRoutes, brooklyn], null);
  assert.equal(result.qFeatureCount, 1);
  assert.equal(result.passed, true);
});

test("assertQContinuousInBrooklyn walks a three-piece chain using coordinate keys", () => {
  const a: Position = [-73.95, 40.60];
  const b: Position = [-73.96, 40.61];
  const c: Position = [-73.97, 40.62];
  const d: Position = [-73.98, 40.63];
  const result = assertQContinuousInBrooklyn(
    [qLine(a, b, "q-1"), qLine(b, c, "q-2"), qLine(c, d, "q-3")],
    null,
  );
  assert.equal(result.passed, true);
  assert.equal(result.qFeatureCount, 3);
});

test("assertOriginsForRedGreenFlatbushEastern accepts a one-point 2/3 stub in the bbox", () => {
  const stub = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [FE_INSIDE_1] },
    properties: { route_ids: ["2", "3"], bundle_id: "irt-stub" },
  };
  const result = assertOriginsForRedGreenFlatbushEastern([stub]);
  assert.equal(result.passed, true);
});

test("assertOriginsForRedGreenFlatbushEastern matches a nearby 4/5 upstream by distance", () => {
  const farOutside: Position = [-73.970, 40.650];
  const shared: Position = [-73.965, 40.655];
  const upstream = irtLine([farOutside, shared], "green-up", ["4", "5"], "anc-out", "anc-shared");
  const inside = irtLine([shared, FE_INSIDE_2], "green-in", ["4", "5"], "anc-shared", "anc-in2");
  const result = assertOriginsForRedGreenFlatbushEastern([upstream, inside]);
  assert.equal(result.passed, true, `expected pass but got ${JSON.stringify(result.violations)}`);
});

test("assertOriginsForRedGreenFlatbushEastern matches a nearby 4/5 origin without shared anchors", () => {
  const upstreamEnd: Position = [-73.965, 40.655];
  const insideStart: Position = [-73.9644, 40.6554];
  const upstream = irtLine([ [-73.970, 40.650], upstreamEnd], "green-up-hav", ["4", "5"]);
  const inside = irtLine([insideStart, FE_INSIDE_2], "green-in-hav", ["4", "5"]);
  const result = assertOriginsForRedGreenFlatbushEastern([upstream, inside]);
  assert.equal(result.passed, true, `expected pass but got ${JSON.stringify(result.violations)}`);
});

test("assertOriginsForRedGreenFlatbushEastern skips a 2/3 feature with no route list", () => {
  const missingRoutes = irtLine([FE_INSIDE_1, FE_INSIDE_2], "irt-noroute", ["2", "3"]);
  missingRoutes.properties.route_ids = undefined;
  const result = assertOriginsForRedGreenFlatbushEastern([missingRoutes]);
  assert.equal(result.passed, true);
});

test("assertQContinuousInBrooklyn walks past a one-point neighbor without using it as a join", () => {
  const a: Position = [-73.95, 40.60];
  const b: Position = [-73.94, 40.61];
  const stub = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [a] },
    properties: { route_ids: ["Q"], bundle_id: "q-point" },
  };
  const result = assertQContinuousInBrooklyn([qLine(a, b, "q-ok"), stub], null);
  assert.equal(result.qFeatureCount, 2);
});
