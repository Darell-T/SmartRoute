import assert from "node:assert/strict";
import test from "node:test";
import {
  bidirectionalHausdorff,
  geometryStats,
  lineLengthMeters,
  offsetPolylineByLaneSlot,
  resampleEdgeAt5m,
  routeSetsIntersect,
} from "./geometry-utils.ts";

const LINE: Array<[number, number]> = [
  [-73.99, 40.75],
  [-73.99, 40.751],
  [-73.99, 40.752],
];

test("geometryStats reports exact length, coordinates, and no sharp corners on a northbound line", () => {
  const stats = geometryStats(LINE);
  assert.equal(stats.coordinate_count, 3);
  assert.equal(stats.sharp_angle_count, 0);
  assert.ok(stats.length_m > 200);
  assert.equal(geometryStats(LINE).length_m, stats.length_m);
});
test("bidirectionalHausdorff is 0 for identical samples and is deterministic twice", () => {
  const samples = resampleEdgeAt5m(LINE);
  const first = bidirectionalHausdorff(samples, samples);
  const second = bidirectionalHausdorff(samples, samples);
  assert.equal(first.hausdorff, 0);
  assert.equal(first.overlap, 1);
  assert.deepEqual(second, first);
});

test("offsetPolylineByLaneSlot leaves a two-point line on slot 0 untouched", () => {
  const coords: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.76],
  ];
  assert.equal(offsetPolylineByLaneSlot(coords, 0), coords);
  assert.deepEqual(offsetPolylineByLaneSlot([], 1), []);
});

test("routeSetsIntersect is false for empty and disjoint ids", () => {
  assert.equal(routeSetsIntersect([], ["A"]), false);
  assert.equal(routeSetsIntersect(["A"], ["B"]), false);
  assert.equal(routeSetsIntersect(["A", "C"], ["B", "C"]), true);
});

test("lineLengthMeters is 0 for empty or single-point input", () => {
  assert.equal(lineLengthMeters([]), 0);
  assert.equal(lineLengthMeters([[-73.99, 40.75]]), 0);
  assert.ok(lineLengthMeters(LINE) > 200);
});

test("resampleEdgeAt5m returns two tangent samples for a short edge", () => {
  const short: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99001, 40.75002],
  ];
  const samples = resampleEdgeAt5m(short);
  assert.equal(samples.length, 2);
  assert.equal(samples[0].t, 0);
  assert.ok(samples[1].t < 50);
  const again = resampleEdgeAt5m(short);
  assert.deepEqual(again, samples);
});

test("offsetPolylineByLaneSlot keeps a zero-length segment from exploding", () => {
  const coords: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.75],
    [-73.99, 40.76],
  ];
  const offset = offsetPolylineByLaneSlot(coords, 1);
  assert.equal(offset.length, 3);
  assert.notEqual(offset, coords);
  assert.ok(Math.abs(offset[2][0] - coords[2][0]) > 0);
});

test("geometryStats counts a hairpin, a zero incoming vector, and a stub", () => {
  const hairpin: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.751],
    [-73.99, 40.75],
  ];
  const stats = geometryStats(hairpin);
  assert.ok(stats.sharp_angle_count >= 1);
  assert.ok(stats.max_bearing_change_degrees > 120);

  const zeroIncoming: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.75],
    [-73.99, 40.751],
  ];
  assert.equal(geometryStats(zeroIncoming).sharp_angle_count, 0);
  assert.equal(geometryStats([[-73.99, 40.75]]).direct_distance_m, 0);
  assert.equal(geometryStats([[-73.99, 40.75]]).sinuosity, 1);
});

test("resampleEdgeAt5m walks a long multi-vertex line including a collapsed segment", () => {
  const long: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.751],
    [-73.99, 40.751],
    [-73.99, 40.752],
    [-73.99, 40.753],
    [-73.99, 40.754],
  ];
  const samples = resampleEdgeAt5m(long);
  assert.ok(samples.length > 2);
  assert.equal(samples[0].t, 0);
  assert.ok(samples[samples.length - 1].t > 50);
});

test("bidirectionalHausdorff treats empty samples as zero overlap and infinite distance", () => {
  const empty = bidirectionalHausdorff([], []);
  assert.equal(empty.overlap, 0);
  assert.equal(empty.avgDistanceA, Infinity);
  assert.equal(empty.avgTangentDeg, 180);
  const oneSided = bidirectionalHausdorff(resampleEdgeAt5m(LINE), []);
  assert.equal(oneSided.overlap, 0);
  assert.equal(oneSided.avgDistanceA, Infinity);
});

test("offsetPolylineByLaneSlot bevels a 180-degree reversal and ignores a non-finite slot", () => {
  const outAndBack: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.751],
    [-73.99, 40.75],
  ];
  const reversed = offsetPolylineByLaneSlot(outAndBack, 1);
  assert.equal(reversed.length, 3);
  assert.notEqual(reversed, outAndBack);

  const sharp: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.751],
    [-73.989, 40.751],
  ];
  const beveled = offsetPolylineByLaneSlot(sharp, 2);
  assert.equal(beveled.length, 3);

  const empty: Array<[number, number]> = [];
  assert.equal(offsetPolylineByLaneSlot(empty, 1), empty);
  assert.equal(offsetPolylineByLaneSlot(LINE, Number.NaN), LINE);
});

test("resampleEdgeAt5m uses a unit tangent on a collapsed short edge", () => {
  const collapsed: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.75],
  ];
  const samples = resampleEdgeAt5m(collapsed);
  assert.equal(samples.length, 2);
  assert.equal(samples[0].t, 0);
});

test("resampleEdgeAt5m steps across a collapsed first segment on a long line", () => {
  const long: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.75],
    [-73.99, 40.751],
    [-73.99, 40.752],
    [-73.99, 40.753],
    [-73.99, 40.754],
  ];
  const samples = resampleEdgeAt5m(long);
  assert.ok(samples.length > 2);
  assert.ok(samples[samples.length - 1].t > 50);
});

test("offsetPolylineByLaneSlot miters a gentle corner and bevels a sharp one", () => {
  const gentle = offsetPolylineByLaneSlot(LINE, 1);
  assert.equal(gentle.length, 3);
  assert.notEqual(gentle, LINE);

  const hairpin: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.752],
    [-73.9902, 40.7502],
  ];
  const beveled = offsetPolylineByLaneSlot(hairpin, 3);
  assert.equal(beveled.length, 3);

  const stub: Array<[number, number]> = [[-73.99, 40.75]];
  assert.equal(offsetPolylineByLaneSlot(stub, 1), stub);
});
