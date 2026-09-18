import assert from "node:assert/strict";
import { test } from "node:test";
import { applyGeometrySmoothingPass } from "./geometry-smoothing-pass.ts";
import type { LineFeature } from "../shared/types.ts";

function line(id: string, coordinates: Array<[number, number]>): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: { bundle_id: id, route_ids: ["1"] },
  };
}

const SMOOTH = {
  angleThresholdDeg: 25,
  iterations: 3,
  ratio: 0.2,
  maxFilletM: 18,
};

test("geometry smoothing is a no-op for missing features, short lines, and straight lines", () => {
  assert.deepEqual(
    applyGeometrySmoothingPass({ features: undefined, ...SMOOTH }),
    { smoothedFeatureCount: 0, smoothedCornerCount: 0 },
  );
  const short = [line("short", [[-73.99, 40.75], [-73.99, 40.76]])];
  assert.deepEqual(
    applyGeometrySmoothingPass({ features: short, ...SMOOTH }),
    { smoothedFeatureCount: 0, smoothedCornerCount: 0 },
  );
  const straight = [line("straight", [[-73.99, 40.75], [-73.99, 40.751], [-73.99, 40.752]])];
  const before = structuredClone(straight);
  assert.deepEqual(
    applyGeometrySmoothingPass({ features: straight, ...SMOOTH }),
    { smoothedFeatureCount: 0, smoothedCornerCount: 0 },
  );
  assert.deepEqual(straight, before);
});

test("geometry smoothing fillets a sharp elbow and pins endpoints, twice", () => {
  const elbow: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.751],
    [-73.989, 40.751],
  ];
  const first = [line("elbow", structuredClone(elbow))];
  const second = [line("elbow", structuredClone(elbow))];
  const result = applyGeometrySmoothingPass({ features: first, ...SMOOTH });
  const again = applyGeometrySmoothingPass({ features: second, ...SMOOTH });
  assert.ok(result.smoothedFeatureCount === 1);
  assert.ok(result.smoothedCornerCount >= 1);
  assert.deepEqual(first[0].geometry.coordinates[0], elbow[0]);
  assert.deepEqual(first[0].geometry.coordinates.at(-1), elbow[2]);
  assert.notDeepEqual(first[0].geometry.coordinates, elbow);
  assert.deepEqual(result, again);
  assert.deepEqual(first[0].geometry.coordinates, second[0].geometry.coordinates);
});
