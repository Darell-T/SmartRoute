import { test } from "node:test";
import assert from "node:assert/strict";
import { splitFeatureAtLongSegments } from "./line-geometry-cleanup.mjs";

const DEG_PER_M_LAT = 1 / 111320;

function feature(coords) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      bundle_id: "bundle-test",
      corridor_id: "corr-test",
      route_ids: ["A"],
    },
  };
}

test("splitFeatureAtLongSegments drops long artificial connector segments", () => {
  const coords = [
    [-73.99, 40.7],
    [-73.99, 40.7 + 20 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 40 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 1000 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 1020 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 1040 * DEG_PER_M_LAT],
  ];

  const parts = splitFeatureAtLongSegments(feature(coords), {
    maxSegmentM: 250,
  });

  assert.equal(parts.length, 2);
  assert.deepEqual(parts[0].geometry.coordinates, coords.slice(0, 3));
  assert.deepEqual(parts[1].geometry.coordinates, coords.slice(3));
  assert.equal(parts[0].properties.max_segment_split, true);
  assert.equal(parts[1].properties.max_segment_split, true);
  assert.equal(parts[0].properties.max_segment_split_part, 0);
  assert.equal(parts[1].properties.max_segment_split_part, 1);
});

test("splitFeatureAtLongSegments preserves normal continuous features", () => {
  const coords = [
    [-73.99, 40.7],
    [-73.99, 40.7 + 20 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 40 * DEG_PER_M_LAT],
  ];

  const original = feature(coords);
  const parts = splitFeatureAtLongSegments(original, {
    maxSegmentM: 250,
  });

  assert.equal(parts.length, 1);
  assert.equal(parts[0], original);
});

test("splitFeatureAtLongSegments removes a leading long connector even when one run survives", () => {
  const coords = [
    [-73.99, 40.7],
    [-73.99, 40.7 + 1000 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 1020 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 1040 * DEG_PER_M_LAT],
  ];

  const parts = splitFeatureAtLongSegments(feature(coords), {
    maxSegmentM: 250,
  });

  assert.equal(parts.length, 1);
  assert.deepEqual(parts[0].geometry.coordinates, coords.slice(1));
  assert.equal(parts[0].properties.max_segment_split, true);
  assert.equal(parts[0].properties.max_segment_split_part, 0);
});

test("splitFeatureAtLongSegments can drop short split remnants", () => {
  const coords = [
    [-73.99, 40.7],
    [-73.99, 40.7 + 40 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 1000 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 1080 * DEG_PER_M_LAT],
    [-73.99, 40.7 + 1140 * DEG_PER_M_LAT],
  ];

  const parts = splitFeatureAtLongSegments(feature(coords), {
    maxSegmentM: 250,
    minSplitPartLengthM: 100,
  });

  assert.equal(parts.length, 1);
  assert.deepEqual(parts[0].geometry.coordinates, coords.slice(2));
  assert.equal(parts[0].properties.length_m >= 35, true);
  assert.match(parts[0].properties.corridor_id, /corr-test-split-0/);
});
