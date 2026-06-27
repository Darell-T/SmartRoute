import { test } from "node:test";
import assert from "node:assert/strict";
import { parallelOffsetCrossColor, type CrossColorFeature } from "./parallel-offset-cross-color.ts";
import type { LineStringGeometry, PointGeometry, Position } from "./types.ts";

const M_LAT = 1 / 110574;
const M_LON = 1 / (111320 * Math.cos((40.84 * Math.PI) / 180));
const P = (lon0: number, lat0: number, dxM: number, dyM: number): Position => [lon0 + dxM * M_LON, lat0 + dyM * M_LAT];
const O: Position = [-73.86, 40.84];
const RED = "#EE352E";
const GREEN = "#00933C";
const ORDER = [RED, GREEN];

const R = 6371000;
const hav = (a: Position, b: Position): number => {
  const r = Math.PI / 180, dy = (b[1] - a[1]) * r, dx = (b[0] - a[0]) * r;
  return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dx / 2) ** 2));
};

function feat(cid: string, color: string, coords: Position[]): CrossColorFeature & {
  geometry: LineStringGeometry;
  properties: { corridor_id: string; color: string };
} {
  return { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: { corridor_id: cid, color } };
}

function pointFeature(cid: string, coordinates: Position): CrossColorFeature & {
  geometry: PointGeometry;
  properties: { corridor_id: string; marker_type: string };
} {
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates },
    properties: { corridor_id: cid, marker_type: "qa" },
  };
}

function findFeature(features: CrossColorFeature[], corridorId: string): CrossColorFeature {
  const feature = features.find((f) => f.properties?.corridor_id === corridorId);
  assert.ok(feature, `expected feature ${corridorId}`);
  return feature;
}

function lineCoords(feature: CrossColorFeature): Position[] {
  assert.equal(feature.geometry?.type, "LineString");
  assert.ok(Array.isArray(feature.geometry.coordinates));
  return feature.geometry.coordinates as Position[];
}

function props(feature: CrossColorFeature): NonNullable<CrossColorFeature["properties"]> {
  assert.ok(feature.properties);
  return feature.properties;
}

test("shifts the higher-color-rank line off a coincident lower-rank line (parallel pair, no cross)", () => {
  const straight = Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30));
  const red = feat("red2", RED, straight);
  const green = feat("grn5", GREEN, straight.map((c) => [...c])); // coincident
  const { features, shiftedCount } = parallelOffsetCrossColor([red, green], { colorOrder: ORDER, overlapDistM: 8, minOverlapM: 150, laneWidthM: 8 });
  assert.equal(shiftedCount, 1, "only the higher-rank (green) line moves");
  const r = lineCoords(findFeature(features, "red2"));
  const g = lineCoords(findFeature(features, "grn5"));
  // red unchanged
  assert.deepEqual(r, straight);
  // green now separated from red by ~laneWidth in the middle, consistently one side (no cross)
  let signs = new Set();
  for (let i = 5; i < 35; i += 1) {
    const sep = hav(r[i], g[i]);
    assert.ok(sep >= 5, `separated at ${i} (got ${sep.toFixed(1)})`);
    signs.add(Math.sign(g[i][0] - r[i][0]));
  }
  assert.equal(signs.size, 1, "green stays on one side of red (no crossing)");
});

test("leaves already-parallel different-color lines untouched (idempotent)", () => {
  const red = feat("red", RED, Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)));
  const green = feat("grn", GREEN, Array.from({ length: 30 }, (_, i) => P(...O, 14, i * 30))); // 14m apart already
  const { shiftedCount, features } = parallelOffsetCrossColor([red, green], { colorOrder: ORDER, overlapDistM: 8, minOverlapM: 150, laneWidthM: 8 });
  assert.equal(shiftedCount, 0);
  assert.equal(findFeature(features, "grn"), green);
});

test("does not shift sustained nearby parallel different-color lines that never swap sides", () => {
  const red = feat("red", RED, Array.from({ length: 36 }, (_, i) => P(...O, 0, i * 30)));
  const green = feat("grn", GREEN, Array.from({ length: 36 }, (_, i) => P(...O, 6, i * 30))); // inside overlapDistM, but always same side
  const originalGreen = green.geometry.coordinates.map((coord) => [...coord]);

  const { shiftedCount, features } = parallelOffsetCrossColor([red, green], {
    colorOrder: ORDER,
    overlapDistM: 8,
    minOverlapM: 150,
    laneWidthM: 8,
  });

  assert.equal(shiftedCount, 0);
  assert.equal(findFeature(features, "grn"), green);
  assert.deepEqual(green.geometry.coordinates, originalGreen, "source geometry remains unchanged");
});

test("shifts a sustained side-flip crossing only over the qualifying arc", () => {
  const red = feat("red", RED, Array.from({ length: 50 }, (_, i) => P(...O, 0, i * 30)));
  const crossingCoords = Array.from({ length: 50 }, (_, i) => P(...O, -12 + (24 * i) / 49, i * 30));
  const green = {
    ...feat("grn", GREEN, crossingCoords),
    properties: { corridor_id: "grn", color: GREEN, custom_stage_marker: "keep-me" },
  };
  const unrelated = pointFeature("station-marker", P(...O, 120, 120));
  const originalGreen = green.geometry.coordinates.map((coord) => [...coord]);

  const { shiftedCount, features } = parallelOffsetCrossColor([red, green, unrelated], {
    colorOrder: ORDER,
    overlapDistM: 8,
    minOverlapM: 150,
    laneWidthM: 8,
    taperM: 0,
  });

  assert.equal(shiftedCount, 1);
  assert.deepEqual(features.map((feature) => feature.properties?.corridor_id), ["red", "grn", "station-marker"]);
  assert.equal(features[0], red, "lower-rank target feature passes through by identity");
  assert.equal(features[2], unrelated, "unrelated features pass through by identity");

  const shifted = features[1];
  const shiftedProps = props(shifted);
  const shiftedCoords = lineCoords(shifted);
  assert.notEqual(shifted, green, "shifted feature is cloned instead of mutating the original");
  assert.equal(shiftedProps.custom_stage_marker, "keep-me");
  assert.equal(shiftedProps.cross_color_parallelized, true);
  assert.deepEqual(green.geometry.coordinates, originalGreen, "source feature geometry remains unchanged");
  assert.deepEqual(shiftedCoords[0], originalGreen[0], "pre-crossing endpoint remains unchanged");
  assert.deepEqual(shiftedCoords.at(-1), originalGreen.at(-1), "post-crossing endpoint remains unchanged");
  assert.notDeepEqual(shiftedCoords[25], originalGreen[25], "interior crossing run is shifted");
});

test("does not shift same-color overlapping lines", () => {
  const a = feat("a", GREEN, Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)));
  const b = feat("b", GREEN, Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)));
  const { shiftedCount } = parallelOffsetCrossColor([a, b], { colorOrder: ORDER, overlapDistM: 8, minOverlapM: 150 });
  assert.equal(shiftedCount, 0);
});

test("does not shift a brief crossing (run shorter than minOverlapM)", () => {
  const red = feat("red", RED, Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  // green runs perpendicular, only ~1 vertex coincident
  const green = feat("grn", GREEN, Array.from({ length: 20 }, (_, i) => P(...O, -300 + i * 30, 600)));
  const { shiftedCount } = parallelOffsetCrossColor([red, green], { colorOrder: ORDER, overlapDistM: 8, minOverlapM: 150 });
  assert.equal(shiftedCount, 0, "a brief crossing is not a shared run");
});

test("preserves input order, unrelated features, and shifted feature properties", () => {
  const straight = Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30));
  const red = feat("red", RED, straight);
  const green = {
    ...feat("grn", GREEN, straight.map((c) => [...c])),
    properties: {
      corridor_id: "grn",
      color: GREEN,
      route_ids: ["5"],
      custom_stage_marker: "keep-me",
    },
  };
  const unrelated = pointFeature("station-marker", P(...O, 300, 300));

  const { features, shiftedCount } = parallelOffsetCrossColor([unrelated, red, green], {
    colorOrder: ORDER,
    overlapDistM: 8,
    minOverlapM: 150,
    laneWidthM: 8,
  });

  assert.equal(shiftedCount, 1);
  assert.deepEqual(features.map((feature) => feature.properties?.corridor_id), ["station-marker", "red", "grn"]);
  assert.equal(features[0], unrelated, "unrelated non-LineString features pass through by identity");
  assert.equal(features[1], red, "lower-rank target feature passes through by identity");

  const shifted = features[2];
  const shiftedProps = props(shifted);
  assert.notEqual(shifted, green, "shifted feature is cloned instead of mutating the original");
  assert.equal(shiftedProps.custom_stage_marker, "keep-me");
  assert.deepEqual(shiftedProps.route_ids, ["5"]);
  assert.equal(shiftedProps.cross_color_parallelized, true);
  assert.deepEqual(green.geometry.coordinates, straight, "source feature geometry remains unchanged");
});
