import { test } from "node:test";
import assert from "node:assert/strict";

import { applyBrightonBqChurchSpacing } from "./brighton-bq-church-spacing.mjs";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / 84410;

function ll(xM, yM) {
  return [-73.964 + xM * DEG_PER_M_LON, 40.646 + yM * DEG_PER_M_LAT];
}

function feature(id, color, routeIds, coords) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: id,
      color,
      route_ids: routeIds,
      color_route_ids: routeIds,
      physical_bundle_id: "pb-00010",
      lane_offset_baked: true,
      lane_slot_source: "physical_bundle_continuous",
    },
  };
}

function xy(p) {
  return [(p[0] + 73.964) / DEG_PER_M_LON, (p[1] - 40.646) / DEG_PER_M_LAT];
}

function pointToSegmentDistanceM(p, a, b) {
  const P = xy(p);
  const A = xy(a);
  const B = xy(b);
  const vx = B[0] - A[0];
  const vy = B[1] - A[1];
  const wx = P[0] - A[0];
  const wy = P[1] - A[1];
  const t = Math.max(0, Math.min(1, (vx * wx + vy * wy) / (vx * vx + vy * vy || 1)));
  return Math.hypot(P[0] - (A[0] + vx * t), P[1] - (A[1] + vy * t));
}

function pointToLineDistanceM(point, line) {
  let best = Infinity;
  for (let index = 1; index < line.length; index += 1) {
    best = Math.min(best, pointToSegmentDistanceM(point, line[index - 1], line[index]));
  }
  return best;
}

function minLineSeparationM(left, right) {
  let best = Infinity;
  for (const point of left) best = Math.min(best, pointToLineDistanceM(point, right));
  for (const point of right) best = Math.min(best, pointToLineDistanceM(point, left));
  return best;
}

test("Brighton B/Q Church spacing removes the bend pinch while preserving endpoints", () => {
  const yellow = feature("yellow-q", "#FCCC0A", ["N", "Q", "R", "W"], [
    ll(-20, -460),
    ll(-24, -260),
    ll(-18, -80),
    ll(-10, 80),
    ll(-6, 280),
    ll(-2, 460),
  ]);
  const orange = feature("orange-b", "#FF6319", ["B"], [
    ll(-7, -460),
    ll(-9, -260),
    ll(-10, -80),
    ll(-8, 80),
    ll(2, 280),
    ll(8, 460),
  ]);

  assert.ok(
    minLineSeparationM(yellow.geometry.coordinates, orange.geometry.coordinates) < 8,
    "fixture starts with a visible pinch",
  );

  const { features, diagnostics } = applyBrightonBqChurchSpacing([yellow, orange], {
    bbox: { minLon: -73.966, maxLon: -73.960, minLat: 40.642, maxLat: 40.650 },
    marginM: 0,
    targetSeparationM: 13,
    blendM: 0,
    sampleM: 12,
  });

  assert.equal(diagnostics.applied, true);
  assert.equal(diagnostics.centerline_fit, "cubic_hermite_fit");
  assert.ok(diagnostics.min_separation_after_m >= 12.5);
  assert.ok(diagnostics.core_min_separation_after_m >= 12.5);
  assert.ok(
    diagnostics.max_centerline_turn_after_degrees <= 4,
    `centerline should be a clean turn, got ${diagnostics.max_centerline_turn_after_degrees}deg`,
  );

  const outYellow = features.find((item) => item.properties.corridor_id === "yellow-q");
  const outOrange = features.find((item) => item.properties.corridor_id === "orange-b");

  assert.deepEqual(outYellow.geometry.coordinates[0], yellow.geometry.coordinates[0]);
  assert.deepEqual(outYellow.geometry.coordinates.at(-1), yellow.geometry.coordinates.at(-1));
  assert.deepEqual(outOrange.geometry.coordinates[0], orange.geometry.coordinates[0]);
  assert.deepEqual(outOrange.geometry.coordinates.at(-1), orange.geometry.coordinates.at(-1));
  assert.ok(outYellow.properties.brighton_bq_church_spacing);
  assert.ok(outOrange.properties.brighton_bq_church_spacing);
  assert.equal(outYellow.properties.brighton_bq_church_centerline_fit, "cubic_hermite_fit");
  assert.equal(outOrange.properties.brighton_bq_church_centerline_fit, "cubic_hermite_fit");
});
