import assert from "node:assert/strict";
import { test } from "node:test";

import { addSixtyThirdStreetF } from "./sixty-third-street-f.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

type TestFeatureProperties = {
  route_ids: string[];
  color_route_ids?: string[];
  color: string;
  visual_feature_type: string;
  sixty_third_f_membership_added?: boolean;
};

// The 63 St tunnel corridor: Lexington Av-63 St -> Roosevelt Island ->
// 21 St-Queensbridge. NYC OpenData draws this geometry as the M service line
// only; in reality (and on Apple Maps) it is the F's crossing.
const TUNNEL_PATH: Position[] = [
  [-73.9662, 40.7646], // Lexington Av-63 St
  [-73.9533, 40.7591], // Roosevelt Island
  [-73.9428, 40.7544], // 21 St-Queensbridge
  [-73.9291, 40.7521], // 36 St junction approach
];

function feature(
  routes: string[],
  coordinates: Position[],
  color = "#FF6319",
): Feature<LineStringGeometry, TestFeatureProperties> {
  return {
    type: "Feature",
    properties: {
      route_ids: [...routes],
      color_route_ids: [...routes],
      color,
      visual_feature_type: "bundle_lane",
    },
    geometry: { type: "LineString", coordinates },
  };
}

test("M-only orange feature through the 63 St tunnel gains F membership", () => {
  const f = feature(["M"], TUNNEL_PATH);
  const summary = addSixtyThirdStreetF([f]);

  assert.equal(summary.updated, 1);
  assert.deepEqual(f.properties.route_ids, ["F", "M"]);
  assert.deepEqual(f.properties.color_route_ids, ["F", "M"]);
  assert.equal(f.properties.sixty_third_f_membership_added, true);
  // Membership only -- geometry must be untouched.
  assert.deepEqual(f.geometry.coordinates, TUNNEL_PATH);
});

test("orange features outside the tunnel bbox are untouched", () => {
  // The M's Myrtle Av leg (Brooklyn) -- nowhere near the tunnel.
  const f = feature(["M"], [
    [-73.9357, 40.6973],
    [-73.9106, 40.6995],
  ]);
  const summary = addSixtyThirdStreetF([f]);

  assert.equal(summary.updated, 0);
  assert.deepEqual(f.properties.route_ids, ["M"]);
});

test("features already carrying F are untouched", () => {
  const f = feature(["F", "M"], TUNNEL_PATH);
  const summary = addSixtyThirdStreetF([f]);

  assert.equal(summary.updated, 0);
  assert.equal(f.properties.sixty_third_f_membership_added, undefined);
});

test("63 St F membership is a no-op for empty lists and is deterministic", () => {
  const first = addSixtyThirdStreetF([]);
  const second = addSixtyThirdStreetF([]);
  assert.equal(first.updated, 0);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  const again = addSixtyThirdStreetF(null);
  assert.equal(again.updated, 0);
});

test("non-orange lines through the bbox (N/W 60 St tunnel) are untouched", () => {
  const f = feature(["N", "W"], [
    [-73.967, 40.7625],
    [-73.94, 40.7565],
  ], "#FCCC0A");
  const summary = addSixtyThirdStreetF([f]);

  assert.equal(summary.updated, 0);
  assert.deepEqual(f.properties.route_ids, ["N", "W"]);
});

test("63 St F membership skips non-lines, missing properties, and a one-vertex tunnel clip", () => {
  const noProps = {
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates: TUNNEL_PATH },
  };
  const point = {
    type: "Feature" as const,
    geometry: { type: "Point" as const, coordinates: TUNNEL_PATH[0] },
    properties: { route_ids: ["M"], color: "#FF6319" },
  };
  const clip = feature(["M"], [TUNNEL_PATH[0], [-73.99, 40.74]]);
  // SAFETY: these fixtures intentionally omit properties or use Point geometry.
  const summary = addSixtyThirdStreetF([noProps, point, clip] as Parameters<typeof addSixtyThirdStreetF>[0]);
  assert.equal(summary.updated, 0);
  assert.deepEqual(clip.properties.route_ids, ["M"]);
});

test("63 St F membership still adds F when color_route_ids is absent", () => {
  const f = feature(["M"], TUNNEL_PATH);
  delete f.properties.color_route_ids;
  const first = addSixtyThirdStreetF([f]);
  const clone = feature(["M"], TUNNEL_PATH);
  delete clone.properties.color_route_ids;
  const second = addSixtyThirdStreetF([clone]);
  assert.equal(first.updated, 1);
  assert.deepEqual(f.properties.route_ids, ["F", "M"]);
  assert.equal(f.properties.color_route_ids, undefined);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
});

test("63 St F membership honors an explicit bbox that excludes the tunnel", () => {
  const f = feature(["M"], TUNNEL_PATH);
  const summary = addSixtyThirdStreetF([f], {
    bbox: { minLon: -74.1, maxLon: -74.0, minLat: 40.7, maxLat: 40.72 },
  });
  assert.equal(summary.updated, 0);
  assert.deepEqual(f.properties.route_ids, ["M"]);
});
