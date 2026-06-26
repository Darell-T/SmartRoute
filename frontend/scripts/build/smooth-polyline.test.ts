// frontend/scripts/build/smooth-polyline.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { smoothSharpCorners, countSharpCorners, densifyLongSegments, type Coordinate } from "./smooth-polyline.ts";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / (111320 * Math.cos((40.69 * Math.PI) / 180));

function P(lon0: number, lat0: number, dxM: number, dyM: number): Coordinate {
  return [lon0 + dxM * DEG_PER_M_LON, lat0 + dyM * DEG_PER_M_LAT];
}

test("countSharpCorners counts only corners above the threshold", () => {
  const o: Coordinate = [-73.98, 40.69];
  const coords = [P(...o, 0, 0), P(...o, 0, 200), P(...o, 200, 200)]; // 90deg
  assert.equal(countSharpCorners(coords, 35), 1);
  const gentle = [P(...o, 0, 0), P(...o, 0, 200), P(...o, 35, 400)]; // ~10deg
  assert.equal(countSharpCorners(gentle, 35), 0);
});

test("smoothSharpCorners rounds a 90deg elbow (reduces sharpness, keeps endpoints)", () => {
  const o: Coordinate = [-73.98, 40.69];
  const coords = [P(...o, 0, 0), P(...o, 0, 200), P(...o, 200, 200)];
  const out = smoothSharpCorners(coords, {
    angleThresholdDeg: 35,
    iterations: 2,
    ratio: 0.22,
    maxFilletM: 18,
  });
  assert.deepEqual(out[0], coords[0]);
  assert.deepEqual(out[out.length - 1], coords[coords.length - 1]);
  assert.equal(countSharpCorners(out, 35), 0);
  assert.ok(out.length > coords.length);
});

test("smoothSharpCorners leaves a straight line byte-identical", () => {
  const o: Coordinate = [-73.98, 40.69];
  const coords = [P(...o, 0, 0), P(...o, 0, 100), P(...o, 0, 200), P(...o, 0, 300)];
  assert.equal(smoothSharpCorners(coords, { angleThresholdDeg: 35 }), coords);
});

test("smoothSharpCorners leaves a gentle curve byte-identical", () => {
  const o: Coordinate = [-73.98, 40.69];
  const coords = [P(...o, 0, 0), P(...o, 5, 100), P(...o, 12, 200), P(...o, 22, 300)];
  assert.equal(smoothSharpCorners(coords, { angleThresholdDeg: 35 }), coords);
});

test("smoothSharpCorners caps the fillet distance on long segments", () => {
  const o: Coordinate = [-73.98, 40.69];
  const corner = P(...o, 0, 1000);
  const coords = [P(...o, 0, 0), corner, P(...o, 1000, 1000)];
  const out = smoothSharpCorners(coords, {
    angleThresholdDeg: 35,
    iterations: 1,
    ratio: 0.5,
    maxFilletM: 20,
  });
  const R = 6371000;
  const hav = ([a, b]: Coordinate, [c, d]: Coordinate) => {
    const r = Math.PI / 180;
    const dy = (d - b) * r;
    const dx = (c - a) * r;
    const h =
      Math.sin(dy / 2) ** 2 +
      Math.cos(b * r) * Math.cos(d * r) * Math.sin(dx / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  };
  assert.ok(hav(out[1], corner) <= 21, `back cut within cap, got ${hav(out[1], corner).toFixed(1)}`);
  assert.ok(hav(out[2], corner) <= 21, `fwd cut within cap, got ${hav(out[2], corner).toFixed(1)}`);
});

test("smoothSharpCorners returns inputs < 3 points unchanged", () => {
  const a: Coordinate[] = [[-73.98, 40.69], [-73.97, 40.70]];
  assert.equal(smoothSharpCorners(a, {}), a);
});

test("densifyLongSegments subdivides chords longer than maxSegM, preserving endpoints", () => {
  const coords: Coordinate[] = [[-73.86, 40.83], [-73.845, 40.842]]; // ~1.8km single chord
  const dense = densifyLongSegments(coords, 250, 40);
  const R = 6371000;
  const rad = Math.PI / 180;
  const hav = ([a, b]: Coordinate, [c, d]: Coordinate) => {
    const dy = (d - b) * rad;
    const dx = (c - a) * rad;
    return 2 * R * Math.asin(Math.sqrt(
      Math.sin(dy / 2) ** 2 + Math.cos(b * rad) * Math.cos(d * rad) * Math.sin(dx / 2) ** 2,
    ));
  };
  for (let i = 1; i < dense.length; i += 1) {
    assert.ok(hav(dense[i - 1], dense[i]) <= 251, "no segment exceeds maxSegM");
  }
  assert.deepEqual(dense[0], coords[0]);
  assert.deepEqual(dense[dense.length - 1], coords[1]);
  assert.ok(dense.length > 2, "points were inserted");
});

test("densifyLongSegments leaves an already-dense line byte-identical", () => {
  const coords: Coordinate[] = [[-73.99, 40.68], [-73.99, 40.681], [-73.99, 40.682]];
  assert.equal(densifyLongSegments(coords, 250, 40), coords);
});
