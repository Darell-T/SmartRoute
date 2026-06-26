import { test } from "node:test";
import assert from "node:assert/strict";
import { snapOffRevenueToShape, maxOffShapeM } from "./snap-off-revenue-to-shape.ts";
import type { Position } from "./types.ts";

const DEG_LAT = 1 / 110574;
const DEG_LON = 1 / (111320 * Math.cos((40.81 * Math.PI) / 180));
const O: Position = [-73.928, 40.815];
const P = (dxM: number, dyM: number): Position => [O[0] + dxM * DEG_LON, O[1] + dyM * DEG_LAT];

// a straight N-S revenue shape at x=0
const shape = Array.from({ length: 21 }, (_, i) => P(0, i * 30));

test("snaps an off-shape bulge back onto the revenue shape, leaves on-shape vertices", () => {
  // a line that follows the shape, bulges ~120m west, then returns
  const line = [
    P(0, 0), P(0, 60), P(0, 120),
    P(-60, 180), P(-120, 240), P(-120, 300), P(-60, 360), // bulge (>50m off)
    P(0, 420), P(0, 480), P(0, 540),
  ];
  const out = snapOffRevenueToShape(line, [shape], { maxOffM: 50 });
  // every output vertex is now within tolerance of the shape
  assert.ok(maxOffShapeM(out, [shape]) <= 50, `expected all vertices on-shape, got ${maxOffShapeM(out, [shape]).toFixed(0)}m`);
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
  const out = snapOffRevenueToShape(line, [curve], { maxOffM: 50 });
  assert.ok(maxOffShapeM(out, [curve]) <= 50, "result lies on the curve");
  // the replaced middle is on the arc, NOT on the straight chord between entry/exit
  const mid = out[Math.floor(out.length / 2)];
  assert.ok(mid);
  // distance from chord endpoints to mid should reflect the arc bulge (curve), i.e. mid is on the arc
  assert.ok(maxOffShapeM([mid], [curve]) <= 50);
});

test("leaves an entirely on-shape line unchanged (same ref)", () => {
  const line = [P(0, 0), P(0, 100), P(0, 200), P(0, 300)];
  assert.equal(snapOffRevenueToShape(line, [shape], { maxOffM: 50 }), line);
});

test("does nothing when no shapes are provided", () => {
  const line = [P(-200, 0), P(-200, 100)];
  assert.equal(snapOffRevenueToShape(line, [], { maxOffM: 50 }), line);
});

test("keeps a genuine divergence that never returns near the shape only where off (still snaps off vertices)", () => {
  // a branch that leaves the shape for good -- each off vertex snaps to the nearest shape point
  const line = [P(0, 0), P(0, 60), P(-80, 120), P(-160, 180)];
  const out = snapOffRevenueToShape(line, [shape], { maxOffM: 50 });
  assert.ok(maxOffShapeM(out, [shape]) <= 50);
});
