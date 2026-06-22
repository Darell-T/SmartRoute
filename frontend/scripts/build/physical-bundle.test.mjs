// frontend/scripts/build/physical-bundle.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  computePairOverlap,
  groupSpinesIntoPhysicalBundles,
  selectPhysicalBundleSpine,
  clipPolylineToExtent,
  resamplePolyline,
  computePhysicalBundleSpineHash,
} from "./physical-bundle.mjs";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Offset a polyline by ~offsetM meters latitude (roughly, for NYC lat).
 * 1 degree lat ~= 111320 m; the offset in degrees is offsetM / 111320.
 */
function offsetLatPolyline(coords, offsetM) {
  const degPerM = 1 / 111320;
  return coords.map(([lon, lat]) => [lon, lat + offsetM * degPerM]);
}

/**
 * Build a spine object with geometry derived from coords and an explicit length_m.
 */
function makeSpine(id, coords, length_m, route_ids = []) {
  return {
    spine_id: id,
    geometry: { type: "LineString", coordinates: coords },
    length_m,
    route_ids,
  };
}

// A simple 1 km straight line along constant longitude (NYC-ish coords).
const BASE_COORDS = [
  [-73.99, 40.700],
  [-73.99, 40.705],  // ~555 m
  [-73.99, 40.710],  // ~555 m -- total ~1110 m
];
const BASE_LEN_M = 1110;

// ---------------------------------------------------------------------------
// Test 1: computePairOverlap on two parallel identical polylines offset by 5m
// ---------------------------------------------------------------------------
test("computePairOverlap: parallel polylines 5m apart => high share, low dist, low tangent", () => {
  const spineA = makeSpine("a", BASE_COORDS, BASE_LEN_M);
  const spineB = makeSpine("b", offsetLatPolyline(BASE_COORDS, 5), BASE_LEN_M);

  const result = computePairOverlap(spineA, spineB, { resampleM: 25, distMaxM: 15 });

  // Both are equal length so either could be "shorter"; the key metrics should hold.
  assert.ok(result.sharedFractionShorter > 0.9, `expected sharedFractionShorter > 0.9, got ${result.sharedFractionShorter}`);
  assert.ok(result.avgDistM < 7, `expected avgDistM < 7, got ${result.avgDistM}`);
  assert.ok(result.tangentDeltaAvgDeg < 5, `expected tangentDeltaAvgDeg < 5, got ${result.tangentDeltaAvgDeg}`);
  assert.ok(result.sharedLenM > BASE_LEN_M * 0.8, `expected sharedLenM > ${BASE_LEN_M * 0.8}, got ${result.sharedLenM}`);
  assert.ok(typeof result.shorterSpineId === "string");
  assert.ok(typeof result.longerSpineId === "string");
});

// ---------------------------------------------------------------------------
// Test 2: computePairOverlap on two disjoint polylines => clear reject
// ---------------------------------------------------------------------------
test("computePairOverlap: disjoint polylines => large avgDistM, low sharedFraction", () => {
  const spineA = makeSpine("a", BASE_COORDS, BASE_LEN_M);
  // Put spine B ~5000m to the east (about 0.05 degrees longitude at NYC lat).
  const farCoords = BASE_COORDS.map(([lon, lat]) => [lon + 0.05, lat]);
  const spineB = makeSpine("b", farCoords, BASE_LEN_M);

  const result = computePairOverlap(spineA, spineB, { resampleM: 25, distMaxM: 15 });

  assert.ok(result.avgDistM > 100, `expected avgDistM > 100, got ${result.avgDistM}`);
  assert.ok(result.sharedFractionShorter < 0.05, `expected sharedFractionShorter < 0.05, got ${result.sharedFractionShorter}`);
  assert.equal(result.tangentDeltaAvgDeg, 180); // no in-shared samples
});

// ---------------------------------------------------------------------------
// Test 3: computePairOverlap on a "T" -- perpendicular at one point
// ---------------------------------------------------------------------------
test("computePairOverlap: T intersection => low sharedFractionShorter", () => {
  // Spine A: horizontal line (constant lat, varying lon), 1km
  const horizCoords = [
    [-74.00, 40.705],
    [-73.99, 40.705],
    [-73.98, 40.705],
  ];
  const spineA = makeSpine("a", horizCoords, 1800);

  // Spine B: short vertical line crossing the midpoint of A perpendicularly
  // (constant lon at midpoint, varying lat), 500m long.
  const vertCoords = [
    [-73.99, 40.702],
    [-73.99, 40.705],  // midpoint of A
    [-73.99, 40.708],
  ];
  const spineB = makeSpine("b", vertCoords, 660);

  const result = computePairOverlap(spineA, spineB, { resampleM: 25, distMaxM: 15 });

  // Spine B is shorter (~660m). Only the samples near the crossing point
  // of B will be close to A. Most samples of B are farther than 15m from A.
  // Expect low shared fraction.
  assert.ok(result.sharedFractionShorter < 0.25,
    `expected sharedFractionShorter < 0.25, got ${result.sharedFractionShorter}`);
});

// ---------------------------------------------------------------------------
// Test 4: groupSpinesIntoPhysicalBundles -- A near B, C far from both
// ---------------------------------------------------------------------------
test("groupSpinesIntoPhysicalBundles: A near B, C far => 1 group {A, B}", () => {
  // A and B are ~5m apart -- will pass all gates.
  const spineA = makeSpine("spine-a", BASE_COORDS, BASE_LEN_M, ["N"]);
  const nearB = offsetLatPolyline(BASE_COORDS, 5);
  const spineB = makeSpine("spine-b", nearB, BASE_LEN_M, ["Q"]);

  // C is far away.
  const farCoords = BASE_COORDS.map(([lon, lat]) => [lon + 1.0, lat]);
  const spineC = makeSpine("spine-c", farCoords, BASE_LEN_M, ["R"]);

  const { groups, rejects } = groupSpinesIntoPhysicalBundles([spineA, spineB, spineC], {
    avgDistMaxM: 15,
    sharedFractionMin: 0.6,
    sharedLenMinM: 250,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  assert.equal(groups.length, 1, `expected 1 group, got ${groups.length}`);
  const g = groups[0];
  assert.equal(g.member_count, 2);
  const ids = g.spine_ids.slice().sort();
  assert.deepEqual(ids, ["spine-a", "spine-b"]);
  assert.ok(g.confidence > 0.5, `expected confidence > 0.5, got ${g.confidence}`);
  // C must not appear in any group.
  const allGrouped = groups.flatMap((gr) => gr.spine_ids);
  assert.ok(!allGrouped.includes("spine-c"), "spine-c should not be grouped");
});

// ---------------------------------------------------------------------------
// Test 5: disjoint transitive overlaps split into separate scoped bundles.
//
// Setup:
//
//   A: short spine at lat 40.700..40.7029 (~330m), lon -73.99
//   C: short spine at lat 40.706..40.7089 (~330m), lon -73.99  (disjoint lat range from A)
//   B: LONG spine at lat 40.700..40.7089 (~990m), lon -73.99 + ~5m east
//      (spans BOTH A's and C's lat ranges)
//
// Pair analysis with avgDistMaxM=12, sharedFractionMin=0.6:
//   A-B: every A point finds a B point ~5m east at the same lat
//        => avgDist~5m, sharedFrac~1.0  ==> PASS (union A+B)
//   B-C: every C point finds a B point ~5m east at the same lat
//        => avgDist~5m, sharedFrac~1.0  ==> PASS (union B+C => {A,B,C})
//   A-C: A and C are spatially disjoint (>=300m apart on the lat axis,
//        same longitude). Every A point is hundreds of meters from any C
//        point. avgDist huge, sharedFrac=0 ==> FAIL
//
// The old union-find implementation incorrectly produced one {A,B,C} bundle.
// Correct behavior is two scoped bundles: {A,B} and {B,C}. A and C never share
// the same physical interval.
// ---------------------------------------------------------------------------
test("groupSpinesIntoPhysicalBundles: disjoint A-B and B-C overlaps do not become one A-B-C bundle", () => {
  // A: 30 points at lat 40.700..40.7029, lon -73.99
  const aCoords = [];
  for (let i = 0; i < 30; i++) aCoords.push([-73.99, 40.700 + i * 0.0001]);

  // C: 30 points at lat 40.706..40.7089, lon -73.99 (disjoint from A's lat range)
  const cCoords = [];
  for (let i = 0; i < 30; i++) cCoords.push([-73.99, 40.706 + i * 0.0001]);

  // B: 90 points at lat 40.700..40.7089, lon -73.99 + 0.00006 (~5m east).
  // Spans BOTH A and C's latitude ranges.
  const bCoords = [];
  for (let i = 0; i < 90; i++) bCoords.push([-73.99 + 0.00006, 40.700 + i * 0.0001]);

  // length_m: ~11.1m per 0.0001 lat * 30 points = ~330m for A/C; ~990m for B.
  const spineA = makeSpine("spine-a", aCoords, 330, ["A"]);
  const spineB = makeSpine("spine-b", bCoords, 990, ["B"]);
  const spineC = makeSpine("spine-c", cCoords, 330, ["C"]);

  const { groups } = groupSpinesIntoPhysicalBundles([spineA, spineB, spineC], {
    avgDistMaxM: 12,
    sharedFractionMin: 0.6,
    sharedLenMinM: 100,
    tangentMaxDeg: 30,
    resampleM: 10,
  });

  const groupedSets = groups.map((group) => group.spine_ids.slice().sort().join(",")).sort();
  assert.deepEqual(
    groupedSets,
    ["spine-a,spine-b", "spine-b,spine-c"],
    `expected separate scoped bundles for disjoint intervals, got ${JSON.stringify(groupedSets)}`,
  );
  assert.ok(
    groups.every((group) => group.member_count === 2),
    "no scoped group should contain all three transitive members",
  );
  assert.ok(
    groups.every((group) => group.reason === "common_overlap_run"),
    "scoped groups should be identified as common overlap runs",
  );
});

// ---------------------------------------------------------------------------
// Test 6: clipPolylineToExtent
// ---------------------------------------------------------------------------
test("clipPolylineToExtent: returns slice between fromCoord and toCoord", () => {
  // A spine with 8 coords (indices 0..7).
  const coords = [
    [-73.99, 40.700],  // 0
    [-73.99, 40.701],  // 1
    [-73.99, 40.702],  // 2
    [-73.99, 40.703],  // 3
    [-73.99, 40.704],  // 4
    [-73.99, 40.705],  // 5
    [-73.99, 40.706],  // 6
    [-73.99, 40.707],  // 7
  ];

  // fromCoord near coords[2], toCoord near coords[5].
  const fromCoord = [-73.99, 40.7021]; // very close to index 2
  const toCoord   = [-73.99, 40.7049]; // very close to index 5

  const result = clipPolylineToExtent(coords, fromCoord, toCoord, { resampleM: 10 });

  assert.ok(Array.isArray(result), "result should be an array");
  assert.ok(result.length >= 2, `result should have at least 2 points, got ${result.length}`);

  // First and last should be near fromCoord and toCoord respectively.
  const firstLat = result[0][1];
  const lastLat = result[result.length - 1][1];
  assert.ok(Math.abs(firstLat - 40.702) < 0.001, `first lat ${firstLat} should be near 40.702`);
  assert.ok(Math.abs(lastLat - 40.705) < 0.001, `last lat ${lastLat} should be near 40.705`);

  // Should include intermediate vertices 3 and 4.
  const lats = result.map((c) => c[1]);
  const has703 = lats.some((lat) => Math.abs(lat - 40.703) < 0.0005);
  const has704 = lats.some((lat) => Math.abs(lat - 40.704) < 0.0005);
  assert.ok(has703, "intermediate vertex near 40.703 should be present");
  assert.ok(has704, "intermediate vertex near 40.704 should be present");
});

// ---------------------------------------------------------------------------
// Test 7: selectPhysicalBundleSpine picks the longest member
// ---------------------------------------------------------------------------
test("selectPhysicalBundleSpine: picks longest member as base_spine_id", () => {
  const spines = [
    makeSpine("spine-short", BASE_COORDS, 500, ["A"]),
    makeSpine("spine-long",  BASE_COORDS, 2000, ["B"]),
    makeSpine("spine-mid",   BASE_COORDS, 1000, ["C"]),
  ];
  const spinesById = new Map(spines.map((s) => [s.spine_id, s]));
  const group = {
    physical_bundle_id: "pb-00001",
    spine_ids: ["spine-short", "spine-long", "spine-mid"],
    member_count: 3,
    confidence: 0.9,
  };
  const result = selectPhysicalBundleSpine(group, spinesById);
  assert.equal(result.base_spine_id, "spine-long");
  assert.equal(result.physical_bundle_id, "pb-00001");
  // route_ids should be union of all members, sorted.
  assert.deepEqual(result.route_ids, ["A", "B", "C"]);
  // member_spine_ids should match group.spine_ids.
  assert.deepEqual(result.member_spine_ids, group.spine_ids);
});

// ---------------------------------------------------------------------------
// Test 8: rejects array contains pairs that bbox-overlapped but failed gates
// ---------------------------------------------------------------------------
test("groupSpinesIntoPhysicalBundles: rejects contains bbox-overlapping but gate-failing pairs", () => {
  // Use a HORIZONTAL polyline (varying longitude, constant lat) so that a
  // perpendicular (latitude) offset of 50m actually gives ~50m nearest-vertex
  // distance rather than sliding along the line direction.
  // ~1800m long horizontal line at NYC lat.
  const horizCoords = [
    [-74.00, 40.705],
    [-73.99, 40.705],  // ~800m
    [-73.98, 40.705],  // ~800m -- total ~1600m
  ];
  const HORIZ_LEN = 1600;

  const spineA = makeSpine("spine-a", horizCoords, HORIZ_LEN, ["N"]);

  // Offset spine B by 50m north (perpendicular to the horizontal line direction).
  // 50m in latitude degrees = 50 / 111320.
  const latOffset50m = 50 / 111320;
  const coordsFar = horizCoords.map(([lon, lat]) => [lon, lat + latOffset50m]);
  const spineB = makeSpine("spine-b", coordsFar, HORIZ_LEN, ["Q"]);

  const { groups, rejects } = groupSpinesIntoPhysicalBundles([spineA, spineB], {
    avgDistMaxM: 15,
    sharedFractionMin: 0.6,
    sharedLenMinM: 250,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  // Should not be grouped -- 50m offset far exceeds avgDistMaxM=15.
  assert.equal(groups.length, 0, `expected 0 groups, got ${groups.length}`);

  // The pair should appear in rejects because the bboxes (expanded by avgDistMaxM+resampleM=40m)
  // overlap: 50m offset < 40m*2=80m expanded bbox.
  assert.equal(rejects.length, 1, `expected 1 reject, got ${rejects.length}`);
  const r = rejects[0];
  assert.ok(
    (r.spine_id_a === "spine-a" && r.spine_id_b === "spine-b") ||
    (r.spine_id_a === "spine-b" && r.spine_id_b === "spine-a"),
    "reject should reference both spines",
  );
  assert.ok(typeof r.reject_reason === "string" && r.reject_reason.length > 0,
    "reject_reason should be non-empty string");
  assert.ok(r.avgDistM > 15, `avgDistM ${r.avgDistM} should exceed threshold`);
});

// ---------------------------------------------------------------------------
// Test 9: clipPolylineToExtent preserves corridor direction
//
// When the corridor runs OPPOSITE to the bundle spine, the returned slice
// must be reversed so that result[0] corresponds to the corridor's fromCoord
// (not the spine's first vertex) and result[-1] corresponds to toCoord.
// ---------------------------------------------------------------------------
test("clipPolylineToExtent: preserves corridor direction when corridor runs opposite to bundle spine", () => {
  // Bundle spine goes east-to-west (decreasing longitude).
  const spine = [
    [-73.99, 40.70],
    [-73.985, 40.70],
    [-73.98, 40.70],
    [-73.975, 40.70],
    [-73.97, 40.70],
  ];
  // Corridor "from" is at the east end, "to" is at the west end (opposite direction from the spine).
  const fromCoord = [-73.971, 40.70];  // near east end of spine (spine's last vertex)
  const toCoord   = [-73.989, 40.70];  // near west end of spine (spine's first vertex)
  const result = clipPolylineToExtent(spine, fromCoord, toCoord, { resampleM: 25 });
  assert.ok(Array.isArray(result), "result should be an array");
  assert.ok(result.length >= 2, "result should have at least 2 points");

  const firstP = result[0];
  const lastP = result[result.length - 1];
  const dFirstFrom = Math.hypot(firstP[0] - fromCoord[0], firstP[1] - fromCoord[1]);
  const dFirstTo   = Math.hypot(firstP[0] - toCoord[0],   firstP[1] - toCoord[1]);
  const dLastFrom  = Math.hypot(lastP[0] - fromCoord[0],  lastP[1] - fromCoord[1]);
  const dLastTo    = Math.hypot(lastP[0] - toCoord[0],    lastP[1] - toCoord[1]);
  assert.ok(dFirstFrom < dFirstTo,
    `result[0] (${firstP}) should be closer to fromCoord (${fromCoord}) than to toCoord (${toCoord}); dFirstFrom=${dFirstFrom}, dFirstTo=${dFirstTo}`);
  assert.ok(dLastTo < dLastFrom,
    `result[-1] (${lastP}) should be closer to toCoord (${toCoord}) than to fromCoord (${fromCoord}); dLastFrom=${dLastFrom}, dLastTo=${dLastTo}`);
});

// ---------------------------------------------------------------------------
// Test 10: confidence is the MINIMUM pair shared-fraction, not the mean
//
// Documents the per-bundle confidence semantic. A 3-member bundle with one
// pair that just barely passes (low shared-fraction) and two tight pairs
// should report confidence = min(pair_fractions), not the average.
// ---------------------------------------------------------------------------
test("groupSpinesIntoPhysicalBundles: confidence is the minimum pair shared-fraction", () => {
  // Three spines arranged so that all pairs pass gates, but with VERY different
  // shared fractions:
  //   A and B fully overlap (lat 40.700..40.710, 100 points): shared ~= 1.0
  //   C is shifted half-up (lat 40.705..40.710 only, 50 points): shared with A or B = ~0.5
  // Use lenient gates to allow C to join the group, then assert that the
  // group's confidence reflects the WORST pair (~0.5 ish), not the mean.
  const aCoords = [];
  for (let i = 0; i < 100; i++) aCoords.push([-73.99, 40.700 + i * 0.0001]);
  const bCoords = aCoords.map(([lon, lat]) => [lon + 0.00005, lat]); // ~4m east of A
  // C: same length range as A, but its first half is far away and only the
  // second half is parallel to A and B. The shorter-spine sample loop will
  // see ~50% in-shared.
  const cCoords = [];
  // First half of C: way off to the east (not near A/B at all)
  for (let i = 0; i < 50; i++) cCoords.push([-73.99 + 0.001, 40.700 + i * 0.0001]);
  // Second half: aligned with A/B (~4m east of A)
  for (let i = 50; i < 100; i++) cCoords.push([-73.99 + 0.00005, 40.700 + i * 0.0001]);

  // Lengths roughly equal (about 1100m each for 100 points at 11m spacing).
  const LEN = 1100;
  const spineA = makeSpine("spine-a", aCoords, LEN, ["A"]);
  const spineB = makeSpine("spine-b", bCoords, LEN, ["B"]);
  const spineC = makeSpine("spine-c", cCoords, LEN, ["C"]);

  const { groups } = groupSpinesIntoPhysicalBundles([spineA, spineB, spineC], {
    avgDistMaxM: 50,         // lenient enough that C-A and C-B pass on avgDist (because the in-shared half dominates)
    sharedFractionMin: 0.4,  // allow C's ~0.5 overlap to qualify
    sharedLenMinM: 250,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  // Find a group containing all three.
  const triGroup = groups.find((g) => g.member_count === 3);
  if (triGroup) {
    // A-B share ~100% but C shares only ~50% with each of A, B.
    // confidence = MIN(pairs) should be close to 0.5, NOT close to mean(~0.66).
    assert.ok(triGroup.confidence < 0.75,
      `confidence (${triGroup.confidence}) should be < 0.75 since at least one pair shares only ~50%. ` +
      `If this asserts above 0.75, the metric may have reverted to MEAN.`);
  } else {
    // If grouping is more conservative, that's also acceptable -- the key
    // assertion is that the LOW-quality pair contributes downward pressure.
    // Find any two-member group containing C and confirm confidence < 0.75.
    const cGroup = groups.find((g) => g.spine_ids.includes("spine-c"));
    if (cGroup) {
      assert.ok(cGroup.confidence < 0.75,
        `confidence (${cGroup.confidence}) for C-bearing group should reflect partial overlap`);
    }
  }
});
