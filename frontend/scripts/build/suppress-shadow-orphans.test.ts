import { test } from "node:test";
import assert from "node:assert/strict";
import { suppressShadowOrphans } from "./suppress-shadow-orphans.ts";
import type { Position, Feature, LineStringGeometry, VisualFeatureProperties } from "./types.ts";

type LineFeature = Feature<LineStringGeometry, VisualFeatureProperties>;

const M_LAT = 1 / 110574;
const M_LON = 1 / (111320 * Math.cos((40.84 * Math.PI) / 180));
const P = (lon0: number, lat0: number, dxM: number, dyM: number): Position => [lon0 + dxM * M_LON, lat0 + dyM * M_LAT];
const O: [number, number] = [-73.86, 40.84];

function feat(
  cid: string,
  color: string,
  routeIds: string[],
  coords: Position[],
  extra: Record<string, unknown> = {},
): LineFeature {
  return { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: { corridor_id: cid, color, route_ids: routeIds, ...extra } };
}

test("drops an error-orphan green line that shadows a different-color red line", () => {
  const straight = Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30));
  const red = feat("red2", "#EE352E", ["2"], straight);
  const shadow = feat("grn5rush", "#00933C", ["5"], straight.map((c) => [...c] as Position), { qa_orphan_severity: "error" });
  const { features, removedIds } = suppressShadowOrphans([red, shadow], { shadowDistM: 18, shadowFracMin: 0.7 });
  assert.deepEqual(removedIds, ["grn5rush"]);
  assert.ok(features.find((f) => f.properties.corridor_id === "red2"));
  assert.equal(features.length, 1);
});

test("keeps a non-orphan green line that shares track with red (legit parallel pair)", () => {
  const straight = Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30));
  const red = feat("red", "#EE352E", ["2"], straight);
  const green = feat("grn", "#00933C", ["5"], straight.map((c) => [...c] as Position)); // NOT an error orphan
  const { removedIds } = suppressShadowOrphans([red, green], { shadowDistM: 18, shadowFracMin: 0.7 });
  assert.deepEqual(removedIds, []);
});

test("keeps an error-orphan that does NOT shadow another color (a real isolated stub)", () => {
  const red = feat("red", "#EE352E", ["2"], Array.from({ length: 40 }, (_, i) => P(...O, 0, i * 30)));
  const lone = feat("lone", "#00933C", ["5"], Array.from({ length: 20 }, (_, i) => P(...O, 500 + i * 30, 0)), { qa_orphan_severity: "error" });
  const { removedIds } = suppressShadowOrphans([red, lone], { shadowDistM: 18, shadowFracMin: 0.7 });
  assert.deepEqual(removedIds, []);
});
