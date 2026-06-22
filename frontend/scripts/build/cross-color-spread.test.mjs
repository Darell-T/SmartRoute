// frontend/scripts/build/cross-color-spread.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  detectCrossColorAdjacency,
  findSharedArcExtent,
  offsetPolylineBySlotRamp,
  offsetPolylineOverExtent,
} from "./cross-color-spread.mjs";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / (111320 * Math.cos((40.69 * Math.PI) / 180));

function ns(lon, lat, lengthM, steps = 20) {
  return Array.from({ length: steps + 1 }, (_, i) => [lon, lat + (lengthM * DEG_PER_M_LAT * i) / steps]);
}
function feat(id, color, routeIds, coords) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: { bundle_id: id, color, route_ids: routeIds, lane_slot_semantic: 0 },
  };
}

test("detectCrossColorAdjacency groups two different colors sharing a corridor", () => {
  const base = ns(-73.99, 40.686, 1000);
  const blue = feat("b-acE", "#0A84FF", ["A", "C"], base);
  const green = feat("g-solo", "#6CBE45", ["G"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  const { groups } = detectCrossColorAdjacency([blue, green], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 18, resampleM: 25,
  });
  assert.equal(groups.length, 1);
  const g = groups[0];
  assert.equal(g.members.length, 2);
  const blueMember = g.members.find((m) => m.color === "#0A84FF");
  const greenMember = g.members.find((m) => m.color === "#6CBE45");
  assert.ok(blueMember.lane_slot < greenMember.lane_slot, "colors get distinct, ordered slots");
  assert.deepEqual([blueMember.lane_slot, greenMember.lane_slot].sort((a,b)=>a-b), [-0.5, 0.5]);
});

test("detectCrossColorAdjacency ignores SAME-color pairs (Phase 3d owns those)", () => {
  const base = ns(-73.99, 40.686, 1000);
  const a = feat("a", "#0A84FF", ["A"], base);
  const c = feat("c", "#0A84FF", ["C"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  const { groups } = detectCrossColorAdjacency([a, c], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 18, resampleM: 25,
  });
  assert.equal(groups.length, 0);
});

test("detectCrossColorAdjacency skips members already offset by continuous materialization", () => {
  const base = ns(-73.99, 40.686, 1000);
  // both at slot 0 (an odd-count middle lane) but already baked by the continuous
  // materialization -> must NOT be re-spread (would double-offset).
  const a = feat("a", "#FF6319", ["B"], base);
  const b = feat("b", "#FCCC0A", ["Q"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  a.properties.lane_slot_source = "physical_bundle_continuous";
  b.properties.lane_slot_source = "physical_bundle_continuous";
  const { groups } = detectCrossColorAdjacency([a, b], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 18, resampleM: 25,
  });
  assert.equal(groups.length, 0, "already-offset continuous members are not re-spread");
});

test("detectCrossColorAdjacency ignores features on different physical tracks", () => {
  const blue = feat("b", "#0A84FF", ["A"], ns(-73.99, 40.686, 1000));
  const green = feat("g", "#6CBE45", ["G"], ns(-73.95, 40.62, 1000));
  const { groups } = detectCrossColorAdjacency([blue, green], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 18, resampleM: 25,
  });
  assert.equal(groups.length, 0);
});

test("detectCrossColorAdjacency centers a 3-color group around 0", () => {
  const base = ns(-73.99, 40.686, 1000);
  const blue = feat("b", "#0A84FF", ["A"], base);
  const orange = feat("o", "#FF6319", ["F"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  const green = feat("g", "#6CBE45", ["G"], base.map(([x, y]) => [x + 12 * DEG_PER_M_LON, y]));
  const { groups } = detectCrossColorAdjacency([blue, orange, green], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 24, resampleM: 25,
  });
  assert.equal(groups.length, 1);
  const slots = groups[0].members.map((m) => m.lane_slot).sort((a, b) => a - b);
  assert.deepEqual(slots, [-1, 0, 1]);
});

test("detectCrossColorAdjacency skips features that already carry a baked offset", () => {
  const base = ns(-73.99, 40.686, 1000);
  const blue = feat("b", "#0A84FF", ["A"], base);
  const green = feat("g", "#6CBE45", ["G"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  green.properties.lane_slot_semantic = 0.5;
  const { groups } = detectCrossColorAdjacency([blue, green], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 18, resampleM: 25,
  });
  assert.equal(groups.length, 0);
});

test("detectCrossColorAdjacency assigns deterministic slots to unknown-rank colors", () => {
  // Two colors NOT in BUNDLE_COLOR_ORDER both rank Infinity. The sort must be
  // stable (string tie-break), so the same color always gets the same slot.
  const base = ns(-73.99, 40.686, 1000);
  const c1 = feat("u1", "#123456", ["X"], base);
  const c2 = feat("u2", "#ABCDEF", ["Y"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  const run = () => {
    const { groups } = detectCrossColorAdjacency([c1, c2], {
      sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 18, resampleM: 25,
    });
    const m = Object.fromEntries(groups[0].members.map((x) => [x.color, x.lane_slot]));
    return m;
  };
  const a = run();
  const b = run();
  assert.deepEqual(a, b, "slot assignment must be identical across runs");
  // #123456 < #ABCDEF lexically -> lower slot.
  assert.ok(a["#123456"] < a["#ABCDEF"]);
});

test("detectCrossColorAdjacency still groups two slot-0 features when a third partner is already offset", () => {
  const base = ns(-73.99, 40.686, 1000);
  const blue = feat("b", "#0A84FF", ["A"], base);
  const green = feat("g", "#6CBE45", ["G"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  const orangeBaked = feat("o", "#FF6319", ["F"], base.map(([x, y]) => [x + 12 * DEG_PER_M_LON, y]));
  orangeBaked.properties.lane_slot_semantic = 0.5; // excluded (already offset)
  const { groups } = detectCrossColorAdjacency([blue, green, orangeBaked], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 24, resampleM: 25,
  });
  assert.equal(groups.length, 1, "blue + green still form a group");
  assert.equal(groups[0].members.length, 2);
  const colors = groups[0].members.map((m) => m.color).sort();
  assert.deepEqual(colors, ["#0A84FF", "#6CBE45"]);
});

// ---------------------------------------------------------------------------
// findSharedArcExtent + offsetPolylineOverExtent (segment-level / v2)
// ---------------------------------------------------------------------------

test("findSharedArcExtent finds the overlapping stretch of two long lines that share a short segment", () => {
  const A = ns(-73.99, 40.70, 3000, 120); // straight N-S, 3km
  const farE = (lon) => lon + 300 * DEG_PER_M_LON; // ~300m east
  const midLat = 40.70 + 1200 * DEG_PER_M_LAT; // ~1200m up A
  const B = [
    [farE(-73.99), 40.70],
    [farE(-73.99), midLat - 50 * DEG_PER_M_LAT],
    [-73.99 + 6 * DEG_PER_M_LON, midLat], // join A (~6m east)
    [-73.99 + 6 * DEG_PER_M_LON, midLat + 600 * DEG_PER_M_LAT], // run alongside 600m
    [farE(-73.99), midLat + 650 * DEG_PER_M_LAT], // leave east
    [farE(-73.99), midLat + 1200 * DEG_PER_M_LAT],
  ];
  const ext = findSharedArcExtent(A, B, { resampleM: 25, distMaxM: 18, minSharedLenM: 250 });
  assert.ok(ext, "should find a shared extent");
  assert.ok(ext.sharedLenM >= 250, "shared length clears the floor");
  assert.ok(ext.sharedLenM <= 900, "shared length is the alongside stretch, not all of A");
  assert.ok(ext.aStartArc > 300, "shared run starts well into A (in the middle, not at the start)");
});

test("findSharedArcExtent returns null for lines that never run close", () => {
  const A = ns(-73.99, 40.70, 1000, 40);
  const B = ns(-73.95, 40.62, 1000, 40);
  const ext = findSharedArcExtent(A, B, { resampleM: 25, distMaxM: 18, minSharedLenM: 250 });
  assert.equal(ext, null);
});

test("offsetPolylineOverExtent leaves vertices outside the extent exactly unchanged", () => {
  const coords = ns(-73.99, 40.70, 2000, 80);
  const out = offsetPolylineOverExtent(coords, 800, 1200, 8, 40);
  assert.equal(out.length, coords.length);
  assert.deepEqual(out[0], coords[0]);
  assert.deepEqual(out[out.length - 1], coords[coords.length - 1]);
});

test("offsetPolylineOverExtent applies full offset in the middle of the extent", () => {
  const coords = ns(-73.99, 40.70, 2000, 80);
  const out = offsetPolylineOverExtent(coords, 800, 1200, 8, 40);
  const R = 6371000;
  const hav = ([lo1, la1], [lo2, la2]) => {
    const r = Math.PI / 180;
    const dLat = (la2 - la1) * r;
    const dLon = (lo2 - lo1) * r;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(la1 * r) * Math.cos(la2 * r) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  };
  const i = 40; // ~arc 1000, middle of the [800,1200] extent
  const shift = hav(coords[i], out[i]);
  assert.ok(shift > 6 && shift < 10, `mid-extent vertex should shift ~8m, got ${shift.toFixed(1)}m`);
});

test("offsetPolylineOverExtent with offsetMeters 0 returns coords unchanged", () => {
  const coords = ns(-73.99, 40.70, 1000, 40);
  const out = offsetPolylineOverExtent(coords, 200, 800, 0, 40);
  assert.deepEqual(out, coords);
});

test("offsetPolylineBySlotRamp tapers from inherited bundle slot to branch center", () => {
  const coords = ns(-73.99, 40.70, 1000, 40);
  const out = offsetPolylineBySlotRamp(coords, 0.5, 0, 8);
  const R = 6371000;
  const hav = ([lo1, la1], [lo2, la2]) => {
    const r = Math.PI / 180;
    const dLat = (la2 - la1) * r;
    const dLon = (lo2 - lo1) * r;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(la1 * r) * Math.cos(la2 * r) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  };

  assert.equal(out.length, coords.length);
  assert.ok(hav(coords[0], out[0]) > 3.5, "shared-side endpoint should inherit the 4m lane offset");
  assert.ok(hav(coords[Math.floor(coords.length / 2)], out[Math.floor(out.length / 2)]) > 1.5, "middle should still carry partial taper");
  assert.ok(hav(coords[out.length - 1], out[out.length - 1]) < 0.01, "branch-side endpoint should recenter");
});
