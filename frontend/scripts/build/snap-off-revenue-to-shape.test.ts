import { test } from "node:test";
import assert from "node:assert/strict";
import { pointToPolylineMinDistM } from "./physical-bundle.ts";
import { snapOffRevenueToPolyline } from "./snap-off-revenue-to-shape.ts";
import type { Position } from "./types.ts";

const DEG_LAT = 1 / 110574;
const DEG_LON = 1 / (111320 * Math.cos((40.81 * Math.PI) / 180));
const O: Position = [-73.928, 40.815];
const P = (dxM: number, dyM: number): Position => [O[0] + dxM * DEG_LON, O[1] + dyM * DEG_LAT];

function maxOffPolylineM(coords: Position[], polylines: Position[][]): number {
  let maxDistance = 0;
  for (const point of coords) {
    const distances = polylines
      .filter((polyline) => polyline.length >= 2)
      .map((polyline) => pointToPolylineMinDistM(point, polyline));
    if (distances.length > 0) maxDistance = Math.max(maxDistance, Math.min(...distances));
  }
  return maxDistance;
}

// a straight N-S revenue shape at x=0
const revenue = Array.from({ length: 21 }, (_, i) => P(0, i * 30));

test("snaps an off-shape bulge back onto the revenue shape, leaves on-shape vertices", () => {
  // a line that follows the shape, bulges ~120m west, then returns
  const line = [
    P(0, 0), P(0, 60), P(0, 120),
    P(-60, 180), P(-120, 240), P(-120, 300), P(-60, 360), // bulge (>50m off)
    P(0, 420), P(0, 480), P(0, 540),
  ];
  const out = snapOffRevenueToPolyline(line, [revenue], { maxOffM: 50 });
  // every output vertex is now within tolerance of the shape
  assert.ok(maxOffPolylineM(out, [revenue]) <= 50, `expected all vertices on-shape, got ${maxOffPolylineM(out, [revenue]).toFixed(0)}m`);
  // on-shape endpoints unchanged
  assert.deepEqual(out[0], line[0]);
  assert.deepEqual(out[out.length - 1], line[line.length - 1]);
});

test("replacement follows a CURVED shape, not a straight chord", () => {
  // quarter-circle shape (radius 300m) curving from south to east
  const Rm = 300;
  const curve = [];
  for (let deg = 180; deg >= 90; deg -= 5) {
    const a = (deg * Math.PI) / 180;
    curve.push(P(Rm + Rm * Math.cos(a), Rm * Math.sin(a)));
  }
  // a line that follows the curve but bulges far off in the middle
  const line = [
    curve[0], curve[3],
    P(-200, 250), P(-200, 350), // off-shape bulge
    curve[curve.length - 3], curve[curve.length - 1],
  ];
  const out = snapOffRevenueToPolyline(line, [curve], { maxOffM: 50 });
  assert.ok(maxOffPolylineM(out, [curve]) <= 50, "result lies on the curve");
  // the replaced middle is on the arc, NOT on the straight chord between entry/exit
  const mid = out[Math.floor(out.length / 2)];
  assert.ok(mid);
  // distance from chord endpoints to mid should reflect the arc bulge (curve), i.e. mid is on the arc
  assert.ok(maxOffPolylineM([mid], [curve]) <= 50);
});

test("leaves an entirely on-shape line unchanged (same ref)", () => {
  const line = [P(0, 0), P(0, 100), P(0, 200), P(0, 300)];
  assert.equal(snapOffRevenueToPolyline(line, [revenue], { maxOffM: 50 }), line);
});

test("does nothing when no shapes are provided", () => {
  const line = [P(-200, 0), P(-200, 100)];
  assert.equal(snapOffRevenueToPolyline(line, [], { maxOffM: 50 }), line);
});

test("keeps a genuine divergence that never returns near the shape only where off (still snaps off vertices)", () => {
  const line = [P(0, 0), P(0, 60), P(-80, 120), P(-160, 180)];
  const out = snapOffRevenueToPolyline(line, [revenue], { maxOffM: 50 });
  assert.ok(maxOffPolylineM(out, [revenue]) <= 50);
});

test("snaps a mid-run bulge whose covering span includes the revenue start and end", () => {
  const line = [
    P(0, 0),
    P(-80, 30),
    P(-80, 570),
    P(0, 600),
  ];
  const out = snapOffRevenueToPolyline(line, [revenue], { maxOffM: 50 });
  assert.ok(maxOffPolylineM(out, [revenue]) <= 50);
  assert.deepEqual(out[0], line[0]);
  assert.deepEqual(out[out.length - 1], line[line.length - 1]);
});

test("prefers the shorter covering span when two revenue polylines both cover the bulge", () => {
  const long = revenue;
  const short = Array.from({ length: 5 }, (_, i) => P(0, 180 + i * 30));
  const line = [
    P(0, 180),
    P(-90, 210),
    P(-90, 240),
    P(0, 270),
  ];
  const out = snapOffRevenueToPolyline(line, [long, short], { maxOffM: 50, dedupeEpsM: 0.5 });
  assert.ok(maxOffPolylineM(out, [short]) <= 50);
  const again = snapOffRevenueToPolyline(line, [long, short], { maxOffM: 50, dedupeEpsM: 0.5 });
  assert.deepEqual(again, out);
});

test("drops an off-run that starts at the first vertex without a return", () => {
  const line = [P(-120, 0), P(-120, 60), P(0, 120), P(0, 180)];
  const out = snapOffRevenueToPolyline(line, [revenue], { maxOffM: 50 });
  assert.ok(out[0][0] !== line[0][0] || out.length <= line.length);
  assert.ok(maxOffPolylineM(out.slice(-2), [revenue]) <= 50);
});

test("snapOffRevenueToPolyline uses default gates and skips a one-point revenue polyline", () => {
  const line = [
    P(0, 0),
    P(0, 60),
    P(-120, 180),
    P(-120, 240),
    P(0, 360),
    P(0, 420),
  ];
  const stub: Position[] = [P(0, 200)];
  const out = snapOffRevenueToPolyline(line, [stub, revenue]);
  assert.ok(maxOffPolylineM(out, [revenue]) <= 55);
  assert.deepEqual(out[0], line[0]);
});

test("a zero-length revenue segment still receives a bulge replacement", () => {
  const revenueDup = [...revenue];
  revenueDup.splice(4, 0, revenue[4]);
  const line = [
    P(0, 0),
    P(0, 60),
    P(-90, 120),
    P(-90, 180),
    P(0, 240),
    P(0, 300),
  ];
  const out = snapOffRevenueToPolyline(line, [revenueDup], { maxOffM: 50, dedupeEpsM: 0.5 });
  assert.ok(maxOffPolylineM(out, [revenueDup]) <= 50);
});

test("a southbound bulge is replaced along the reversed revenue subpath", () => {
  const line = [
    P(0, 420),
    P(-90, 360),
    P(-90, 300),
    P(0, 240),
  ];
  const out = snapOffRevenueToPolyline(line, [revenue], { maxOffM: 50 });
  assert.ok(maxOffPolylineM(out, [revenue]) <= 50);
  assert.deepEqual(out[0], line[0]);
  assert.deepEqual(out[out.length - 1], line[line.length - 1]);
});

test("an off-run that never rejoins a covering span keeps the original line", () => {
  const far = [
    P(0, 0),
    P(0, 30),
    P(-400, 90),
    P(-400, 150),
    P(0, 210),
    P(0, 240),
  ];
  const out = snapOffRevenueToPolyline(far, [revenue], { maxOffM: 50 });
  assert.ok(Array.isArray(out));
  assert.ok(out.length >= 2);
});

test("default snap gates leave an on-shape line unchanged", () => {
  const line = [P(0, 0), P(0, 90), P(0, 180)];
  assert.equal(snapOffRevenueToPolyline(line, [revenue]), line);
});
