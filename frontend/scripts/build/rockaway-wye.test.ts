import assert from "node:assert/strict";
import { test } from "node:test";

import { connectRockawayWye } from "./rockaway-wye.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

type TestFeatureProperties = {
  corridor_id: string | null;
  route_ids: string[];
  color: string;
  length_m?: number;
  rockaway_wye_connected?: boolean;
};

// Synthetic Hammels Wye matching the real defect: the east and west legs
// share a junction node; the cross-bay leg stops ~46m short of it and two
// degenerate stubs dangle at its end.
const J: Position = [-73.80935, 40.59291]; // junction node (east/west legs meet here)
const SHORT: Position = [-73.80952, 40.5933]; // cross-bay end, ~46m from J

function feature(
  id: string | null,
  coordinates: Position[],
  routes = ["A"],
): Feature<LineStringGeometry, TestFeatureProperties> {
  return {
    type: "Feature",
    properties: { corridor_id: id, route_ids: routes, color: "#0A84FF" },
    geometry: { type: "LineString", coordinates },
  };
}

function makeWye() {
  return [
    // cross-bay from the north, ending short of J
    feature("cross-bay", [[-73.8095, 40.6093], [-73.80955, 40.59339], SHORT]),
    // east leg to Far Rockaway, ending AT J
    feature("east-leg", [[-73.7545, 40.6046], [-73.80933, 40.59287], J]),
    // west leg to Rockaway Park, starting AT J
    feature("west-leg", [J, [-73.81517, 40.58817], [-73.837, 40.5805]]),
    // degenerate 6m stubs at the cross-bay end
    feature(null, [[-73.80946, 40.59327], SHORT]),
    feature(null, [[-73.80946, 40.59327], SHORT]),
  ];
}

test("cross-bay leg is extended to the junction node", () => {
  const features = makeWye();
  const summary = connectRockawayWye(features);

  assert.equal(summary.connected, true);
  const crossBay = features.find((f) => f.properties.corridor_id === "cross-bay");
  assert.ok(crossBay);
  const end = crossBay.geometry.coordinates.at(-1);
  assert.deepEqual(end, J, "cross-bay must terminate at the junction node");
});

test("degenerate stubs at the wye are removed", () => {
  const features = makeWye();
  const summary = connectRockawayWye(features);

  assert.equal(summary.stubsRemoved, 2);
  assert.equal(features.length, 3);
});

test("legs already touching the junction are untouched", () => {
  const features = makeWye();
  const before = JSON.stringify(features[1].geometry.coordinates);
  connectRockawayWye(features);
  assert.equal(JSON.stringify(features[1].geometry.coordinates), before);
});

test("no-op outside the wye or when legs are missing", () => {
  const features = [feature("elsewhere", [[-73.9, 40.7], [-73.91, 40.71]])];
  const summary = connectRockawayWye(features);
  assert.equal(summary.connected, false);
  assert.equal(features.length, 1);
});
