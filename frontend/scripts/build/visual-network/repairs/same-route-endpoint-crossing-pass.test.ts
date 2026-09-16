import assert from "node:assert/strict";
import { test } from "node:test";
import { applySameRouteEndpointCrossingPass } from "./same-route-endpoint-crossing-pass.ts";
import type { LineFeature, Position } from "../shared/types.ts";

const O: Position = [-73.87, 40.84];
const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / (111320 * Math.cos((40.84 * Math.PI) / 180));
const P = (xM: number, yM: number): Position => [O[0] + xM * DEG_PER_M_LON, O[1] + yM * DEG_PER_M_LAT];

function line(corridorId: string, routeIds: string[], coords: Position[], color = "#00933C"): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: corridorId,
      bundle_id: corridorId,
      route_ids: routeIds,
      color_route_ids: routeIds,
      color,
    },
  };
}

function cloneFeatures(features: LineFeature[]): LineFeature[] {
  return JSON.parse(JSON.stringify(features));
}

function runPass(visualFeatures: LineFeature[] | undefined, maxEndpointOvershootM = 60) {
  const bundleArtifacts = { visualFeatures };
  const result = applySameRouteEndpointCrossingPass({ bundleArtifacts, maxEndpointOvershootM });
  return { bundleArtifacts, result };
}

test("same-route endpoint-crossing pass snaps an overshooting branch onto the trunk", () => {
  const trunk = line("trunk", ["5"], [P(0, -100), P(0, 160)]);
  const branch = line("branch", ["5"], [P(-14, -16), P(18, 80), P(45, 150)]);
  const { bundleArtifacts, result } = runPass([trunk, branch]);
  assert.equal(result.sameRouteEndpointRepairCount, 1);
  const fixed = bundleArtifacts.visualFeatures?.find((feature) => feature.properties.corridor_id === "branch");
  assert.ok(fixed);
  assert.equal(fixed.properties.same_route_junction_fabric, true);
  assert.ok(Math.abs((fixed.geometry.coordinates[0][0] - O[0]) / DEG_PER_M_LON) < 0.1);
});

test("same-route endpoint-crossing pass does not repair different-route crossings", () => {
  const trunk = line("trunk", ["2"], [P(0, -100), P(0, 160)], "#EE352E");
  const branch = line("branch", ["5"], [P(-14, -16), P(18, 80), P(45, 150)]);
  const { bundleArtifacts, result } = runPass([trunk, branch]);
  assert.equal(result.sameRouteEndpointRepairCount, 0);
  assert.equal(bundleArtifacts.visualFeatures?.[0], trunk);
  assert.equal(bundleArtifacts.visualFeatures?.[1], branch);
});

test("same-route endpoint-crossing pass is deterministic and ignores empty or missing visualFeatures", () => {
  assert.equal(runPass([]).result.sameRouteEndpointRepairCount, 0);
  assert.equal(runPass(undefined).result.sameRouteEndpointRepairCount, 0);
  assert.equal(runPass(undefined).bundleArtifacts.visualFeatures, undefined);

  const trunk = line("trunk", ["5"], [P(0, -100), P(0, 160)]);
  const branch = line("branch", ["5"], [P(-14, -16), P(18, 80), P(45, 150)]);
  const first = runPass(cloneFeatures([trunk, branch]));
  const second = runPass(cloneFeatures([trunk, branch]));
  assert.equal(JSON.stringify(first.bundleArtifacts.visualFeatures), JSON.stringify(second.bundleArtifacts.visualFeatures));
  assert.equal(first.result.sameRouteEndpointRepairCount, second.result.sameRouteEndpointRepairCount);
});
