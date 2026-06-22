import { test } from "node:test";
import assert from "node:assert/strict";
import { parallelOffsetCrossColor } from "./parallel-offset-cross-color.mjs";

const M_LAT = 1 / 110574;
const M_LON = 1 / (111320 * Math.cos((40.84 * Math.PI) / 180));
const P = (lon0, lat0, dxM, dyM) => [lon0 + dxM * M_LON, lat0 + dyM * M_LAT];
const O = [-73.86, 40.84];
const RED = "#EE352E";
const GREEN = "#00933C";
const ORDER = [RED, GREEN];

const R = 6371000;
const hav = (a, b) => {
  const r = Math.PI / 180, dy = (b[1] - a[1]) * r, dx = (b[0] - a[0]) * r;
  return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dx / 2) ** 2));
};

function feat(cid, color, coords) {
  return { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: { corridor_id: cid, color } };
}

test("shifts the higher-color-rank line off a coincident lower-rank line (parallel pair, no cross)", () => {
  const straight = Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30));
  const red = feat("red2", RED, straight);
  const green = feat("grn5", GREEN, straight.map((c) => [...c])); // coincident
  const { features, shiftedCount } = parallelOffsetCrossColor([red, green], { colorOrder: ORDER, overlapDistM: 8, minOverlapM: 150, laneWidthM: 8 });
  assert.equal(shiftedCount, 1, "only the higher-rank (green) line moves");
  const r = features.find((f) => f.properties.corridor_id === "red2").geometry.coordinates;
  const g = features.find((f) => f.properties.corridor_id === "grn5").geometry.coordinates;
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
  assert.equal(features.find((f) => f.properties.corridor_id === "grn"), green);
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
