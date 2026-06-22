import assert from "node:assert/strict";
import test from "node:test";

import { taperBakedJointSteps } from "./joint-offset-taper.mjs";

const M_PER_DEG_LAT = 110574;
const LAT = 40.65;
const M_PER_DEG_LNG = 111320 * Math.cos((LAT * Math.PI) / 180);

// Horizontal west->east line between xStartM and xEndM, shifted northward
// by lateralM, vertices every stepM. Simulates a BAKED lane.
function bakedLine(xStartM, xEndM, lateralM, stepM = 25) {
  const coords = [];
  for (let s = xStartM; s <= xEndM; s += stepM) {
    coords.push([s / M_PER_DEG_LNG, LAT + lateralM / M_PER_DEG_LAT]);
  }
  return coords;
}

function lateralM(point) {
  return (point[1] - LAT) * M_PER_DEG_LAT;
}

function lane(coords, routeIds, slotSemantic, id) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      visual_feature_type: "bundle_lane",
      route_ids: routeIds,
      lane_slot_semantic: slotSemantic,
      lane_slot: 0,
      lane_offset_baked: true,
      corridor_id: id,
    },
  };
}

test("warps the offset lane's tail onto the neighbor endpoint", () => {
  // G-at-Terrace-Pl shape: south lane baked 6m off-axis, north lane on axis.
  const south = lane(bakedLine(0, 600, 6), ["G"], 0.5, "south");
  const north = lane(bakedLine(600, 1200, 0), ["G"], 0, "north");

  const result = taperBakedJointSteps([south, north], { blendM: 100 });

  assert.equal(result.count, 1, "exactly one joint repaired");
  const coords = south.geometry.coordinates;
  const end = coords[coords.length - 1];
  // Mover endpoint now lands on the neighbor's start.
  assert.ok(Math.abs(lateralM(end)) < 0.01, `joint end at 0m, got ${lateralM(end)}`);
  // Far end untouched.
  assert.ok(Math.abs(lateralM(coords[0]) - 6) < 0.01, "far end keeps 6m");
  // Vertices beyond the blend zone untouched (600m lane, 100m blend).
  assert.ok(Math.abs(lateralM(coords[10]) - 6) < 0.01, "body untouched at 250m");
  // Monotonic approach within the blend zone.
  const tail = coords.slice(-5).map((p) => lateralM(p));
  for (let i = 1; i < tail.length; i += 1) {
    assert.ok(tail[i] <= tail[i - 1] + 0.01, "offset decays monotonically into the joint");
  }
  // Neighbor untouched.
  assert.ok(north.geometry.coordinates.every((p) => Math.abs(lateralM(p)) < 1e-9));
  assert.equal(south.properties.joint_offset_taper_baked, true);
  assert.equal(north.properties.joint_offset_taper_baked, undefined);
});

test("already-flush joints are left alone", () => {
  const a = lane(bakedLine(0, 600, 0.5), ["2", "3"], 0.5, "a");
  const b = lane(bakedLine(600, 1200, 0), ["2", "3"], 0, "b");
  // gap = 0.5m < snapMinM 1.5
  const result = taperBakedJointSteps([a, b]);
  assert.equal(result.count, 0);
});

test("real gaps beyond snapMaxM are left for the bridge pass", () => {
  const a = lane(bakedLine(0, 600, 25), ["A"], 0.5, "a");
  const b = lane(bakedLine(600, 1200, 0), ["A"], 0, "b");
  const result = taperBakedJointSteps([a, b]);
  assert.equal(result.count, 0);
});

test("ignores unrelated routes and equal slot magnitudes", () => {
  const a = lane(bakedLine(0, 600, 6), ["F"], 0.5, "a");
  const otherRoute = lane(bakedLine(600, 1200, 0), ["A"], 0, "other");
  assert.equal(taperBakedJointSteps([a, otherRoute]).count, 0);

  const left = lane(bakedLine(0, 600, 6), ["F"], 0.5, "left");
  const right = lane(bakedLine(600, 1200, 0), ["F"], -0.5, "right");
  assert.equal(taperBakedJointSteps([left, right]).count, 0);
});

test("an endpoint warps once, onto the nearest target, even with several neighbors", () => {
  // Mover joint endpoint has TWO slot-0 same-route neighbors in range:
  // one 6m away laterally, one 8m on the other side. A second warp must not
  // re-step the endpoint off the first target.
  const mover = lane(bakedLine(0, 600, 6), ["G"], 0.5, "mover");
  const near = lane(bakedLine(600, 1200, 0), ["G"], 0, "near"); // 6m below endpoint
  const far = lane(bakedLine(600, 1200, 14), ["G"], 0, "far"); // 8m above endpoint
  const result = taperBakedJointSteps([mover, near, far], { blendM: 100 });

  assert.equal(result.count, 1, "endpoint repaired exactly once");
  const end = mover.geometry.coordinates[mover.geometry.coordinates.length - 1];
  assert.ok(Math.abs(lateralM(end)) < 0.01, `lands on the NEAREST target (0m), got ${lateralM(end)}`);
});

test("flags the now-redundant 2-point stitch between the old endpoints", () => {
  const mover = lane(bakedLine(0, 600, 6), ["G"], 0.5, "mover");
  const north = lane(bakedLine(600, 1200, 0), ["G"], 0, "north");
  // Stitch left behind by an earlier pass, spanning the pre-warp step.
  const stitch = lane(
    [mover.geometry.coordinates[mover.geometry.coordinates.length - 1], north.geometry.coordinates[0]],
    ["G"],
    0,
    "stitch",
  );

  const result = taperBakedJointSteps([mover, north, stitch], { blendM: 100 });

  assert.equal(result.count, 1);
  assert.equal(stitch.properties.joint_offset_taper_drop, true, "stitch flagged for removal");
  assert.equal(mover.properties.joint_offset_taper_drop, undefined);
  assert.equal(north.properties.joint_offset_taper_drop, undefined);
});

test("short lanes clamp the blend instead of warping past their start", () => {
  const shortLane = lane(bakedLine(540, 600, 6, 10), ["G"], 0.5, "short"); // 60m lane
  const north = lane(bakedLine(600, 1200, 0), ["G"], 0, "north");
  const result = taperBakedJointSteps([shortLane, north], { blendM: 100 });
  assert.equal(result.count, 1);
  const coords = shortLane.geometry.coordinates;
  assert.ok(Math.abs(lateralM(coords[coords.length - 1])) < 0.01, "joint end lands at 0");
  // Start may move some (blend clamped to lane length) but never past target.
  assert.ok(lateralM(coords[0]) > 0 && lateralM(coords[0]) <= 6.01, "start stays between 0 and 6m");
});
