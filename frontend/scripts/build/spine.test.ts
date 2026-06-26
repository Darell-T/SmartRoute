// frontend/scripts/build/spine.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { groupCorridorsByPhysicalTrack, selectSpine, computeBaseSpineHash, buildSpineFromCorridor } from "./spine.ts";
import type { Position } from "./types.ts";

test("groupCorridorsByPhysicalTrack returns one group per disjoint corridor", () => {
  const corridors = [
    { corridor_id: "c1", geometry: { type: "LineString", coordinates: [[0,0],[0,1]] as Position[] }, route_ids: ["B","D"] },
    { corridor_id: "c2", geometry: { type: "LineString", coordinates: [[5,5],[5,6]] as Position[] }, route_ids: ["L"] },
  ];
  const groups = groupCorridorsByPhysicalTrack(corridors, { hausdorffMaxM: 15 });
  assert.equal(groups.length, 2);
});

test("groupCorridorsByPhysicalTrack merges two corridors within Hausdorff threshold", () => {
  // Two near-parallel polylines ~5m apart (in degrees ~5e-5 lat).
  const corridors = [
    { corridor_id: "c1", geometry: { type: "LineString", coordinates: [[-73.99,40.70],[-73.99,40.71]] as Position[] }, route_ids: ["B"] },
    { corridor_id: "c2", geometry: { type: "LineString", coordinates: [[-73.9900,40.70005],[-73.9900,40.71005]] as Position[] }, route_ids: ["N"] },
  ];
  const groups = groupCorridorsByPhysicalTrack(corridors, { hausdorffMaxM: 15 });
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].corridor_ids.sort(), ["c1","c2"]);
});

test("selectSpine picks the longest corridor's geometry", () => {
  const group = {
    corridor_ids: ["c1","c2"],
    corridors: [
      { corridor_id: "c1", geometry: { type: "LineString", coordinates: [[0,0],[0,1]] as Position[] }, length_m: 111000 },
      { corridor_id: "c2", geometry: { type: "LineString", coordinates: [[0,0],[0,0.5]] as Position[] }, length_m: 55500 },
    ],
  };
  const spine = selectSpine(group);
  assert.equal(spine.base_corridor_id, "c1");
  assert.equal(spine.method, "longest_member_edge");
});

test("groupCorridorsByPhysicalTrack merges route_ids unique and sorted", () => {
  const corridors = [
    { corridor_id: "c1", geometry: { type: "LineString", coordinates: [[-73.99,40.70],[-73.99,40.71]] as Position[] }, route_ids: ["N","B"] },
    { corridor_id: "c2", geometry: { type: "LineString", coordinates: [[-73.9900,40.70005],[-73.9900,40.71005]] as Position[] }, route_ids: ["B","Q"] },
  ];
  const groups = groupCorridorsByPhysicalTrack(corridors, { hausdorffMaxM: 15 });
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].route_ids, ["B","N","Q"]);
});

test("groupCorridorsByPhysicalTrack handles many spatially disjoint corridors", () => {
  // 20 corridors far apart from each other -- bbox prefilter should reject ~all pairs.
  const corridors = Array.from({ length: 20 }, (_, i) => ({
    corridor_id: `c${i}`,
    geometry: { type: "LineString", coordinates: [[i * 10, i * 10], [i * 10, i * 10 + 0.001]] as Position[] },
    route_ids: [`R${i}`],
  }));
  const groups = groupCorridorsByPhysicalTrack(corridors, { hausdorffMaxM: 15 });
  assert.equal(groups.length, 20);
});

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
      source_shape_ids: ["s1"],
      length_m: 312.45,
      base_geometry_selection: "quality_density_length",
    },
    geometry: { type: "LineString", coordinates: [[-73.99, 40.70], [-73.98, 40.71]] as Position[] },
  };
  const spine = buildSpineFromCorridor(corridor);
  assert.equal(spine.spine_id, "spine-corr-00042");
  assert.equal(spine.base_corridor_id, "corr-00042");
  assert.deepEqual(spine.route_ids, ["B", "D"]);
  assert.equal(spine.method, "quality_density_length");
  assert.match(spine.base_spine_hash, /^h[0-9a-z]+$/);
});
