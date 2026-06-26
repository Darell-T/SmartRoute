import { test } from "node:test";
import assert from "node:assert/strict";
import { dedupeDuplicateCorridors } from "./dedupe-duplicate-corridors.ts";

type Coordinate = [number, number];

const M_LAT = 1 / 111320;
const M_LON = 1 / (111320 * Math.cos((40.71 * Math.PI) / 180));
const P = (lon0: number, lat0: number, dxM: number, dyM: number): Coordinate => [
  lon0 + dxM * M_LON,
  lat0 + dyM * M_LAT,
];
const O: Coordinate = [-74.008, 40.713];

function corr(id: string, routeIds: string[], coords: Coordinate[]) {
  return {
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates: coords },
    properties: { corridor_id: id, route_ids: routeIds, color: "#0A84FF" },
  };
}

test("collapses a short corridor that runs ~17m parallel to a longer same-route corridor", () => {
  const main = corr("main", ["A", "C", "E"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const dup = corr("dup", ["E"], Array.from({ length: 18 }, (_, i) => P(...O, 17, 100 + i * 30))); // 17m east, overlaps main's middle
  const { features, removedIds } = dedupeDuplicateCorridors([main, dup], {
    parallelDistM: 25,
    overlapRatioMin: 0.8,
  });
  assert.deepEqual(removedIds, ["dup"], "the duplicate sub-corridor is removed");
  assert.ok(features.find((f) => f.properties?.corridor_id === "main"), "the longer corridor survives");
  assert.equal(features.length, 1);
});

test("keeps a genuine divergent branch of the same route (not a duplicate)", () => {
  const main = corr("main", ["E"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const branch = corr("branch", ["E"], Array.from({ length: 20 }, (_, i) => P(...O, i * 60, 1200))); // peels east, not parallel
  const { removedIds } = dedupeDuplicateCorridors([main, branch], {
    parallelDistM: 25,
    overlapRatioMin: 0.8,
  });
  assert.deepEqual(removedIds, [], "a divergent same-route branch must be kept");
});

test("does not dedupe corridors of different routes", () => {
  const a = corr("a", ["A"], Array.from({ length: 30 }, (_, i) => P(...O, 0, i * 30)));
  const b = corr("b", ["C"], Array.from({ length: 30 }, (_, i) => P(...O, 17, i * 30)));
  const { removedIds } = dedupeDuplicateCorridors([a, b], {
    parallelDistM: 25,
    overlapRatioMin: 0.8,
  });
  assert.deepEqual(removedIds, []);
});

test("removes an id-less duplicate by object identity", () => {
  const main = corr("main", ["A", "C", "E"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const dupWithId = corr("dup", ["E"], Array.from({ length: 18 }, (_, i) => P(...O, 17, 100 + i * 30)));
  const dup = {
    ...dupWithId,
    properties: { route_ids: dupWithId.properties.route_ids, color: dupWithId.properties.color },
  };

  const { features, removedIds } = dedupeDuplicateCorridors([main, dup], {
    parallelDistM: 25,
    overlapRatioMin: 0.8,
  });

  assert.deepEqual(removedIds, [], "id-less removals are not exposed as diagnostic ids");
  assert.equal(features.length, 1);
  assert.equal(features[0], main);
});
