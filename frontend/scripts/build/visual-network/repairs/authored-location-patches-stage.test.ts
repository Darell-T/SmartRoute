import assert from "node:assert/strict";
import { test } from "node:test";
import { applyAuthoredLocationPatchesStage } from "./authored-location-patches-stage.ts";
import type { LineFeature } from "../shared/types.ts";

function line(id: string, routes: string[], color: string, coordinates: Array<[number, number]>): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: { corridor_id: id, route_ids: routes, color },
  };
}

test("authored location patches are a no-op when visualFeatures is missing", () => {
  const bundleArtifacts = {};
  applyAuthoredLocationPatchesStage({ bundleArtifacts });
  assert.equal("visualFeatures" in bundleArtifacts, false);
});

test("authored location patches leave unmatched geometry identical and deterministic", () => {
  const coords: Array<[number, number]> = [[-73.98, 40.75], [-73.98, 40.76]];
  const first = [line("unmatched", ["1"], "#EE352E", structuredClone(coords))];
  const second = [line("unmatched", ["1"], "#EE352E", structuredClone(coords))];
  applyAuthoredLocationPatchesStage({ bundleArtifacts: { visualFeatures: first } });
  applyAuthoredLocationPatchesStage({ bundleArtifacts: { visualFeatures: second } });
  assert.equal(first.length, 1);
  assert.deepEqual(first[0].geometry.coordinates, coords);
  assert.equal(first[0].properties.corridor_id, "unmatched");
  assert.equal(JSON.stringify(first), JSON.stringify(second));
});

test("authored location patches leave an empty feature list empty", () => {
  const visualFeatures: LineFeature[] = [];
  const bundleArtifacts = { visualFeatures };
  applyAuthoredLocationPatchesStage({ bundleArtifacts });
  assert.deepEqual(bundleArtifacts.visualFeatures, []);
});

test("authored location patches smooth the Joralemon green river crossing", () => {
  const before: Array<[number, number]> = [
    [-73.9850, 40.6905],
    [-73.9940, 40.6935],
    [-74.0030, 40.6965],
    [-74.0078, 40.6979],
    [-74.0085, 40.6982],
    [-74.0081, 40.6985],
    [-74.0087, 40.6988],
    [-74.0110, 40.7020],
    [-74.0130, 40.7060],
    [-74.0100, 40.7110],
  ];
  const first = { visualFeatures: [line("green-45", ["4", "5"], "#00933C", structuredClone(before))] };
  const second = { visualFeatures: [line("green-45", ["4", "5"], "#00933C", structuredClone(before))] };
  applyAuthoredLocationPatchesStage({ bundleArtifacts: first });
  applyAuthoredLocationPatchesStage({ bundleArtifacts: second });
  const out = first.visualFeatures[0];
  assert.ok(out);
  assert.equal(out.properties.joralemon_green_river_smoothed, true);
  assert.equal(out.properties.corridor_id, "green-45");
  assert.deepEqual(out.geometry.coordinates[0], before[0]);
  assert.deepEqual(out.geometry.coordinates.at(-1), before.at(-1));
  assert.ok(out.geometry.coordinates.length > before.length);
  assert.equal(JSON.stringify(first.visualFeatures), JSON.stringify(second.visualFeatures));
});

test("authored location patches rebalance Brighton B/Q, Culver F/G, St Nicholas A/C, and Nostrand 2/5", () => {
  const degLat = 1 / 110574;
  const churchLon = -73.964;
  const churchLat = 40.646;
  const churchLng = 1 / (111320 * Math.cos((churchLat * Math.PI) / 180));
  const church = (xM: number, yM: number): [number, number] => [
    churchLon + xM * churchLng,
    churchLat + yM * degLat,
  ];
  const culverLon = -73.977;
  const culverLat = 40.655;
  const culverLng = 111320 * Math.cos((culverLat * Math.PI) / 180);
  const culver = (xM: number, yM: number): [number, number] => [
    culverLon + xM / culverLng,
    culverLat + yM / 110574,
  ];
  const nostrand = (xM: number, yM: number): [number, number] => [
    -73.951 + xM / 84410,
    40.670 + yM / 111320,
  ];
  const visualFeatures = [
    line("yellow-q", ["Q"], "#FCCC0A", [
      church(-20, -460), church(-24, -260), church(-18, -80), church(-10, 80), church(-6, 280), church(-2, 460),
    ]),
    line("orange-b", ["B"], "#FF6319", [
      church(-7, -460), church(-9, -260), church(-10, -80), church(-8, 80), church(2, 280), church(8, 460),
    ]),
    line("f", ["F"], "#FF6319", [
      culver(0, -260), culver(2, -160), culver(3, -60), culver(4, 60), culver(4, 180), culver(2, 220),
    ]),
    line("g-south", ["G"], "#6CBE45", [
      culver(14, -260), culver(15, -160), culver(13, -70), culver(8, -20), culver(1, 0),
    ]),
    line("g-north", ["G"], "#6CBE45", [
      culver(4, 220), culver(3, 120), culver(2, 60), culver(1, 0),
    ]),
    line("blue-north", ["A", "C"], "#0A84FF", [
      [-73.93992, 40.83499],
      [-73.94020, 40.83410],
      [-73.94082, 40.83280],
      [-73.94183, 40.82901],
      [-73.94360, 40.82604],
    ]),
    line("blue-south", ["A", "C", "E"], "#0A84FF", [
      [-73.94358, 40.82602],
      [-73.94418, 40.82490],
      [-73.94480, 40.82380],
    ]),
    line("green-4", ["4"], "#00933C", [
      nostrand(1400, 0), nostrand(1000, 0), nostrand(600, 0), nostrand(180, 0), nostrand(0, 0), nostrand(-8, -8),
    ]),
    line("green-5", ["5"], "#00933C", [
      nostrand(-120, -500), nostrand(-80, -300), nostrand(-35, -120), nostrand(-10, -18), nostrand(-120, 4), nostrand(-500, 12),
    ]),
    line("red-3", ["3"], "#EE352E", [
      nostrand(-600, -12), nostrand(-200, -10), nostrand(200, -9), nostrand(600, -8),
    ]),
    line("red-2", ["2"], "#EE352E", [
      nostrand(-10, -18), nostrand(-35, -25), nostrand(-10, -80), nostrand(35, -220), nostrand(70, -500),
    ]),
  ];
  const first = { visualFeatures: structuredClone(visualFeatures) };
  const second = { visualFeatures: structuredClone(visualFeatures) };
  applyAuthoredLocationPatchesStage({ bundleArtifacts: first });
  applyAuthoredLocationPatchesStage({ bundleArtifacts: second });
  const byId = (id: string) => first.visualFeatures.find((item) => item.properties.corridor_id === id);
  assert.equal(byId("yellow-q")?.properties.brighton_bq_church_spacing, true);
  assert.equal(byId("orange-b")?.properties.brighton_bq_church_spacing, true);
  assert.equal(byId("g-south")?.properties.culver_fg_prospect_smoothing, true);
  assert.equal(byId("blue-north")?.properties.st_nicholas_blue_straightened, true);
  assert.equal(byId("green-4")?.properties.nostrand_eastern_straight_tail, true);
  assert.equal(byId("red-2")?.properties.nostrand_eastern_branch_curve, true);
  const yellowRoutes = byId("yellow-q")?.properties.route_ids;
  assert.ok(Array.isArray(yellowRoutes));
  assert.equal(yellowRoutes[0], "Q");
  assert.equal(JSON.stringify(first.visualFeatures), JSON.stringify(second.visualFeatures));
});
