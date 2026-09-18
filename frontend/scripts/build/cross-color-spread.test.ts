// frontend/scripts/build/cross-color-spread.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  detectCrossColorAdjacency,
  findSharedArcExtent,
  offsetPolylineBySlotRamp,
  offsetPolylineOverExtent,
  type CrossColorGroup,
  type CrossColorSpreadFeature,
} from "./cross-color-spread.ts";
import type { Position, RouteId } from "./types.ts";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / (111320 * Math.cos((40.69 * Math.PI) / 180));

function ns(lon: number, lat: number, lengthM: number, steps = 20): Position[] {
  return Array.from({ length: steps + 1 }, (_, i): Position => [lon, lat + (lengthM * DEG_PER_M_LAT * i) / steps]);
}
function feat(id: string, color: string, routeIds: RouteId[], coords: Position[]): CrossColorSpreadFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: { bundle_id: id, color, route_ids: routeIds, lane_slot_semantic: 0 },
  };
}

function mustMember(group: CrossColorGroup, color: string) {
  const member = group.members.find((m) => m.color === color);
  assert.ok(member, `expected group member ${color}`);
  return member;
}

function laneSlot(member: ReturnType<typeof mustMember>): number {
  const slot = member.lane_slot;
  assert.ok(slot === Number(slot) && Number.isFinite(slot), "expected numeric lane slot");
  return slot;
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
  const blueMember = mustMember(g, "#0A84FF");
  const greenMember = mustMember(g, "#6CBE45");
  assert.ok(laneSlot(blueMember) < laneSlot(greenMember), "colors get distinct, ordered slots");
  assert.deepEqual([laneSlot(blueMember), laneSlot(greenMember)].sort((a, b) => a - b), [-0.5, 0.5]);
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
  const slots = groups[0].members.map((m) => laneSlot(m)).sort((a, b) => a - b);
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
    const assigned: Array<[string, number]> = groups[0].members.map((x) => [x.color, laneSlot(x)]);
    return Object.fromEntries(assigned);
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
  const farE = (lon: number): number => lon + 300 * DEG_PER_M_LON; // ~300m east
  const midLat = 40.70 + 1200 * DEG_PER_M_LAT; // ~1200m up A
  const B: Position[] = [
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
  const hav = ([lo1, la1]: Position, [lo2, la2]: Position): number => {
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
  const hav = ([lo1, la1]: Position, [lo2, la2]: Position): number => {
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

test("detectCrossColorAdjacency rejects a close pair that fails later overlap gates", () => {
  const blue = feat("b", "#0A84FF", ["A"], ns(-73.99, 40.686, 1000));
  const green = feat("g", "#6CBE45", ["G"], ns(-73.99, 40.686, 1000).map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  const { groups } = detectCrossColorAdjacency([blue, green], {
    sharedFractionMin: 0.99,
    sharedLenMinM: 5000,
    avgDistMaxM: 18,
    tangentMaxDeg: 1,
    resampleM: 25,
  });
  assert.equal(groups.length, 0, "a pair that fails shared-length or tangent gates must not spread");
});

test("detectCrossColorAdjacency treats missing route_ids as an empty list", () => {
  const base = ns(-73.99, 40.686, 1000);
  const blue = feat("b", "#0A84FF", ["A"], base);
  const green = feat("g", "#6CBE45", ["G"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  delete green.properties.route_ids;
  const { groups } = detectCrossColorAdjacency([blue, green], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 18, resampleM: 25,
  });
  assert.equal(groups.length, 1);
  const greenMember = groups[0].members.find((member) => member.color === "#6CBE45");
  assert.deepEqual(greenMember?.route_ids, []);
});

test("detectCrossColorAdjacency skips features without color or with non-line geometry", () => {
  const base = ns(-73.99, 40.686, 1000);
  const blue = feat("b", "#0A84FF", ["A"], base);
  const noColor = feat("g", "#6CBE45", ["G"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  delete noColor.properties.color;
  const stub = feat("stub", "#6CBE45", ["G"], [base[0]]);
  const { groups } = detectCrossColorAdjacency([blue, noColor, stub], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 18, resampleM: 25,
  });
  assert.equal(groups.length, 0);
});

test("findSharedArcExtent returns null for short, empty, or below-floor overlaps", () => {
  const long = ns(-73.99, 40.70, 1000, 40);
  assert.equal(findSharedArcExtent([long[0]], long), null);
  assert.equal(findSharedArcExtent(long, [long[0]]), null);
  assert.equal(findSharedArcExtent([], long), null);
  const near = long.map(([x, y]): Position => [x + 6 * DEG_PER_M_LON, y]);
  assert.equal(findSharedArcExtent(long, near, { minSharedLenM: 5000, resampleM: 25, distMaxM: 18 }), null);
});

test("offsetPolylineOverExtent leaves short input unchanged and skips zero-length segments", () => {
  const one: Position[] = [[-73.99, 40.70]];
  assert.equal(offsetPolylineOverExtent(one, 0, 10, 8), one);
  const coords = ns(-73.99, 40.70, 1000, 40);
  const withDup: Position[] = [coords[0], coords[0], ...coords.slice(1)];
  const out = offsetPolylineOverExtent(withDup, 200, 800, 8, 40);
  assert.equal(out.length, withDup.length);
  assert.deepEqual(out[0], withDup[0]);
});

test("detectCrossColorAdjacency rejects a perpendicular pair that is close only at a crossing", () => {
  const nsLine = ns(-73.99, 40.686, 1200);
  const mid = nsLine[Math.floor(nsLine.length / 2)];
  const ew = Array.from({ length: 21 }, (_, i): Position => [
    mid[0] + (i - 10) * 60 * DEG_PER_M_LON,
    mid[1],
  ]);
  const blue = feat("b", "#0A84FF", ["A"], nsLine);
  const green = feat("g", "#6CBE45", ["G"], ew);
  const { groups } = detectCrossColorAdjacency([blue, green], {
    sharedFractionMin: 0.2,
    sharedLenMinM: 50,
    avgDistMaxM: 30,
    tangentMaxDeg: 20,
    resampleM: 25,
  });
  assert.equal(groups.length, 0, "a high-angle crossing must not count as a shared corridor");
});

test("detectCrossColorAdjacency treats a missing lane slot as slot 0", () => {
  const base = ns(-73.99, 40.686, 1000);
  const blue = feat("b", "#0A84FF", ["A"], base);
  const green = feat("g", "#6CBE45", ["G"], base.map(([x, y]) => [x + 6 * DEG_PER_M_LON, y]));
  delete blue.properties.lane_slot_semantic;
  delete green.properties.lane_slot_semantic;
  const { groups } = detectCrossColorAdjacency([blue, green], {
    sharedFractionMin: 0.6, sharedLenMinM: 250, avgDistMaxM: 18, resampleM: 25,
  });
  assert.equal(groups.length, 1);
});

test("offsetPolylineOverExtent with taperM 0 still offsets the interior of a hairpin", () => {
  const north = ns(-73.99, 40.70, 400, 8);
  const south = [...north].reverse();
  const hairpin: Position[] = [...north, ...south.slice(1)];
  const out = offsetPolylineOverExtent(hairpin, 50, 350, 8, 0);
  assert.equal(out.length, hairpin.length);
  assert.notDeepEqual(out[4], hairpin[4], "zero taper still offsets vertices inside the extent");
});

test("offsetPolylineBySlotRamp offsets a zero-length vertex without dropping the polyline", () => {
  const coords = ns(-73.99, 40.70, 400, 8);
  const withDup: Position[] = [...coords.slice(0, 4), coords[4], ...coords.slice(4)];
  const out = offsetPolylineBySlotRamp(withDup, 0.5, 0.5, 8);
  assert.equal(out.length, withDup.length);
  assert.notDeepEqual(out.at(-1), withDup.at(-1), "constant slot 0.5 should offset the far endpoint");
});

test("offsetPolylineBySlotRamp no-ops on short, zero, or NaN slot inputs", () => {
  const coords = ns(-73.99, 40.70, 1000, 40);
  const one: Position[] = [coords[0]];
  assert.equal(offsetPolylineBySlotRamp(one, 0.5, 0, 8), one);
  assert.deepEqual(offsetPolylineBySlotRamp(coords, Number.NaN, 0, 8), coords);
  assert.deepEqual(offsetPolylineBySlotRamp(coords, 0.5, 0, 0), coords);
  assert.deepEqual(offsetPolylineBySlotRamp(coords, 0, 0, 8), coords);
  const withDup: Position[] = [coords[0], coords[0], ...coords.slice(1)];
  const ramped = offsetPolylineBySlotRamp(withDup, 0.5, 0, 8);
  assert.equal(ramped.length, withDup.length);
});
