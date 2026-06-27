import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import { applyStNicholasBlueStraightening } from "./st-nicholas-blue-straightening.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

type Vector = [number, number];

type TestProperties = {
  bundle_id?: unknown;
  corridor_id?: unknown;
  color?: unknown;
  route_id?: unknown;
  route_ids?: unknown;
  color_route_ids?: unknown;
  st_nicholas_blue_straightened?: boolean;
  [key: string]: unknown;
};

type TestFeature = Feature<LineStringGeometry, TestProperties>;

type BBox = {
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
};

const BLUE = "#0A84FF";
const ORANGE = "#FF6319";

function lineFeature(id: string, color: string, routes: string[], coordinates: Position[]): TestFeature {
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates,
    },
    properties: {
      bundle_id: id,
      color,
      route_ids: routes,
      color_route_ids: routes,
    },
  };
}

function xy(point: Position, lat = 40.825): Vector {
  return [
    point[0] * 111320 * Math.cos((lat * Math.PI) / 180),
    point[1] * 110574,
  ];
}

function distanceM(a: Position, b: Position): number {
  const pa = xy(a);
  const pb = xy(b);
  return Math.hypot(pb[0] - pa[0], pb[1] - pa[1]);
}

function perpendicularDistanceM(point: Position, lineA: Position, lineB: Position): number {
  const p = xy(point);
  const a = xy(lineA);
  const b = xy(lineB);
  const vx = b[0] - a[0];
  const vy = b[1] - a[1];
  const wx = p[0] - a[0];
  const wy = p[1] - a[1];
  const denom = vx * vx + vy * vy || 1;
  const t = Math.max(0, Math.min(1, (wx * vx + wy * vy) / denom));
  return Math.hypot(p[0] - (a[0] + vx * t), p[1] - (a[1] + vy * t));
}

function polylineDistanceM(point: Position, line: Position[]): number {
  let best = Infinity;
  for (let index = 0; index < line.length - 1; index += 1) {
    best = Math.min(best, perpendicularDistanceM(point, line[index], line[index + 1]));
  }
  return best;
}

function inBBox(point: Position, bbox: BBox): boolean {
  return (
    point[0] >= bbox.minLon &&
    point[0] <= bbox.maxLon &&
    point[1] >= bbox.minLat &&
    point[1] <= bbox.maxLat
  );
}

function routeIdsOf(feature: TestFeature): string[] {
  const props = feature.properties ?? {};
  return Array.from(new Set([
    ...(Array.isArray(props.route_ids) ? props.route_ids : []),
    ...(Array.isArray(props.color_route_ids) ? props.color_route_ids : []),
    props.route_id,
  ].filter(Boolean).map(String)));
}

function bearingDeg(a: Position, b: Position): number {
  const pa = xy(a);
  const pb = xy(b);
  return (Math.atan2(pb[1] - pa[1], pb[0] - pa[0]) * 180) / Math.PI;
}

function turnDeg(previous: Position, point: Position, next: Position): number {
  let delta = Math.abs(bearingDeg(previous, point) - bearingDeg(point, next));
  while (delta > 180) delta = Math.abs(delta - 360);
  return delta;
}

test("St Nicholas blue straightening aligns A/C seam pieces onto one straight axis", () => {
  const north = lineFeature("north", BLUE, ["A", "C"], [
    [-73.9426, 40.8280],
    [-73.9430, 40.8272],
    [-73.9437, 40.8263], // off-axis kink
    [-73.9438, 40.8256],
  ]);
  const south = lineFeature("south", BLUE, ["A", "C", "E"], [
    [-73.94368, 40.82557], // nearby seam endpoint, not identical
    [-73.9443, 40.8246],
    [-73.9449, 40.8237],
  ]);
  const orange = lineFeature("orange", ORANGE, ["B", "D"], [
    [-73.9420, 40.8280],
    [-73.9440, 40.8240],
  ]);

  const { features, diagnostics } = applyStNicholasBlueStraightening([north, south, orange], {
    bbox: {
      minLon: -73.946,
      maxLon: -73.941,
      minLat: 40.823,
      maxLat: 40.829,
    },
    endpointSnapM: 18,
  });

  assert.equal(diagnostics.applied, true);
  assert.equal(diagnostics.target_feature_count, 2);
  assert.equal(diagnostics.snapped_endpoint_clusters, 1);

  const outNorth = features.find((feature) => feature.properties.bundle_id === "north");
  const outSouth = features.find((feature) => feature.properties.bundle_id === "south");
  const outOrange = features.find((feature) => feature.properties.bundle_id === "orange");
  assert.ok(outNorth);
  assert.ok(outSouth);
  assert.ok(outOrange);
  assert.equal(outNorth.properties.st_nicholas_blue_straightened, true);
  assert.equal(outSouth.properties.st_nicholas_blue_straightened, true);
  assert.deepEqual(outOrange.geometry.coordinates, orange.geometry.coordinates);

  const northEnd = outNorth.geometry.coordinates.at(-1);
  const southStart = outSouth.geometry.coordinates[0];
  assert.ok(northEnd);
  assert.ok(distanceM(northEnd, southStart) < 0.2, "seam endpoints should be snapped together");

  const axisStart = outNorth.geometry.coordinates[0];
  const axisEnd = outSouth.geometry.coordinates.at(-1);
  assert.ok(axisEnd);
  const maxDistance = Math.max(
    ...outNorth.geometry.coordinates.map((point) => perpendicularDistanceM(point, axisStart, axisEnd)),
    ...outSouth.geometry.coordinates.map((point) => perpendicularDistanceM(point, axisStart, axisEnd)),
  );
  assert.ok(maxDistance < 1.0, `expected straightened blue points to be on one axis, max=${maxDistance}`);
  const maxAfter = diagnostics.max_perpendicular_after_m;
  const maxBefore = diagnostics.max_perpendicular_before_m;
  if (typeof maxAfter !== "number" || typeof maxBefore !== "number") {
    throw new TypeError("expected numeric St Nicholas drift diagnostics");
  }
  assert.ok(
    maxAfter < maxBefore * 0.25,
    "straightening should materially reduce lateral drift",
  );
});

test("default St Nicholas scope snaps the visible 145-163 St run to the A/C station spine", () => {
  const north = lineFeature("north", BLUE, ["A", "C"], [
    [-73.93992, 40.83499],
    [-73.94020, 40.83410],
    [-73.94082, 40.83280],
    [-73.94183, 40.82901], // visible off-axis bow near the current screenshot
    [-73.94360, 40.82604],
  ]);
  const south = lineFeature("south", BLUE, ["A", "C", "E"], [
    [-73.94358, 40.82602],
    [-73.94418, 40.82490],
    [-73.94480, 40.82380],
  ]);

  const { features, diagnostics } = applyStNicholasBlueStraightening([north, south]);

  assert.equal(diagnostics.applied, true);
  assert.equal(diagnostics.target_feature_count, 2);

  const outNorth = features.find((feature) => feature.properties.bundle_id === "north");
  const outSouth = features.find((feature) => feature.properties.bundle_id === "south");
  assert.ok(outNorth);
  assert.ok(outSouth);
  const stationSpine: Position[] = [
    [-73.944216, 40.824783],
    [-73.941514, 40.830518],
    [-73.939892, 40.836013],
  ];
  const maxDistance = Math.max(
    ...outNorth.geometry.coordinates.map((point) => polylineDistanceM(point, stationSpine)),
    ...outSouth.geometry.coordinates.map((point) => polylineDistanceM(point, stationSpine)),
  );

  assert.ok(
    maxDistance < 1.0,
    `expected full 145-163 St blue run to follow station spine, max=${maxDistance}`,
  );
});

test("straightening extends to nearby endpoint vertices so bbox boundaries do not create doglegs", () => {
  const north = lineFeature("north", BLUE, ["A", "C"], [
    [-73.9404, 40.8320],
    [-73.9398, 40.8314], // outside the bbox, but close enough to be part of the same straight run
    [-73.9409, 40.8306],
    [-73.9412, 40.8296],
    [-73.9416, 40.8284],
  ]);
  const south = lineFeature("south", BLUE, ["A", "C", "E"], [
    [-73.94162, 40.82838],
    [-73.9420, 40.8272],
    [-73.9424, 40.8260],
  ]);

  const { features, diagnostics } = applyStNicholasBlueStraightening([north, south], {
    bbox: {
      minLon: -73.9430,
      maxLon: -73.9405,
      minLat: 40.8260,
      maxLat: 40.8310,
    },
    marginM: 0,
    rangeExtensionM: 220,
  });

  assert.equal(diagnostics.applied, true);

  const outNorth = features.find((feature) => feature.properties.bundle_id === "north");
  const outSouth = features.find((feature) => feature.properties.bundle_id === "south");
  assert.ok(outNorth);
  assert.ok(outSouth);
  const axisStart = outNorth.geometry.coordinates[0];
  const axisEnd = outSouth.geometry.coordinates.at(-1);
  assert.ok(axisEnd);
  const maxDistance = Math.max(
    ...outNorth.geometry.coordinates.map((point) => perpendicularDistanceM(point, axisStart, axisEnd)),
    ...outSouth.geometry.coordinates.map((point) => perpendicularDistanceM(point, axisStart, axisEnd)),
  );

  assert.ok(
    maxDistance < 1.0,
    `expected boundary-adjacent endpoint to be included in the straightened run, max=${maxDistance}`,
  );
});

test("real St Nicholas A/C corridor has no straightening handoff doglegs", () => {
  const artifactPath = path.join(process.cwd(), "public", "subway-network.visual.geojson");
  const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8")) as { features?: TestFeature[] };
  const { features, diagnostics } = applyStNicholasBlueStraightening(artifact.features ?? []);
  assert.equal(diagnostics.applied, true);

  const qaBBox = {
    minLon: -73.9495,
    maxLon: -73.9355,
    minLat: 40.8200,
    maxLat: 40.8395,
  };
  const offenders: Array<{
    featureIndex: number;
    bundle_id: unknown;
    corridor_id: unknown;
    index: number;
    turn: number;
    inLength: number;
    outLength: number;
    point: Position;
  }> = [];
  for (const [featureIndex, feature] of features.entries()) {
    if (String(feature.properties?.color ?? "").toUpperCase() !== BLUE) continue;
    const routes = routeIdsOf(feature);
    if (!routes.includes("A") && !routes.includes("C")) continue;
    const coords = feature.geometry?.coordinates ?? [];
    if (!coords.some((coord) => inBBox(coord, qaBBox))) continue;

    for (let index = 1; index < coords.length - 1; index += 1) {
      const previous = coords[index - 1];
      const point = coords[index];
      const next = coords[index + 1];
      if (![previous, point, next].some((coord) => inBBox(coord, qaBBox))) continue;
      const inLength = distanceM(previous, point);
      const outLength = distanceM(point, next);
      const turn = turnDeg(previous, point, next);
      if (turn > 35 && Math.max(inLength, outLength) > 20) {
        offenders.push({
          featureIndex,
          bundle_id: feature.properties?.bundle_id,
          corridor_id: feature.properties?.corridor_id,
          index,
          turn: Number(turn.toFixed(1)),
          inLength: Number(inLength.toFixed(1)),
          outLength: Number(outLength.toFixed(1)),
          point,
        });
      }
    }
  }

  assert.deepEqual(offenders.slice(0, 5), [], `sharp handoff doglegs remain: ${JSON.stringify(offenders.slice(0, 5))}`);
});

test("real St Nicholas A/C corridor follows the A/C station spine, not the B/D branch", () => {
  const artifactPath = path.join(process.cwd(), "public", "subway-network.visual.geojson");
  const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8")) as { features?: TestFeature[] };
  const { features, diagnostics } = applyStNicholasBlueStraightening(artifact.features ?? []);
  assert.equal(diagnostics.applied, true);

  const stationSpine: Position[] = [
    [-73.944216, 40.824783], // 145 St A/C/B/D
    [-73.941514, 40.830518], // 155 St A/C
    [-73.939892, 40.836013], // 163 St-Amsterdam Av A/C
  ];
  const qaBBox = {
    minLon: -73.9495,
    maxLon: -73.9355,
    minLat: 40.8230,
    maxLat: 40.8395,
  };

  const distances: number[] = [];
  for (const feature of features) {
    if (String(feature.properties?.color ?? "").toUpperCase() !== BLUE) continue;
    const routes = routeIdsOf(feature);
    if (!routes.includes("A") && !routes.includes("C")) continue;
    for (const coord of feature.geometry?.coordinates ?? []) {
      if (inBBox(coord, qaBBox)) {
        distances.push(polylineDistanceM(coord, stationSpine));
      }
    }
  }

  assert.ok(distances.length > 20, "expected enough A/C points in the St Nicholas QA window");
  const maxDistance = Math.max(...distances);
  assert.ok(
    maxDistance <= 45,
    `A/C St Nicholas corridor drifted away from station spine: max=${maxDistance.toFixed(1)}m`,
  );
});
