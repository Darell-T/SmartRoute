import { test } from "node:test";
import assert from "node:assert/strict";
import { replaceEndpointHairpin } from "./schematic-hairpin-arc.ts";
import type { Position } from "./types.ts";

type TestFeature = {
  type: "Feature";
  properties: {
    route_id: string;
    note: string;
  };
  geometry: {
    type: "LineString";
    coordinates: Position[];
  };
};

const DEG_LAT = 1 / 110574;
const DEG_LON = 1 / (111320 * Math.cos((40.818 * Math.PI) / 180));

function p(origin: Position, dxM: number, dyM: number): Position {
  return [origin[0] + dxM * DEG_LON, origin[1] + dyM * DEG_LAT];
}

function straightLine(): Position[] {
  const origin: Position = [-73.95, 40.7];
  return Array.from({ length: 12 }, (_, i) => p(origin, i * 25, 0));
}

function endpointHairpin(): Position[] {
  const origin: Position = [-73.928, 40.818];
  const coords = [p(origin, 0, -120), p(origin, 0, -60), p(origin, 0, -10)];
  const radiusM = 18;
  for (let deg = 180; deg >= 0; deg -= 20) {
    const a = (deg * Math.PI) / 180;
    coords.push(p(origin, radiusM - radiusM * Math.cos(a), radiusM * Math.sin(a)));
  }
  coords.push(p(origin, 2 * radiusM, -10), p(origin, 2 * radiusM, -60), p(origin, 2 * radiusM, -120));
  return coords;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

test("replaceEndpointHairpin returns the same coordinates for straight endpoint geometry", () => {
  const coords = straightLine();

  const out = replaceEndpointHairpin(coords);

  assert.equal(out, coords);
});

test("replaceEndpointHairpin replaces a compact endpoint hairpin with a tangent arc", () => {
  const coords = endpointHairpin();
  const before = clone(coords);
  const [targetLon, targetLat] = coords[coords.length - 1];
  const target: Position = [targetLon, targetLat];

  const out = replaceEndpointHairpin(coords, target, undefined, { sampleM: 5 });

  assert.notEqual(out, coords);
  assert.deepEqual(coords, before, "input coordinates should not be mutated");
  assert.equal(out.length, 35);
  assert.deepEqual(out.slice(0, 4), before.slice(0, 4), "non-hairpin prefix should be preserved");
  assert.deepEqual(out[out.length - 1], target, "arc should land on the requested target point");
});

test("replaceEndpointHairpin preserves unrelated feature objects and properties", () => {
  const untouched: TestFeature = {
    type: "Feature",
    properties: { route_id: "A", note: "unrelated" },
    geometry: { type: "LineString", coordinates: straightLine() },
  };
  const hairpin: TestFeature = {
    type: "Feature",
    properties: { route_id: "5", note: "target" },
    geometry: { type: "LineString", coordinates: endpointHairpin() },
  };
  const untouchedBefore = clone(untouched);
  const hairpinProperties = hairpin.properties;

  const rewritten = {
    ...hairpin,
    geometry: {
      ...hairpin.geometry,
      coordinates: replaceEndpointHairpin(hairpin.geometry.coordinates),
    },
  };

  assert.deepEqual(untouched, untouchedBefore);
  assert.equal(untouched.properties.note, "unrelated");
  assert.equal(rewritten.properties, hairpinProperties);
  assert.notEqual(rewritten.geometry.coordinates, hairpin.geometry.coordinates);
});

test("replaceEndpointHairpin returns the same reference for short, empty, and start-only hairpins", () => {
  const short = Array.from({ length: 9 }, (_, i) => p([-73.95, 40.7], i * 10, 0));
  assert.equal(replaceEndpointHairpin(short), short);
  const empty: Position[] = [];
  assert.equal(replaceEndpointHairpin(empty), empty);
  const startHairpin = endpointHairpin().slice().reverse();
  assert.equal(replaceEndpointHairpin(startHairpin, undefined, undefined, { maxArcM: 40 }), startHairpin);
});

test("replaceEndpointHairpin uses an explicit target tangent and stays deterministic", () => {
  const coords = endpointHairpin();
  const target = coords[coords.length - 1];
  const tangent: [number, number] = [0, -1];
  const first = replaceEndpointHairpin(coords, target, tangent, { sampleM: 5, handleFrac: 0.4 });
  const second = replaceEndpointHairpin(coords, target, tangent, { sampleM: 5, handleFrac: 0.4 });
  assert.notEqual(first, coords);
  assert.deepEqual(first[first.length - 1], target);
  assert.deepEqual(first, second);
  assert.deepEqual(first.slice(0, 4), coords.slice(0, 4));
});

test("replaceEndpointHairpin leaves a long reversal outside maxArcM unchanged", () => {
  const origin: Position = [-73.928, 40.818];
  const coords = Array.from({ length: 20 }, (_, i) => p(origin, 0, i * 40));
  const out = replaceEndpointHairpin(coords, undefined, undefined, {
    minReversalDeg: 120,
    maxArcM: 30,
  });
  assert.equal(out, coords);
});
