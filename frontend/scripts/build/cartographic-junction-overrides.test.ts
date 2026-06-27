import { test } from "node:test";
import assert from "node:assert/strict";
import { applyCartographicJunctionOverrides } from "./cartographic-junction-overrides.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

const DEG_PER_M_LAT = 1 / 110574;
const DEG_PER_M_LON = 1 / (111320 * Math.cos((40.815 * Math.PI) / 180));
const O: Position = [-73.93, 40.815];
const P = (dxM: number, dyM: number): Position => [O[0] + dxM * DEG_PER_M_LON, O[1] + dyM * DEG_PER_M_LAT];
const EARTH_RADIUS_M = 6371000;

type TestFeatureProperties = {
  route_ids: string[];
  color_route_ids: string[];
  color: string;
  length_m: number;
  corridor_id?: string;
  cartographic_junction_override?: string;
  [key: string]: unknown;
};

type TestFeature = Feature<LineStringGeometry, TestFeatureProperties>;
type LineFeature = Feature<LineStringGeometry, { [key: string]: unknown }>;

function hav([lon1, lat1]: Position, [lon2, lat2]: Position): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

function feature(
  routeIds: string[],
  coords: Position[],
  extra: Partial<TestFeatureProperties> = {},
): TestFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      route_ids: routeIds,
      color_route_ids: routeIds,
      color: "#00933C",
      length_m: 1,
      ...extra,
    },
  };
}

function bearing(a: Position, b: Position): number {
  return Math.atan2((b[1] - a[1]) * 110574, (b[0] - a[0]) * (1 / DEG_PER_M_LON)) * 180 / Math.PI;
}

function turnAt(coords: Position[], i: number): number {
  let turn = bearing(coords[i], coords[i + 1]) - bearing(coords[i - 1], coords[i]);
  while (turn > 180) turn -= 360;
  while (turn < -180) turn += 360;
  return Math.abs(turn);
}

function pointLineDistanceM(point: Position, start: Position, end: Position): number {
  const ax = (start[0] - O[0]) / DEG_PER_M_LON;
  const ay = (start[1] - O[1]) / DEG_PER_M_LAT;
  const bx = (end[0] - O[0]) / DEG_PER_M_LON;
  const by = (end[1] - O[1]) / DEG_PER_M_LAT;
  const px = (point[0] - O[0]) / DEG_PER_M_LON;
  const py = (point[1] - O[1]) / DEG_PER_M_LAT;
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy || 1e-9;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  return Math.hypot(px - (ax + dx * t), py - (ay + dy * t));
}

test("Mott Haven route-5 override creates an Apple-style lower schematic join into the 4/5 trunk", () => {
  const badBranch = feature(["5"], [
    P(520, 270),
    P(360, 275),
    P(200, 275),
    P(120, 270),
    P(40, 330),
    P(25, 290),
    P(35, 250),
  ], { corridor_id: "branch-5" });
  const trunk = feature(["4", "5"], [
    P(35, 255),
    P(20, 160),
    P(5, 40),
    P(0, -180),
  ], { corridor_id: "trunk-45" });

  const result = applyCartographicJunctionOverrides([badBranch, trunk], {
    branchCutBackM: 430,
    trunkMergeDownstreamM: 230,
    sampleM: 8,
    maxEndpointGapM: 80,
    schematicPoints: [
      P(-160, 205),
      P(-190, 110),
      P(-145, 25),
      P(-45, -20),
    ],
  });

  assert.equal(result.appliedCount, 1);
  assert.equal(result.features.length, 2);

  const repaired = result.features[0];
  assert.ok(repaired);
  assert.equal(repaired.properties.cartographic_junction_override, "mott_haven_5");
  assert.ok(repaired.geometry.coordinates.length > badBranch.geometry.coordinates.length);

  const end = repaired.geometry.coordinates.at(-1);
  assert.ok(end);
  assert.ok(
    hav(end, trunk.geometry.coordinates[0]) > 180,
    "branch should join lower on the 4/5 stem instead of at the station node",
  );
  assert.ok(
    Math.min(...trunk.geometry.coordinates.map((coord) => hav(end, coord))) < 45,
    "branch should still terminate on or near the 4/5 trunk",
  );

  const branchCutCeiling = P(0, 286)[1];
  const maxLat = Math.max(...repaired.geometry.coordinates.map((coord) => coord[1]));
  assert.ok(
    maxLat <= branchCutCeiling,
    "branch should not arc north above the E 149 St approach",
  );

  const debugFeature = result.debugFeatures[0];
  assert.ok(debugFeature);
  const curve = debugFeature.geometry.coordinates;
  const westSidePeel = P(-175, 95);
  assert.ok(
    Math.min(...curve.map((coord) => hav(coord, westSidePeel))) < 45,
    "branch should pass through a broad west-side peel instead of a skinny diagonal chord",
  );

  const minLon = Math.min(...repaired.geometry.coordinates.map((coord) => coord[0]));
  assert.ok(
    minLon <= P(-160, 0)[0],
    "branch should bow far enough west to read as the Apple-style loop around the block",
  );

  const topApproach = repaired.geometry.coordinates.filter((coord) => coord[1] > P(0, 235)[1]);
  const topApproachLatSpreadM =
    (Math.max(...topApproach.map((coord) => coord[1])) -
      Math.min(...topApproach.map((coord) => coord[1]))) / DEG_PER_M_LAT;
  assert.ok(
    topApproachLatSpreadM < 45,
    `E 149 St approach should read as a street-aligned run, got ${topApproachLatSpreadM.toFixed(1)}m lat spread`,
  );

  const maxTurn = Math.max(
    ...repaired.geometry.coordinates.slice(1, -1).map((_, i) => turnAt(repaired.geometry.coordinates, i + 1)),
  );
  assert.ok(maxTurn < 55, `expected controlled curve without a kink, got max turn ${maxTurn.toFixed(1)}`);
});

test("Mott Haven override is inert when the route-5 branch is not near the 4/5 trunk", () => {
  const branch = feature(["5"], [P(0, 0), P(100, 0)], { corridor_id: "branch-5" });
  const trunk = feature(["4", "5"], [P(1000, 0), P(1000, 300)], { corridor_id: "trunk-45" });

  const result = applyCartographicJunctionOverrides([branch, trunk], {
    maxEndpointGapM: 80,
  });

  assert.equal(result.appliedCount, 0);
  assert.equal(result.features[0], branch);
  assert.equal(result.features[1], trunk);
});
