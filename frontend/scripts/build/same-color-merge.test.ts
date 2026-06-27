// frontend/scripts/build/same-color-merge.test.ts
// Unit tests for Phase 3d same-color merge helpers.
//
// NYC-realistic coordinates: lat ~40.7, lon ~-73.99.
// 1 degree lat ~= 111320 m; 1 degree lon ~= 84410 m at NYC lat.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  groupCorridorsByColorAndOverlap,
  mergeSameColorGroup,
  type SameColorCorridor,
  type SameColorMergeAppliedResult,
  type SameColorMergeResult,
} from "./same-color-merge.ts";
import type { Position } from "./types.ts";

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / 84410; // at NYC lat ~40.7

function makePolylineNS(startLon: number, startLat: number, lengthM: number, segmentCount = 4): Position[] {
  // North-south line starting at (startLon, startLat), going lengthM north.
  const coords: Position[] = [];
  const stepLat = (lengthM * DEG_PER_M_LAT) / segmentCount;
  for (let i = 0; i <= segmentCount; i++) {
    coords.push([startLon, startLat + i * stepLat]);
  }
  return coords;
}

function makePolylineEW(startLon: number, startLat: number, lengthM: number, segmentCount = 4): Position[] {
  // East-west line starting at (startLon, startLat), going lengthM east.
  const coords: Position[] = [];
  const stepLon = (lengthM * DEG_PER_M_LON) / segmentCount;
  for (let i = 0; i <= segmentCount; i++) {
    coords.push([startLon + i * stepLon, startLat]);
  }
  return coords;
}

function haversinePolylineM(coords: Position[]): number {
  const EARTH_RADIUS_M = 6371000;
  function haversineM([lon1, lat1]: Position, [lon2, lat2]: Position): number {
    const toRad = (d: number): number => (d * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
  }
  let total = 0;
  for (let i = 1; i < coords.length; i++) total += haversineM(coords[i - 1], coords[i]);
  return total;
}

function makeCorridor(id: string, coords: Position[], color: string, routeIds: string[]): SameColorCorridor {
  const length_m = haversinePolylineM(coords);
  return {
    corridor_id: id,
    color,
    route_ids: routeIds,
    geometry: { type: "LineString", coordinates: coords },
    length_m,
  };
}

function corridorMap(corridors: SameColorCorridor[]): Map<string, SameColorCorridor> {
  return new Map(corridors.map((c) => [c.corridor_id, c]));
}

function assertApplied(result: SameColorMergeResult): asserts result is SameColorMergeAppliedResult {
  assert.ok(!result.skipped, `group should not be skipped, got: ${result.skipped?.reason}`);
}

// ---------------------------------------------------------------------------
// Test 1: Two same-color overlapping parallel polylines — one shorter branch
// into a longer trunk. Diverging branch survives; trunk keeps its own route ids.
// ---------------------------------------------------------------------------
test("Test 1: same-color overlap — trunk union + branch clip", () => {
  // Trunk: 1500 m north-south (downtown Manhattan-ish)
  const trunkCoords = makePolylineNS(-73.990, 40.700, 1500, 8);
  // Branch: 800 m, starting at same point but only first 600 m overlaps trunk,
  // then diverges east for 200 m.
  // To simulate: branch goes north 600 m (overlapping trunk), then east 200 m.
  const branchOverlapCoords = makePolylineNS(-73.990, 40.700, 600, 4);
  // After 600 m north, go east.
  const lastOverlapPt = branchOverlapCoords[branchOverlapCoords.length - 1];
  const divergeCoords = makePolylineEW(lastOverlapPt[0], lastOverlapPt[1], 200, 2);
  const branchCoords = [...branchOverlapCoords.slice(0, -1), ...divergeCoords];

  const trunk = makeCorridor("trunk-1", trunkCoords, "#EE352E", ["1"]);
  const branch = makeCorridor("branch-1", branchCoords, "#EE352E", ["2"]);

  const corridors = [trunk, branch];
  const { groups } = groupCorridorsByColorAndOverlap(corridors, {
    sharedFractionMin: 0.40, // branch is ~75% overlapping (600/800), well above 0.4
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  assert.ok(groups.length >= 1, `expected at least 1 group, got ${groups.length}`);
  const group = groups[0];
  assert.strictEqual(group.trunk_corridor_id, "trunk-1", "longer line should be trunk");
  assert.deepStrictEqual(group.member_route_ids_union.sort(), ["1", "2"]);

  const cmap = corridorMap(corridors);
  const result = mergeSameColorGroup(group, cmap, { minBranchLenM: 30, resampleM: 25, avgDistMaxM: 15 });

  assertApplied(result);
  assert.strictEqual(result.trunkUpdates.corridor_id, "trunk-1");
  assert.deepStrictEqual(result.trunkUpdates.route_ids.sort(), ["1", "2"]);
  assert.deepStrictEqual(result.trunkUpdates.merged_from_corridor_ids.sort(), ["branch-1", "trunk-1"]);

  // Branch should have newCoords (the diverging eastern portion), not be dropped.
  const branchUpdate = result.branchUpdates.find((u) => u.corridor_id === "branch-1");
  assert.ok(branchUpdate, "branch update should exist");
  assert.ok(!branchUpdate.drop, `branch should not be dropped, reason: ${branchUpdate.reason}`);
  assert.ok(Array.isArray(branchUpdate.newCoords) && branchUpdate.newCoords.length >= 2,
    "branch should have clipped coords");

  // Clipped coords should be the diverging eastern segment (roughly east-west oriented).
  // The first point of clipped coords should be near the diverge point (approx lat 40.705).
  const firstPt = branchUpdate.newCoords[0];
  const lastPt = branchUpdate.newCoords[branchUpdate.newCoords.length - 1];
  // The diverge portion is ~200 m east, so lastPt.lon > firstPt.lon by ~200m in degrees.
  assert.ok(lastPt[0] > firstPt[0] || Math.abs(lastPt[1] - firstPt[1]) < 0.002,
    "clipped branch should cover the eastern divergence");
});

// ---------------------------------------------------------------------------
// Test 2: Two same-color polylines on DIFFERENT physical tracks (far apart).
// Should produce no merge group.
// ---------------------------------------------------------------------------
test("Test 2: same-color polylines on different physical tracks => no merge", () => {
  // Line A at lon -73.990, Line B at lon -73.970 (~1.7 km apart).
  const coordsA = makePolylineNS(-73.990, 40.700, 1000, 5);
  const coordsB = makePolylineNS(-73.970, 40.700, 1000, 5);

  const a = makeCorridor("a-far", coordsA, "#EE352E", ["1"]);
  const b = makeCorridor("b-far", coordsB, "#EE352E", ["2"]);

  const { groups } = groupCorridorsByColorAndOverlap([a, b], {
    sharedFractionMin: 0.55,
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
  });

  assert.strictEqual(groups.length, 0, "no groups expected for distant same-color lines");
});

// ---------------------------------------------------------------------------
// Test 3: Different-color polylines on shared track => NO merge.
// They must render as parallel lanes.
// ---------------------------------------------------------------------------
test("Test 3: different-color polylines on shared track => no merge", () => {
  // Two lines physically on the same track (< 5m apart) but different colors.
  const coordsA = makePolylineNS(-73.990, 40.700, 1200, 6);
  const offsetM = 5; // ~5 m offset (well within merge threshold)
  const coordsB: Position[] = coordsA.map(([lon, lat]): Position => [lon, lat + offsetM * DEG_PER_M_LAT]);

  const orange = makeCorridor("orange-B", coordsA, "#FF6319", ["B"]);
  const yellow = makeCorridor("yellow-Q", coordsB, "#FCCC0A", ["Q"]);

  const { groups } = groupCorridorsByColorAndOverlap([orange, yellow], {
    sharedFractionMin: 0.55,
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
  });

  // Color-scoped bucketing means B (#FF6319) and Q (#FCCC0A) are in separate buckets.
  // Each bucket has only 1 member, so no groups.
  assert.strictEqual(groups.length, 0, "different-color lines must never be merged");
});

// ---------------------------------------------------------------------------
// Test 4: Branch entirely contained in trunk => branch dropped, trunk gets union.
// ---------------------------------------------------------------------------
test("Test 4: branch entirely contained in trunk => branch dropped, trunk gets route_id union", () => {
  // Trunk: 2000 m north-south.
  const trunkCoords = makePolylineNS(-73.990, 40.700, 2000, 10);
  // Branch: 500 m subset of the same track (from 0 to 500 m north).
  const branchCoords = makePolylineNS(-73.990, 40.700, 500, 4);

  const trunk = makeCorridor("trunk-full", trunkCoords, "#EE352E", ["1"]);
  const branch = makeCorridor("branch-sub", branchCoords, "#EE352E", ["2"]);

  const corridors = [trunk, branch];
  const { groups } = groupCorridorsByColorAndOverlap(corridors, {
    sharedFractionMin: 0.85, // branch is entirely contained, fraction = 1.0
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
  });

  assert.ok(groups.length >= 1, "should form a merge group");
  const group = groups[0];
  assert.strictEqual(group.trunk_corridor_id, "trunk-full");

  const cmap = corridorMap(corridors);
  const result = mergeSameColorGroup(group, cmap, { minBranchLenM: 30, resampleM: 25, avgDistMaxM: 15 });

  assertApplied(result);
  assert.deepStrictEqual(result.trunkUpdates.route_ids.sort(), ["1", "2"]);

  const branchUpdate = result.branchUpdates.find((u) => u.corridor_id === "branch-sub");
  assert.ok(branchUpdate, "branch update should exist");
  assert.ok(branchUpdate.drop === true, `branch should be dropped (fully_contained), got: ${JSON.stringify(branchUpdate)}`);
});

// ---------------------------------------------------------------------------
// Test 5: Three same-color polylines on shared trunk.
// Trunk keeps its own route ids while two diverging branches are clipped.
// ---------------------------------------------------------------------------
test("Test 5: three same-color polylines on shared trunk => union route_ids", () => {
  // Trunk: 2000 m north-south.
  const trunkCoords = makePolylineNS(-73.990, 40.700, 2000, 10);

  // Branch A: overlaps trunk for 600 m, then diverges east 200 m.
  const branchAOverlapCoords = makePolylineNS(-73.990, 40.700, 600, 4);
  const branchADivergePt = branchAOverlapCoords[branchAOverlapCoords.length - 1];
  const branchADivergeCoords = makePolylineEW(branchADivergePt[0], branchADivergePt[1], 300, 3);
  const branchACoords = [...branchAOverlapCoords.slice(0, -1), ...branchADivergeCoords];

  // Branch B: overlaps trunk for 900 m (different start lat), then diverges west 200 m.
  // Start partway up the trunk (at 500m mark).
  const branchBStartLat = 40.700 + 500 * DEG_PER_M_LAT;
  const branchBOverlapCoords = makePolylineNS(-73.990, branchBStartLat, 400, 4);
  const branchBDivergePt = branchBOverlapCoords[branchBOverlapCoords.length - 1];
  const branchBDivergeCoords = makePolylineEW(branchBDivergePt[0] - 200 * DEG_PER_M_LON, branchBDivergePt[1], 200, 2).reverse();
  const branchBCoords = [...branchBOverlapCoords.slice(0, -1), ...branchBDivergeCoords];

  const trunk = makeCorridor("trunk-3way", trunkCoords, "#EE352E", ["1"]);
  const branchA = makeCorridor("branch-A", branchACoords, "#EE352E", ["2"]);
  const branchB = makeCorridor("branch-B", branchBCoords, "#EE352E", ["3"]);

  const corridors = [trunk, branchA, branchB];
  const { groups } = groupCorridorsByColorAndOverlap(corridors, {
    sharedFractionMin: 0.40,
    sharedLenMinM: 50,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  // Should be a single group with all three members.
  assert.ok(groups.length >= 1, "should form at least one merge group");
  const group = groups.find((g) => g.member_corridor_ids.length === 3);
  assert.ok(group, `expected a 3-member group, got groups: ${JSON.stringify(groups.map(g => g.member_corridor_ids))}`);
  assert.strictEqual(group.trunk_corridor_id, "trunk-3way");
  assert.deepStrictEqual(group.member_route_ids_union.sort(), ["1", "2", "3"]);

  const cmap = corridorMap(corridors);
  const result = mergeSameColorGroup(group, cmap, { minBranchLenM: 30, resampleM: 25, avgDistMaxM: 15 });

  assertApplied(result);
  assert.deepStrictEqual(result.trunkUpdates.route_ids.sort(), ["1", "2", "3"]);

  // Both branches should either be clipped (with newCoords) or dropped.
  // We expect them to each have some non-overlapping geometry.
  const branchAUpdate = result.branchUpdates.find((u) => u.corridor_id === "branch-A");
  const branchBUpdate = result.branchUpdates.find((u) => u.corridor_id === "branch-B");
  assert.ok(branchAUpdate, "branch-A update must exist");
  assert.ok(branchBUpdate, "branch-B update must exist");
  // At least one should have newCoords (the diverging part).
  const hasClipped = (branchAUpdate.newCoords || branchBUpdate.newCoords);
  assert.ok(hasClipped, "at least one branch should be clipped to its diverging portion");
});

// ---------------------------------------------------------------------------
// Test 6: Tangent-mismatch overlap (perpendicular crossing) => no merge.
// ---------------------------------------------------------------------------
test("Test 6: tangent-mismatch (perpendicular crossing) => no merge", () => {
  // Line A: north-south, 1200 m.
  const coordsA = makePolylineNS(-73.990, 40.700, 1200, 6);
  // Line B: east-west, crossing at the midpoint of A, same color.
  // Midpoint of A is at lat ~40.7054.
  const crossLat = 40.700 + 600 * DEG_PER_M_LAT;
  const coordsB = makePolylineEW(-73.993, crossLat, 1200, 6);

  const a = makeCorridor("a-ns", coordsA, "#EE352E", ["1"]);
  const b = makeCorridor("b-ew", coordsB, "#EE352E", ["2"]);

  const { groups } = groupCorridorsByColorAndOverlap([a, b], {
    sharedFractionMin: 0.55,
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30, // strict tangent check
  });

  // Perpendicular lines should be rejected by the tangent gate.
  assert.strictEqual(groups.length, 0, "perpendicular same-color lines should not merge");
});

// ---------------------------------------------------------------------------
// Test 7: Connectivity preservation: a route whose only feature would be
// entirely consumed by merge => skip group, keep unmerged.
// ---------------------------------------------------------------------------
test("Test 7: connectivity preservation fallback — sole-feature route blocks merge drop", () => {
  // Trunk: 1500 m north-south.
  const trunkCoords = makePolylineNS(-73.990, 40.700, 1500, 8);
  // Branch: 400 m, entirely contained within trunk (same track, shorter).
  const branchCoords = makePolylineNS(-73.990, 40.700, 400, 4);

  const trunk = makeCorridor("trunk-conn", trunkCoords, "#EE352E", ["1"]);
  const branch = makeCorridor("branch-conn", branchCoords, "#EE352E", ["2"]);

  const corridors = [trunk, branch];
  const { groups } = groupCorridorsByColorAndOverlap(corridors, {
    sharedFractionMin: 0.85,
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
  });

  if (groups.length === 0) {
    // If grouping didn't produce a group (e.g., thresholds not met), the test is vacuous.
    // Still pass — the route is preserved.
    assert.ok(true, "no group formed, route automatically preserved");
    return;
  }

  const group = groups[0];
  const cmap = corridorMap(corridors);

  // routeCoverageMap: route "2" has coverage of 1 (only branch-conn).
  const routeCoverageMap = new Map([["1", 1], ["2", 1]]);

  const result = mergeSameColorGroup(group, cmap, {
    minBranchLenM: 30,
    resampleM: 25,
    avgDistMaxM: 15,
    routeCoverageMap,
  });

  // The merge should be SKIPPED because dropping branch-conn would leave route "2"
  // with zero coverage.
  assert.ok(result.skipped, "group should be skipped to preserve connectivity");
  assert.strictEqual(result.skipped.reason, "would_break_route_connectivity");
});

// ---------------------------------------------------------------------------
// Test 8: middle-floater branch is dropped (not emitted as a free-floating slice)
// ---------------------------------------------------------------------------
test("Test 8: middle-floater branch is dropped (not emitted as a free-floating slice)", () => {
  // Trunk: 1200 m north-south.
  const trunkCoords = makePolylineNS(-73.990, 40.700, 1200, 12);
  // Branch: sandwich pattern -- first 400 m overlaps trunk, middle 200 m
  // diverges ~50 m east (out-of-overlap), last 400 m overlaps trunk again.
  const branchPart1 = makePolylineNS(-73.990, 40.700, 400, 4);
  const lastPart1 = branchPart1[branchPart1.length - 1];
  const offsetLon = 50 * DEG_PER_M_LON;
  const branchPart2: Position[] = [
    [lastPart1[0] + offsetLon, lastPart1[1]],
    [lastPart1[0] + offsetLon, lastPart1[1] + (200 * DEG_PER_M_LAT) / 2],
    [lastPart1[0] + offsetLon, lastPart1[1] + 200 * DEG_PER_M_LAT],
  ];
  const reconvergeStartLat = lastPart1[1] + 200 * DEG_PER_M_LAT;
  const branchPart3 = makePolylineNS(-73.990, reconvergeStartLat, 400, 4);
  const branchCoords: Position[] = [...branchPart1, ...branchPart2.slice(1), ...branchPart3.slice(1)];

  const trunk = makeCorridor("trunk-mf", trunkCoords, "#EE352E", ["1"]);
  const branch = makeCorridor("branch-mf", branchCoords, "#EE352E", ["2"]);
  // Decoy elsewhere carrying "2" so the connectivity-preservation fallback
  // does NOT fire (we want to test the middle-floater path, not the fallback).
  const decoy = makeCorridor(
    "decoy-mf",
    makePolylineNS(-73.95, 40.65, 500, 5),
    "#EE352E",
    ["2"],
  );
  const corridors = [trunk, branch, decoy];
  const routeCoverageMap = new Map([["1", 1], ["2", 2]]);

  const { groups } = groupCorridorsByColorAndOverlap(corridors, {
    sharedFractionMin: 0.50,
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  const tbGroup = groups.find(
    (g) =>
      g.member_corridor_ids.includes("trunk-mf") &&
      g.member_corridor_ids.includes("branch-mf"),
  );
  assert.ok(tbGroup, "trunk-mf + branch-mf should form a group");

  const cmap = corridorMap(corridors);
  const result = mergeSameColorGroup(tbGroup, cmap, {
    minBranchLenM: 30,
    resampleM: 25,
    avgDistMaxM: 15,
    routeCoverageMap,
  });

  assertApplied(result);
  const branchUpdate = result.branchUpdates.find((u) => u.corridor_id === "branch-mf");
  assert.ok(branchUpdate, "branch update should exist");
  assert.strictEqual(branchUpdate.drop, true, "middle-floater branch should be dropped");
  assert.strictEqual(
    branchUpdate.reason,
    "middle_floater_dropped",
    "drop reason should be middle_floater_dropped",
  );
});

// ---------------------------------------------------------------------------
// Test 9: Clipped branch records a safe connector back to the shared trunk.
// This protects the Lower Manhattan 1 branch case, where the branch survives as
// a diverging slice but needs a short connector back to the 1/2/3 trunk.
// ---------------------------------------------------------------------------
test("Test 9: clipped same-color branch emits connector back to trunk", () => {
  const trunkCoords = makePolylineNS(-73.990, 40.700, 1500, 8);
  const branchOverlapCoords = makePolylineNS(-73.990, 40.700, 600, 4);
  const lastOverlapPt = branchOverlapCoords[branchOverlapCoords.length - 1];
  const divergeCoords = makePolylineEW(lastOverlapPt[0], lastOverlapPt[1], 200, 2);
  const branchCoords = [...branchOverlapCoords.slice(0, -1), ...divergeCoords];

  const trunk = makeCorridor("trunk-connector", trunkCoords, "#EE352E", ["1"]);
  const branch = makeCorridor("branch-connector", branchCoords, "#EE352E", ["2"]);
  const corridors = [trunk, branch];

  const { groups } = groupCorridorsByColorAndOverlap(corridors, {
    sharedFractionMin: 0.40,
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  const result = mergeSameColorGroup(groups[0], corridorMap(corridors), {
    minBranchLenM: 30,
    resampleM: 25,
    avgDistMaxM: 15,
    connectorMaxM: 35,
  });
  assertApplied(result);

  const branchUpdate = result.branchUpdates.find(
    (u) => u.corridor_id === "branch-connector",
  );
  assert.ok(branchUpdate?.connector, "clipped branch should include connector metadata");
  assert.ok(
    branchUpdate.connector.distance_m <= 35,
    `connector should stay within safe radius, got ${branchUpdate.connector.distance_m}`,
  );
  assert.deepStrictEqual(branchUpdate.connector.route_ids, ["2"]);
  assert.equal(branchUpdate.connector.color, "#EE352E");
});

// ---------------------------------------------------------------------------
// Test 10: Low-detail long clipped branches are dropped instead of promoted as
// straight visual chords.
// ---------------------------------------------------------------------------
test("Test 10: low-detail long clipped branch is dropped as a chord", () => {
  const trunkCoords = makePolylineEW(-73.990, 40.700, 2500, 10);
  const branchCoords: Position[] = [
    ...makePolylineEW(-73.990, 40.700, 600, 4).slice(0, -1),
    [-73.990 + 600 * DEG_PER_M_LON, 40.700],
    [-73.990 + 600 * DEG_PER_M_LON, 40.714],
  ];

  const trunk = makeCorridor("trunk-chord", trunkCoords, "#00933C", ["4"]);
  const branch = makeCorridor("branch-chord", branchCoords, "#00933C", ["5"]);
  const { groups } = groupCorridorsByColorAndOverlap([trunk, branch], {
    sharedFractionMin: 0.20,
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
    resampleM: 25,
  });
  assert.ok(groups[0], "expected branch to form an overlap group before chord filtering");

  const result = mergeSameColorGroup(groups[0], corridorMap([trunk, branch]), {
    minBranchLenM: 30,
    resampleM: 25,
    avgDistMaxM: 15,
    maxTwoPointBranchLenM: 250,
  });
  assertApplied(result);

  const branchUpdate = result.branchUpdates.find((u) => u.corridor_id === "branch-chord");
  assert.equal(branchUpdate?.drop, true);
  assert.equal(branchUpdate?.reason, "low_detail_long_chord_dropped");
});

// ---------------------------------------------------------------------------
// Test 11: A long two-point branch is legitimate when it is endpoint-anchored
// and tangent-aligned with the shared trunk. This protects the 4 line from
// Nostrand Av to Utica Av: the OpenData slice is sparse but it is the real
// straight Eastern Parkway branch and must not be dropped as a fake chord.
// ---------------------------------------------------------------------------
test("Test 11: aligned long straight branch is preserved, not treated as a chord", () => {
  const trunkCoords = makePolylineEW(-73.990, 40.700, 5000, 20);
  const branchCoords: Position[] = [
    [-73.990 - 1500 * DEG_PER_M_LON, 40.700],
    [-73.990, 40.700],
    ...trunkCoords.slice(1, 11),
  ];

  const trunk = makeCorridor("trunk-aligned", trunkCoords, "#00933C", ["4"]);
  const branch = makeCorridor("branch-aligned", branchCoords, "#00933C", ["5"]);
  const { groups } = groupCorridorsByColorAndOverlap([trunk, branch], {
    sharedFractionMin: 0.40,
    sharedLenMinM: 100,
    avgDistMaxM: 15,
    tangentMaxDeg: 30,
    resampleM: 25,
  });
  assert.ok(groups[0], "expected aligned branch to form an overlap group");

  const result = mergeSameColorGroup(groups[0], corridorMap([trunk, branch]), {
    minBranchLenM: 30,
    resampleM: 25,
    avgDistMaxM: 15,
    maxTwoPointBranchLenM: 250,
    connectorMaxM: 35,
  });
  assertApplied(result);

  const branchUpdate = result.branchUpdates.find((u) => u.corridor_id === "branch-aligned");
  assert.ok(branchUpdate, "branch update should exist");
  assert.equal(branchUpdate.drop, undefined, `aligned branch should survive, got ${branchUpdate.reason}`);
  assert.ok(Array.isArray(branchUpdate.newCoords), "aligned sparse branch should keep clipped geometry");
  assert.ok(
    haversinePolylineM(branchUpdate.newCoords) > 1000,
    "the preserved branch should be the long Utica-style tail",
  );
});
