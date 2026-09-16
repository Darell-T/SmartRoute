import { test } from "node:test";
import assert from "node:assert/strict";

import {
  applyBrightonBqChurchSpacing,
  buildBalancedPair,
  cumulativeArcs,
  fitHermiteCenterline,
  haversineM,
  interpolateAtArc,
  lengthM,
  lerpPoint,
  minSeparationM,
  normalAt,
  offsetPoint,
  orientationNeedsReverse,
  pointLineDistanceM,
  removeAdjacentDuplicates,
  replaceArcRange,
  samplePolyline,
  sliceArc,
  smoothstep,
} from "./brighton-bq-church-spacing.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

type Vector = [number, number];
type CenterlineFit = "raw_centerline" | "smoothed_raw_centerline" | "cubic_axis_fit" | "cubic_hermite_fit";

type TestProperties = {
  corridor_id: string;
  color?: string;
  route_ids: string[] | string;
  color_route_ids: string[];
  physical_bundle_id: string;
  lane_offset_baked: boolean;
  lane_slot_source: string;
  brighton_bq_church_spacing?: boolean;
  brighton_bq_church_centerline_fit?: CenterlineFit | null;
  brighton_bq_church_min_before_m?: number | null;
  brighton_bq_church_min_after_m?: number | null;
  brighton_bq_church_core_min_after_m?: number | null;
  brighton_bq_church_max_turn_after_degrees?: number | null;
};

type TestFeature = Feature<LineStringGeometry, TestProperties>;

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / 84410;

function ll(xM: number, yM: number): Position {
  return [-73.964 + xM * DEG_PER_M_LON, 40.646 + yM * DEG_PER_M_LAT];
}

function feature(id: string, color: string, routeIds: string[], coords: Position[]): TestFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: id,
      color,
      route_ids: routeIds,
      color_route_ids: routeIds,
      physical_bundle_id: "pb-00010",
      lane_offset_baked: true,
      lane_slot_source: "physical_bundle_continuous",
    },
  };
}

function xy(p: Position): Vector {
  return [(p[0] + 73.964) / DEG_PER_M_LON, (p[1] - 40.646) / DEG_PER_M_LAT];
}

function pointToSegmentDistanceM(p: Position, a: Position, b: Position): number {
  const P = xy(p);
  const A = xy(a);
  const B = xy(b);
  const vx = B[0] - A[0];
  const vy = B[1] - A[1];
  const wx = P[0] - A[0];
  const wy = P[1] - A[1];
  const t = Math.max(0, Math.min(1, (vx * wx + vy * wy) / (vx * vx + vy * vy || 1)));
  return Math.hypot(P[0] - (A[0] + vx * t), P[1] - (A[1] + vy * t));
}

function pointToLineDistanceM(point: Position, line: Position[]): number {
  let best = Infinity;
  for (let index = 1; index < line.length; index += 1) {
    best = Math.min(best, pointToSegmentDistanceM(point, line[index - 1], line[index]));
  }
  return best;
}

function minLineSeparationM(left: Position[], right: Position[]): number {
  let best = Infinity;
  for (const point of left) best = Math.min(best, pointToLineDistanceM(point, right));
  for (const point of right) best = Math.min(best, pointToLineDistanceM(point, left));
  return best;
}

test("Brighton B/Q Church spacing removes the bend pinch while preserving endpoints", () => {
  const yellow = feature("yellow-q", "#FCCC0A", ["N", "Q", "R", "W"], [
    ll(-20, -460),
    ll(-24, -260),
    ll(-18, -80),
    ll(-10, 80),
    ll(-6, 280),
    ll(-2, 460),
  ]);
  const orange = feature("orange-b", "#FF6319", ["B"], [
    ll(-7, -460),
    ll(-9, -260),
    ll(-10, -80),
    ll(-8, 80),
    ll(2, 280),
    ll(8, 460),
  ]);

  assert.ok(
    minLineSeparationM(yellow.geometry.coordinates, orange.geometry.coordinates) < 8,
    "fixture starts with a visible pinch",
  );

  const firstInput = [yellow, orange];
  const secondInput = [structuredClone(yellow), structuredClone(orange)];
  const { features, diagnostics } = applyBrightonBqChurchSpacing(firstInput, {
    bbox: { minLon: -73.966, maxLon: -73.960, minLat: 40.642, maxLat: 40.650 },
    marginM: 0,
    targetSeparationM: 13,
    blendM: 0,
    sampleM: 12,
  });
  const again = applyBrightonBqChurchSpacing(secondInput, {
    bbox: { minLon: -73.966, maxLon: -73.960, minLat: 40.642, maxLat: 40.650 },
    marginM: 0,
    targetSeparationM: 13,
    blendM: 0,
    sampleM: 12,
  });
  assert.equal(JSON.stringify(diagnostics), JSON.stringify(again.diagnostics));

  assert.equal(diagnostics.applied, true);
  assert.equal(diagnostics.centerline_fit, "cubic_hermite_fit");
  const minAfter = diagnostics.min_separation_after_m;
  const coreMinAfter = diagnostics.core_min_separation_after_m;
  const maxTurnAfter = diagnostics.max_centerline_turn_after_degrees;
  if (minAfter === null || coreMinAfter === null || maxTurnAfter === null) {
    throw new TypeError("expected numeric Brighton B/Q spacing diagnostics");
  }
  assert.ok(minAfter >= 12.5);
  assert.ok(coreMinAfter >= 12.5);
  assert.ok(
    maxTurnAfter <= 4,
    `centerline should be a clean turn, got ${maxTurnAfter}deg`,
  );

  const outYellow = features.find((item) => item.properties.corridor_id === "yellow-q");
  const outOrange = features.find((item) => item.properties.corridor_id === "orange-b");
  assert.ok(outYellow);
  assert.ok(outOrange);

  assert.deepEqual(outYellow.geometry.coordinates[0], yellow.geometry.coordinates[0]);
  assert.deepEqual(outYellow.geometry.coordinates.at(-1), yellow.geometry.coordinates.at(-1));
  assert.deepEqual(outOrange.geometry.coordinates[0], orange.geometry.coordinates[0]);
  assert.deepEqual(outOrange.geometry.coordinates.at(-1), orange.geometry.coordinates.at(-1));
  assert.ok(outYellow.properties.brighton_bq_church_spacing);
  assert.ok(outOrange.properties.brighton_bq_church_spacing);
  assert.equal(outYellow.properties.brighton_bq_church_centerline_fit, "cubic_hermite_fit");
  assert.equal(outOrange.properties.brighton_bq_church_centerline_fit, "cubic_hermite_fit");
});

test("Brighton B/Q Church spacing is a no-op when the B/Q pair is incomplete", () => {
  const yellow = feature("yellow-q", "#FCCC0A", ["Q"], [
    ll(-20, -460),
    ll(-18, -80),
    ll(-6, 280),
  ]);
  const unrelated = feature("orange-f", "#FF6319", ["F"], [
    ll(-7, -460),
    ll(-10, -80),
    ll(2, 280),
  ]);

  const { features, diagnostics } = applyBrightonBqChurchSpacing([yellow, unrelated], {
    bbox: { minLon: -73.966, maxLon: -73.960, minLat: 40.642, maxLat: 40.650 },
  });

  assert.equal(diagnostics.applied, false);
  assert.equal(diagnostics.reason, "missing_bq_features");
  assert.equal(features[0], yellow);
  assert.equal(features[1], unrelated);
});

test("Brighton B/Q Church spacing leaves an empty feature list empty", () => {
  const first = applyBrightonBqChurchSpacing([]);
  const second = applyBrightonBqChurchSpacing([]);
  assert.equal(first.diagnostics.applied, false);
  assert.deepEqual(first.features, []);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
});

test("Brighton B/Q Church spacing is a no-op when only the B or only the Q is present", () => {
  const yellow = feature("yellow-q", "#FCCC0A", ["Q"], [ll(-20, -80), ll(-6, 80)]);
  const orange = feature("orange-b", "#FF6319", ["B"], [ll(-7, -80), ll(2, 80)]);
  const onlyQ = applyBrightonBqChurchSpacing([yellow]);
  const onlyB = applyBrightonBqChurchSpacing([orange]);
  assert.equal(onlyQ.diagnostics.applied, false);
  assert.equal(onlyQ.diagnostics.reason, "missing_bq_features");
  assert.equal(onlyQ.features[0], yellow);
  assert.equal(onlyB.diagnostics.applied, false);
  assert.equal(onlyB.diagnostics.reason, "missing_bq_features");
  assert.equal(onlyB.features[0], orange);
});

test("Brighton B/Q Church spacing ignores B/Q geometry that never enters the Church Ave window", () => {
  const yellow = feature("yellow-q", "#FCCC0A", ["Q"], [ll(-2000, -2000), ll(-1900, -1900)]);
  const orange = feature("orange-b", "#FF6319", ["B"], [ll(2000, 2000), ll(2100, 2100)]);
  const { features, diagnostics } = applyBrightonBqChurchSpacing([yellow, orange], {
    bbox: { minLon: -73.966, maxLon: -73.960, minLat: 40.642, maxLat: 40.650 },
  });
  assert.equal(diagnostics.applied, false);
  assert.equal(diagnostics.reason, "missing_bq_features");
  assert.equal(features[0], yellow);
  assert.equal(features[1], orange);
});

test("Brighton B/Q Church spacing skips a pair whose Church Ave slice is a single vertex", () => {
  const yellow = feature("yellow-q", "#FCCC0A", ["Q"], [ll(-80, -80), ll(0, 0), ll(80, 80)]);
  const orange = feature("orange-b", "#FF6319", ["B"], [ll(-70, -80), ll(0, 0), ll(70, 80)]);
  const bbox = { minLon: -73.96405, maxLon: -73.96395, minLat: 40.64595, maxLat: 40.64605 };
  const { features, diagnostics } = applyBrightonBqChurchSpacing([yellow, orange], {
    bbox,
    marginM: 0,
  });
  assert.equal(diagnostics.applied, false);
  assert.equal(diagnostics.reason, "degenerate_local_segment");
  assert.equal(features[0], yellow);
  assert.equal(features[1], orange);
});

test("Brighton B/Q Church spacing preserves unrelated corridors when it rebalances B/Q", () => {
  const yellow = feature("yellow-q", "#FCCC0A", ["N", "Q", "R", "W"], [
    ll(-20, -460),
    ll(-24, -260),
    ll(-18, -80),
    ll(-10, 80),
    ll(-6, 280),
    ll(-2, 460),
  ]);
  const orange = feature("orange-b", "#FF6319", ["B"], [
    ll(-7, -460),
    ll(-9, -260),
    ll(-10, -80),
    ll(-8, 80),
    ll(2, 280),
    ll(8, 460),
  ]);
  const other = feature("red-2", "#EE352E", ["2"], [ll(400, -100), ll(400, 100)]);
  const { features, diagnostics } = applyBrightonBqChurchSpacing([yellow, orange, other], {
    bbox: { minLon: -73.966, maxLon: -73.960, minLat: 40.642, maxLat: 40.650 },
    marginM: 0,
    targetSeparationM: 13,
    blendM: 0,
    sampleM: 12,
  });
  assert.equal(diagnostics.applied, true);
  assert.equal(features[2], other);
  assert.deepEqual(features[2].geometry.coordinates, other.geometry.coordinates);
  assert.ok(Array.isArray(features[0].properties.route_ids));
  assert.ok(Array.isArray(features[1].properties.route_ids));
  assert.equal(features[0].properties.route_ids.join(","), "N,Q,R,W");
  assert.equal(features[1].properties.route_ids.join(","), "B");
});

test("Brighton B/Q Church spacing still separates a southbound B from a northbound Q", () => {
  const yellow = feature("yellow-q", "#FCCC0A", ["Q"], [
    ll(-20, -460),
    ll(-18, -80),
    ll(-6, 280),
    ll(-2, 460),
  ]);
  const orange = feature("orange-b", "#FF6319", ["B"], [
    ll(8, 460),
    ll(2, 280),
    ll(-10, -80),
    ll(-7, -460),
  ]);
  const { features, diagnostics } = applyBrightonBqChurchSpacing([yellow, orange], {
    bbox: { minLon: -73.966, maxLon: -73.960, minLat: 40.642, maxLat: 40.650 },
    marginM: 0,
    targetSeparationM: 13,
    blendM: 40,
    sampleM: 12,
    blendFromCore: true,
  });
  assert.equal(diagnostics.applied, true);
  const outYellow = features.find((item) => item.properties.corridor_id === "yellow-q");
  const outOrange = features.find((item) => item.properties.corridor_id === "orange-b");
  assert.ok(outYellow);
  assert.ok(outOrange);
  assert.ok(Array.isArray(outYellow.properties.route_ids));
  assert.ok(Array.isArray(outOrange.properties.route_ids));
  assert.equal(outYellow.properties.route_ids[0], "Q");
  assert.equal(outOrange.properties.route_ids[0], "B");
  assert.ok(outYellow.properties.brighton_bq_church_spacing);
});

test("fitHermiteCenterline falls back to raw and smoothed centerlines on short polylines", () => {
  const raw = fitHermiteCenterline([ll(0, 0), ll(1, 8), ll(2, 16)]);
  assert.equal(raw.fit, "raw_centerline");
  assert.equal(raw.coords.length, 3);

  const pinched: Position[] = [ll(0, 0), ll(40, 80), ll(-40, 80), ll(1, 2)];
  const smoothed = fitHermiteCenterline(pinched);
  assert.equal(smoothed.fit, "smoothed_raw_centerline");
  assert.deepEqual(smoothed.coords[0], pinched[0]);
  assert.deepEqual(smoothed.coords.at(-1), pinched.at(-1));
});

test("fitHermiteCenterline keeps endpoints when the start of the run points against the chord", () => {
  const hooked: Position[] = [
    ll(0, 0),
    ll(4, -40),
    ll(6, -70),
    ll(8, -40),
    ll(2, 40),
    ll(0, 90),
    ll(-2, 140),
    ll(-4, 190),
    ll(-6, 240),
    ll(-8, 290),
    ll(-10, 340),
    ll(-12, 390),
  ];
  const first = fitHermiteCenterline(hooked);
  const second = fitHermiteCenterline(hooked);
  assert.equal(first.fit, "cubic_hermite_fit");
  assert.deepEqual(first.coords[0], hooked[0]);
  assert.deepEqual(first.coords.at(-1), hooked.at(-1));
  assert.equal(JSON.stringify(first), JSON.stringify(second));
});

test("buildBalancedPair uses the full-run separation when the core window is a single sample", () => {
  const yellow = [ll(-10, -80), ll(-10, 0), ll(-10, 80)];
  const orange = [ll(10, 80), ll(10, 0), ll(10, -80)];
  const options = {
    bbox: { minLon: -73.966, maxLon: -73.960, minLat: 40.642, maxLat: 40.650 },
    marginM: 0,
    targetSeparationM: 13,
    blendM: 0,
    sampleM: 20,
    smoothingPasses: 0,
    blendFromCore: false,
    coreStartFraction: 0.5,
    coreEndFraction: 0.5,
    forcedASign: 1,
  };
  const first = buildBalancedPair(yellow, orange, options);
  const second = buildBalancedPair(yellow, orange, options);
  assert.equal(first.aSign, 1);
  assert.ok(first.minAfterM >= 12.5);
  assert.equal(first.coreMinAfterM, first.minAfterM);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
});

test("exported Brighton geometry helpers cover empty, reversed, and degenerate polylines", () => {
  const line = [ll(0, 0), ll(0, 100), ll(0, 200)];
  const reverse = [ll(8, 200), ll(8, 100), ll(8, 0)];
  const arcs = cumulativeArcs(line);
  assert.equal(haversineM(line[0], line[0]), 0);
  assert.ok(lengthM(line) > 190);
  assert.deepEqual(interpolateAtArc(line, arcs, -5), line[0]);
  assert.deepEqual(interpolateAtArc(line, arcs, arcs[arcs.length - 1] + 50), line[2]);
  assert.deepEqual(sliceArc(line, 10, 10.2), []);
  assert.equal(samplePolyline(line, 1).length, 1);
  assert.deepEqual(samplePolyline(line, 1)[0], line[0]);
  assert.equal(orientationNeedsReverse(line, reverse), true);
  assert.equal(orientationNeedsReverse(line, [ll(8, 0), ll(8, 100), ll(8, 200)]), false);
  assert.ok(minSeparationM(line, [ll(8, 0), ll(8, 200)]) > 6);
  assert.ok(pointLineDistanceM(ll(8, 100), line) > 6);
  assert.deepEqual(removeAdjacentDuplicates([line[0], line[0], line[1]]), [line[0], line[1]]);
  assert.equal(smoothstep(-1), 0);
  assert.equal(smoothstep(2), 1);
  assert.deepEqual(lerpPoint(line[0], line[2], 0.5)[0], line[0][0]);
  const n = normalAt([ll(0, 0), ll(0, 0), ll(0, 0)], 1);
  assert.deepEqual(n, [0, 0]);
  const offset = offsetPoint(line[1], [1, 0], 10);
  assert.ok(haversineM(line[1], offset) > 9);
  const replaced = replaceArcRange(line, 0, arcs[1], [ll(1, 0), ll(1, 100)]);
  assert.deepEqual(replaced[0], ll(1, 0));
  const degenerate = [ll(0, 0), ll(0, 0), ll(0, 100)];
  const zeroArc = cumulativeArcs(degenerate);
  const interpolated = interpolateAtArc(degenerate, zeroArc, 0.0001);
  assert.ok(haversineM(interpolated, degenerate[0]) < 0.02);
});

test("Brighton B/Q Church spacing ignores string route_ids and missing color", () => {
  const yellow = feature("yellow-q", "#FCCC0A", ["Q"], [ll(-20, -80), ll(-6, 80)]);
  yellow.properties.route_ids = "Q";
  const orange = feature("orange-b", "#FF6319", ["B"], [ll(-7, -80), ll(2, 80)]);
  delete orange.properties.color;
  const { diagnostics } = applyBrightonBqChurchSpacing([yellow, orange], {
    bbox: { minLon: -73.966, maxLon: -73.960, minLat: 40.642, maxLat: 40.650 },
  });
  assert.equal(diagnostics.applied, false);
  assert.equal(diagnostics.reason, "missing_bq_features");
  assert.equal(diagnostics.yellow_corridor_id, null);
  assert.equal(diagnostics.orange_corridor_id, null);
});
