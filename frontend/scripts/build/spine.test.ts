// frontend/scripts/build/spine.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { computeBaseSpineHash, buildSpineFromCorridor } from "./spine.ts";
import type { Position } from "./types.ts";

function lineString(coordinates: Position[]) {
  return { type: "LineString" as const, coordinates };
}

test("computeBaseSpineHash is deterministic for identical inputs", () => {
  const coords: Position[] = [[-73.99, 40.70], [-73.98, 40.71]];
  const h1 = computeBaseSpineHash(coords);
  const h2 = computeBaseSpineHash([[-73.99, 40.70], [-73.98, 40.71]]);
  assert.equal(h1, h2);
  assert.match(h1, /^h[0-9a-z]+$/);
});

test("computeBaseSpineHash differs for different inputs", () => {
  const a = computeBaseSpineHash([[-73.99, 40.70], [-73.98, 40.71]]);
  const b = computeBaseSpineHash([[-73.99, 40.70], [-73.98, 40.72]]);
  assert.notEqual(a, b);
});

test("buildSpineFromCorridor produces a spine derived from its corridor", () => {
  const corridor = {
    properties: {
      corridor_id: "corr-00042",
      route_ids: ["B", "D"],
      source_edge_ids: ["e1", "e2"],
      "source_shape_ids": ["s1"],
      length_m: 312.45,
      base_geometry_selection: "quality_density_length",
    },
    geometry: lineString([[-73.99, 40.70], [-73.98, 40.71]]),
  };
  const spine = buildSpineFromCorridor(corridor);
  assert.equal(spine.spine_id, "spine-corr-00042");
  assert.equal(spine.base_corridor_id, "corr-00042");
  assert.deepEqual(spine.route_ids, ["B", "D"]);
  assert.equal(spine.method, "quality_density_length");
  assert.match(spine.base_spine_hash, /^h[0-9a-z]+$/);
});
