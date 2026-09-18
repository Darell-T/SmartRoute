import assert from "node:assert/strict";
import { test } from "node:test";
import { applyPostMottLocalFixesStage } from "./post-mott-local-fixes-stage.ts";
import type { LineFeature } from "../shared/types.ts";

function line(id: string, routes: string[], color: string, coordinates: Array<[number, number]>): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: { corridor_id: id, route_ids: routes, color },
  };
}

test("post-Mott local fixes leave unmatched geometry identical and deterministic", () => {
  const coords: Array<[number, number]> = [[-73.98, 40.75], [-73.98, 40.76]];
  const first = [line("unmatched", ["1"], "#EE352E", structuredClone(coords))];
  const second = [line("unmatched", ["1"], "#EE352E", structuredClone(coords))];
  applyPostMottLocalFixesStage({ bundleArtifacts: { visualFeatures: first } });
  applyPostMottLocalFixesStage({ bundleArtifacts: { visualFeatures: second } });
  assert.equal(first.length, 1);
  assert.deepEqual(first[0].geometry.coordinates, coords);
  const unmatchedRoutes = first[0].properties.route_ids;
  assert.ok(Array.isArray(unmatchedRoutes));
  assert.equal(unmatchedRoutes[0], "1");
  assert.equal(JSON.stringify(first), JSON.stringify(second));
});

test("post-Mott local fixes leave an empty feature list empty", () => {
  const visualFeatures: LineFeature[] = [];
  const bundleArtifacts = { visualFeatures };
  applyPostMottLocalFixesStage({ bundleArtifacts });
  assert.deepEqual(bundleArtifacts.visualFeatures, []);
});

test("post-Mott local fixes add F membership on the 63 St orange M tunnel", () => {
  const tunnelPath: Array<[number, number]> = [
    [-73.9662, 40.7646],
    [-73.9533, 40.7591],
    [-73.9428, 40.7544],
    [-73.9291, 40.7521],
  ];
  const first = [line("tunnel", ["M"], "#FF6319", structuredClone(tunnelPath))];
  const second = [line("tunnel", ["M"], "#FF6319", structuredClone(tunnelPath))];
  applyPostMottLocalFixesStage({ bundleArtifacts: { visualFeatures: first } });
  applyPostMottLocalFixesStage({ bundleArtifacts: { visualFeatures: second } });
  assert.deepEqual(first[0].properties.route_ids, ["F", "M"]);
  assert.deepEqual(first[0].geometry.coordinates, tunnelPath);
  assert.equal(first[0].properties.sixty_third_f_membership_added, true);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
});

test("post-Mott local fixes stitch SI, add 63 St F, and connect the Rockaway wye", () => {
  const lat = 40.55;
  const mPerDegLon = 111320 * Math.cos((lat * Math.PI) / 180);
  const lonAt = (m: number): [number, number] => [-74.25 + m / mPerDegLon, lat];
  const tunnelPath: Array<[number, number]> = [
    [-73.9662, 40.7646],
    [-73.9533, 40.7591],
    [-73.9428, 40.7544],
    [-73.9291, 40.7521],
  ];
  const j: [number, number] = [-73.80935, 40.59291];
  const short: [number, number] = [-73.80952, 40.5933];
  const visualFeatures = [
    line("tunnel", ["M"], "#FF6319", structuredClone(tunnelPath)),
    line("si-1", ["SI"], "#0078C6", [lonAt(0), lonAt(4000)]),
    line("si-2", ["SI"], "#0078C6", [lonAt(4070), lonAt(10000)]),
    line("cross-bay", ["A"], "#0A84FF", [[-73.8095, 40.6093], [-73.80955, 40.59339], short]),
    line("east-leg", ["A"], "#0A84FF", [[-73.7545, 40.6046], [-73.80933, 40.59287], j]),
    line("west-leg", ["A"], "#0A84FF", [j, [-73.81517, 40.58817], [-73.837, 40.5805]]),
  ];
  const clone = structuredClone(visualFeatures);
  applyPostMottLocalFixesStage({ bundleArtifacts: { visualFeatures } });
  applyPostMottLocalFixesStage({ bundleArtifacts: { visualFeatures: clone } });
  assert.deepEqual(visualFeatures[0].properties.route_ids, ["F", "M"]);
  assert.equal(visualFeatures[0].properties.sixty_third_f_membership_added, true);
  assert.ok(visualFeatures.some((item) => String(item.properties.corridor_id).startsWith("si-stitch")));
  const crossBay = visualFeatures.find((item) => item.properties.corridor_id === "cross-bay");
  assert.ok(crossBay);
  assert.deepEqual(crossBay.geometry.coordinates.at(-1), j);
  assert.equal(
    JSON.stringify(visualFeatures.map((item) => item.properties.corridor_id).sort()),
    JSON.stringify(clone.map((item) => item.properties.corridor_id).sort()),
  );
});
