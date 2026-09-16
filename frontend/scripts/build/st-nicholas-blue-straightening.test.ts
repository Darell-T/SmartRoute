import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import { applyStNicholasBlueStraightening } from "./st-nicholas-blue-straightening.ts";
import type { Feature, JsonValue, LineStringGeometry, Position } from "./types.ts";

type Vector = [number, number];

type TestProperties = {
  bundle_id?: JsonValue;
  corridor_id?: JsonValue;
  color?: JsonValue;
  route_id?: JsonValue;
  route_ids?: JsonValue;
  color_route_ids?: JsonValue;
  st_nicholas_blue_straightened?: boolean;
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

  const firstInput = [north, south, orange];
  const secondInput = [structuredClone(north), structuredClone(south), structuredClone(orange)];
  const options = {
    bbox: {
      minLon: -73.946,
      maxLon: -73.941,
      minLat: 40.823,
      maxLat: 40.829,
    },
    endpointSnapM: 18,
  };
  const { features, diagnostics } = applyStNicholasBlueStraightening(firstInput, options);
  const again = applyStNicholasBlueStraightening(secondInput, options);
  assert.equal(JSON.stringify(diagnostics), JSON.stringify(again.diagnostics));

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
  if (maxAfter === undefined || maxBefore === undefined) {
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

function visualNetworkFeatures(filePath: string): TestFeature[] {
  const parsed: unknown = JSON.parse(fs.readFileSync(filePath, "utf8"));
  if (parsed === null || Array.isArray(parsed) || parsed !== Object(parsed)) return [];
  // SAFETY: the visual network artifact is a GeoJSON FeatureCollection after the predicates above.
  const doc = parsed as { features?: TestFeature[] };
  return Array.isArray(doc.features) ? doc.features : [];
}

type HandoffOffender = {
  featureIndex: number;
  bundle_id: unknown;
  corridor_id: unknown;
  index: number;
  turn: number;
  inLength: number;
  outLength: number;
  point: Position;
};

function isQaBlueAcFeature(feature: TestFeature, qaBBox: BBox): boolean {
  if (String(feature.properties?.color ?? "").toUpperCase() !== BLUE) return false;
  const routes = routeIdsOf(feature);
  if (!routes.includes("A") && !routes.includes("C")) return false;
  const coords = feature.geometry?.coordinates ?? [];
  return coords.some((coord) => inBBox(coord, qaBBox));
}

type VertexHandoff = {
  turn: number;
  inLength: number;
  outLength: number;
  point: Position;
};

function vertexHandoff(
  previous: Position,
  point: Position,
  next: Position,
  qaBBox: BBox,
): VertexHandoff | null {
  if (![previous, point, next].some((coord) => inBBox(coord, qaBBox))) return null;
  const inLength = distanceM(previous, point);
  const outLength = distanceM(point, next);
  const turn = turnDeg(previous, point, next);
  if (turn <= 35 || Math.max(inLength, outLength) <= 20) return null;
  return {
    turn: Number(turn.toFixed(1)),
    inLength: Number(inLength.toFixed(1)),
    outLength: Number(outLength.toFixed(1)),
    point,
  };
}

function stNicholasHandoffOffenders(features: TestFeature[], qaBBox: BBox): HandoffOffender[] {
  const offenders: HandoffOffender[] = [];
  for (const [featureIndex, feature] of features.entries()) {
    if (!isQaBlueAcFeature(feature, qaBBox)) continue;
    const coords = feature.geometry?.coordinates ?? [];
    for (let index = 1; index < coords.length - 1; index += 1) {
      const hit = vertexHandoff(coords[index - 1], coords[index], coords[index + 1], qaBBox);
      if (!hit) continue;
      offenders.push({
        featureIndex,
        bundle_id: feature.properties?.bundle_id,
        corridor_id: feature.properties?.corridor_id,
        index,
        ...hit,
      });
    }
  }
  return offenders;
}

test("real St Nicholas A/C corridor applies straightening", () => {
  const artifactPath = path.join(process.cwd(), "public", "subway-network.visual.geojson");
  const input = visualNetworkFeatures(artifactPath);
  const first = applyStNicholasBlueStraightening(input);
  const second = applyStNicholasBlueStraightening(input);
  assert.equal(first.diagnostics.applied, true);
  assert.equal(JSON.stringify(first.diagnostics), JSON.stringify(second.diagnostics));
});

test("real St Nicholas A/C corridor has no straightening handoff doglegs", () => {
  const artifactPath = path.join(process.cwd(), "public", "subway-network.visual.geojson");
  const { features, diagnostics } = applyStNicholasBlueStraightening(visualNetworkFeatures(artifactPath));
  assert.equal(diagnostics.applied, true);
  const qaBBox = {
    minLon: -73.9495,
    maxLon: -73.9355,
    minLat: 40.8200,
    maxLat: 40.8395,
  };
  const offenders = stNicholasHandoffOffenders(features, qaBBox);
  assert.deepEqual(offenders.slice(0, 5), [], `sharp handoff doglegs remain: ${JSON.stringify(offenders.slice(0, 5))}`);
});

test("real St Nicholas A/C corridor follows the A/C station spine, not the B/D branch", () => {
  const artifactPath = path.join(process.cwd(), "public", "subway-network.visual.geojson");
  const { features, diagnostics } = applyStNicholasBlueStraightening(visualNetworkFeatures(artifactPath));
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

test("St Nicholas blue straightening is a no-op for empty lists and unmatched colors", () => {
  const emptyFirst = applyStNicholasBlueStraightening([]);
  const emptySecond = applyStNicholasBlueStraightening([]);
  assert.equal(emptyFirst.diagnostics.applied, false);
  assert.deepEqual(emptyFirst.features, []);
  assert.equal(JSON.stringify(emptyFirst.diagnostics), JSON.stringify(emptySecond.diagnostics));

  const yellow = lineFeature("yellow", "#FCCC0A", ["N"], [
    [-73.9430, 40.8272],
    [-73.9438, 40.8256],
  ]);
  const unmatched = applyStNicholasBlueStraightening([yellow]);
  assert.equal(unmatched.diagnostics.applied, false);
  assert.deepEqual(unmatched.features[0].geometry.coordinates, yellow.geometry.coordinates);
  assert.equal(unmatched.features[0].properties.st_nicholas_blue_straightened, undefined);
});

test("St Nicholas blue straightening fits an orange B/D axis when no station spine is supplied", () => {
  const north = lineFeature("north", BLUE, ["A", "C"], [
    [-73.9420, 40.8280],
    [-73.9424, 40.8272],
    [-73.9428, 40.8264],
    [-73.9432, 40.8256],
  ]);
  const south = lineFeature("south", BLUE, ["A", "C", "E"], [
    [-73.94322, 40.82558],
    [-73.9436, 40.8248],
    [-73.9440, 40.8240],
    [-73.9444, 40.8232],
  ]);
  const orange = lineFeature("orange", ORANGE, ["B", "D"], [
    [-73.9410, 40.8280],
    [-73.9415, 40.8268],
    [-73.9420, 40.8256],
    [-73.9425, 40.8244],
    [-73.9430, 40.8232],
  ]);
  const options = {
    bbox: {
      minLon: -73.946,
      maxLon: -73.940,
      minLat: 40.8225,
      maxLat: 40.8295,
    },
    spineCoordinates: null,
    maxReferenceDistanceM: 150,
  };
  const first = applyStNicholasBlueStraightening([north, south, orange], options);
  const second = applyStNicholasBlueStraightening(
    [structuredClone(north), structuredClone(south), structuredClone(orange)],
    options,
  );
  assert.equal(first.diagnostics.applied, true);
  assert.equal(first.diagnostics.reference_axis_source, "orange_bd");
  assert.ok((first.diagnostics.reference_offset_point_count ?? 0) >= 4);
  assert.equal(JSON.stringify(first.diagnostics), JSON.stringify(second.diagnostics));
  const outNorth = first.features.find((item) => item.properties.bundle_id === "north");
  const outOrange = first.features.find((item) => item.properties.bundle_id === "orange");
  assert.ok(outNorth);
  assert.ok(outOrange);
  assert.equal(outNorth.properties.st_nicholas_blue_straightened, true);
  assert.deepEqual(outNorth.properties.route_ids, ["A", "C"]);
  assert.deepEqual(outOrange.geometry.coordinates, orange.geometry.coordinates);
});

test("St Nicholas blue straightening falls back to a blue-only axis when B/D is too far", () => {
  const north = lineFeature("north", BLUE, ["A"], [
    [-73.9426, 40.8280],
    [-73.9410, 40.8274],
    [-73.9394, 40.8268],
    [-73.9378, 40.8262],
  ]);
  const south = lineFeature("south", BLUE, ["C"], [
    [-73.93778, 40.82618],
    [-73.9362, 40.8256],
    [-73.9346, 40.8250],
    [-73.9330, 40.8244],
  ]);
  const orange = lineFeature("orange", ORANGE, ["B", "D"], [
    [-73.9600, 40.8380],
    [-73.9590, 40.8370],
    [-73.9580, 40.8360],
    [-73.9570, 40.8350],
    [-73.9560, 40.8340],
  ]);
  const { diagnostics } = applyStNicholasBlueStraightening([north, south, orange], {
    bbox: {
      minLon: -73.9435,
      maxLon: -73.9325,
      minLat: 40.8238,
      maxLat: 40.8288,
    },
    spineCoordinates: null,
    maxReferenceDistanceM: 20,
  });
  assert.equal(diagnostics.applied, true);
  assert.equal(diagnostics.reference_axis_source, "blue_fit");
  assert.equal(diagnostics.reference_offset_point_count, 0);
});

test("St Nicholas blue straightening merges two nearby bbox ranges on one A/C run", () => {
  const north = lineFeature("north", BLUE, ["A", "C"], [
    [-73.9416, 40.8308],
    [-73.9418, 40.8300],
    [-73.9400, 40.8294],
    [-73.9420, 40.8288],
    [-73.9424, 40.8280],
    [-73.9428, 40.8272],
  ]);
  const south = lineFeature("south", BLUE, ["A", "C"], [
    [-73.94282, 40.82718],
    [-73.9432, 40.8264],
    [-73.9436, 40.8256],
  ]);
  const { features, diagnostics } = applyStNicholasBlueStraightening([north, south], {
    bbox: {
      minLon: -73.9430,
      maxLon: -73.9414,
      minLat: 40.8270,
      maxLat: 40.8310,
    },
    marginM: 0,
    rangeExtensionM: 220,
    spineCoordinates: null,
  });
  assert.equal(diagnostics.applied, true);
  const outNorth = features.find((item) => item.properties.bundle_id === "north");
  const outSouth = features.find((item) => item.properties.bundle_id === "south");
  assert.ok(outNorth);
  assert.ok(outSouth);
  assert.equal(outNorth.properties.st_nicholas_blue_straightened, true);
  assert.equal(outSouth.properties.st_nicholas_blue_straightened, true);
  assert.deepEqual(outNorth.properties.route_ids, ["A", "C"]);
  const axisStart = outNorth.geometry.coordinates[0];
  const axisEnd = outSouth.geometry.coordinates.at(-1);
  assert.ok(axisEnd);
  const maxDistance = Math.max(
    ...outNorth.geometry.coordinates.map((point) => perpendicularDistanceM(point, axisStart, axisEnd)),
    ...outSouth.geometry.coordinates.map((point) => perpendicularDistanceM(point, axisStart, axisEnd)),
  );
  assert.ok(maxDistance < 2, `merged ranges should lie on one axis, max=${maxDistance}`);
});

test("St Nicholas blue straightening uses route_id alone and rejects insufficient geometry", () => {
  const north = lineFeature("north", BLUE, [], [
    [-73.9426, 40.8280],
    [-73.9430, 40.8272],
    [-73.9437, 40.8263],
    [-73.9438, 40.8256],
  ]);
  north.properties.route_id = "A";
  delete north.properties.route_ids;
  delete north.properties.color_route_ids;
  const south = lineFeature("south", BLUE, [], [
    [-73.94368, 40.82557],
    [-73.9443, 40.8246],
    [-73.9449, 40.8237],
  ]);
  south.properties.route_id = "C";
  delete south.properties.route_ids;
  const { diagnostics, features } = applyStNicholasBlueStraightening([north, south], {
    bbox: {
      minLon: -73.946,
      maxLon: -73.941,
      minLat: 40.823,
      maxLat: 40.829,
    },
    spineCoordinates: [],
  });
  assert.equal(diagnostics.applied, true);
  assert.equal(features[0].properties.st_nicholas_blue_straightened, true);

  const one = applyStNicholasBlueStraightening([north]);
  assert.equal(one.diagnostics.applied, false);
  assert.equal(one.diagnostics.reason, "insufficient_target_geometry");

  const colorless = lineFeature("plain", "", ["A"], [
    [-73.9426, 40.8280],
    [-73.9438, 40.8256],
  ]);
  delete colorless.properties.color;
  const skipped = applyStNicholasBlueStraightening([colorless, south]);
  assert.equal(skipped.diagnostics.applied, false);
});
