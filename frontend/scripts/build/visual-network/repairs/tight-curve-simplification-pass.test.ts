import assert from "node:assert/strict";
import { test } from "node:test";
import { applyTightCurveSimplificationPass } from "./tight-curve-simplification-pass.ts";
import type { LineFeature, Position } from "../shared/types.ts";

const DEG_LAT = 1 / 110574;
const DEG_LON = 1 / (111320 * Math.cos((40.818 * Math.PI) / 180));

function P(origin: Position, dxM: number, dyM: number): Position {
  return [origin[0] + dxM * DEG_LON, origin[1] + dyM * DEG_LAT];
}

function tightHairpin(): Position[] {
  const origin: Position = [-73.928, 40.818];
  const pts = [P(origin, 0, -120), P(origin, 0, -60), P(origin, 0, -10)];
  const radiusM = 18;
  for (let deg = 180; deg >= 0; deg -= 20) {
    const a = (deg * Math.PI) / 180;
    pts.push(P(origin, radiusM - radiusM * Math.cos(a), radiusM * Math.sin(a)));
  }
  pts.push(P(origin, 2 * radiusM, -10), P(origin, 2 * radiusM, -60), P(origin, 2 * radiusM, -120));
  return pts;
}

function line(id: string, coordinates: Position[]): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: { bundle_id: id, route_ids: ["5"] },
  };
}

const TIGHT = {
  tightTurnDeg: 70,
  windowM: 50,
  iterations: 16,
  lambda: 0.5,
};

test("tight-curve pass is a no-op for missing features and straight lines", () => {
  assert.deepEqual(
    applyTightCurveSimplificationPass({ features: undefined, ...TIGHT }),
    { tightCurveFeatureCount: 0 },
  );
  const straight = [line("straight", [
    [-73.98, 40.69],
    [-73.98, 40.691],
    [-73.98, 40.692],
    [-73.98, 40.693],
    [-73.98, 40.694],
  ])];
  const before = structuredClone(straight);
  assert.deepEqual(
    applyTightCurveSimplificationPass({ features: straight, ...TIGHT }),
    { tightCurveFeatureCount: 0 },
  );
  assert.deepEqual(straight, before);
});

test("tight-curve pass rounds a hairpin, pins endpoints, and is deterministic", () => {
  const hairpin = tightHairpin();
  const first = [line("hairpin", structuredClone(hairpin))];
  const second = [line("hairpin", structuredClone(hairpin))];
  const result = applyTightCurveSimplificationPass({ features: first, ...TIGHT });
  const again = applyTightCurveSimplificationPass({ features: second, ...TIGHT });
  assert.equal(result.tightCurveFeatureCount, 1);
  assert.deepEqual(first[0].geometry.coordinates[0], hairpin[0]);
  assert.deepEqual(first[0].geometry.coordinates.at(-1), hairpin.at(-1));
  assert.notDeepEqual(first[0].geometry.coordinates, hairpin);
  assert.deepEqual(result, again);
  assert.deepEqual(first[0].geometry.coordinates, second[0].geometry.coordinates);
});

test("tight-curve pass skips empty lists, point features, and polylines shorter than 5 vertices", () => {
  assert.deepEqual(
    applyTightCurveSimplificationPass({ features: [], ...TIGHT }),
    { tightCurveFeatureCount: 0 },
  );
  const point = line("point", [[-73.98, 40.69], [-73.98, 40.691]]);
  // SAFETY: the pass skips non-LineString geometry after a runtime type check.
  (point.geometry as { type: string }).type = "Point";
  const short = [line("short", [[-73.98, 40.69], [-73.98, 40.691], [-73.98, 40.692], [-73.98, 40.693]])];
  const mixed = [point, ...short];
  assert.deepEqual(
    applyTightCurveSimplificationPass({ features: mixed, ...TIGHT }),
    { tightCurveFeatureCount: 0 },
  );
  assert.deepEqual(short[0].geometry.coordinates.length, 4);
});
