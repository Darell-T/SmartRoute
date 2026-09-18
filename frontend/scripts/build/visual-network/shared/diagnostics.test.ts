import assert from "node:assert/strict";
import test from "node:test";
import {
  buildRouteIncidentCounts,
  buildVisualAnomalyRecords,
  buildVisualRouteIncidentCounts,
} from "./diagnostics.ts";
import type { LineFeature } from "./types.ts";

function line(
  corridorId: string,
  routeIds: string[],
  extra: LineFeature["properties"] = {},
  coordinates: Array<[number, number]> = [
    [-73.99, 40.75],
    [-73.99, 40.76],
  ],
): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      from_stop_id: `${corridorId}-from`,
      to_stop_id: `${corridorId}-to`,
      from_stop_name: "South",
      to_stop_name: "North",
      ...extra,
    },
  };
}

test("buildRouteIncidentCounts counts both endpoints per route", () => {
  const features = [line("c1", ["A", "C"])];
  const counts = buildRouteIncidentCounts(features);
  assert.equal(counts.get("c1-from|A")?.count, 1);
  assert.equal(counts.get("c1-to|C")?.count, 1);
  assert.equal(buildRouteIncidentCounts(features).get("c1-from|A")?.count, 1);
});

test("buildVisualRouteIncidentCounts uses source edges when present", () => {
  const edge: LineFeature = line("e1", ["Q"], {
    route_id: "Q",
    from_stop_id: "D16",
    to_stop_id: "D17",
    from_stop_name: "Prospect Park",
    to_stop_name: "Parkside Av",
    "shape_id": "gtfs-q",
  });
  const corridor = line("c-q", ["Q"], { source_edge_ids: ["e1"] });
  const edgeById = new Map([["e1", edge]]);
  const counts = buildVisualRouteIncidentCounts([corridor], edgeById);
  assert.equal(counts.get("D16|Q")?.count, 1);
  assert.equal(counts.get("D17|Q")?.count, 1);
});

test("buildVisualAnomalyRecords keeps sparse long slices as hard defects", () => {
  const sparse = line(
    "sparse-1",
    ["2"],
    { source_edge_ids: [] },
    [
      [-73.99, 40.7],
      [-73.95, 40.8],
    ],
  );
  const records = buildVisualAnomalyRecords([sparse], new Map(), {
    maxSegmentAnomalyM: 250,
    sparseLongSliceM: 600,
    projectionAnomalyM: 125,
  });
  assert.equal(records.length, 1);
  assert.ok(records[0].reasons.includes("sparse_long_slice"));
  assert.deepEqual(records[0].gtfsPolylineIds, []);
});

test("buildVisualAnomalyRecords returns empty for a short well-sampled fragment", () => {
  const short = line("ok-1", ["G"], {}, [
    [-73.99, 40.75],
    [-73.99005, 40.75004],
    [-73.9901, 40.75008],
    [-73.99015, 40.75012],
  ]);
  const records = buildVisualAnomalyRecords([short], new Map(), {
    maxSegmentAnomalyM: 250,
    sparseLongSliceM: 600,
    projectionAnomalyM: 125,
  });
  assert.deepEqual(records, []);
});

test("buildRouteIncidentCounts can count source-edge route_id instead of corridor route_ids", () => {
  const edge = line("e1", ["A", "C"], { route_id: "A" });
  const counts = buildRouteIncidentCounts([edge], true);
  assert.equal(counts.get("e1-from|A")?.count, 1);
  assert.equal(counts.get("e1-from|C"), undefined);
});

test("buildVisualRouteIncidentCounts falls back to corridor stops when source edges are missing", () => {
  const corridor = line("c-g", ["G"], { source_edge_ids: ["missing"] });
  const counts = buildVisualRouteIncidentCounts([corridor], new Map());
  assert.equal(counts.get("c-g-from|G")?.count, 1);
  assert.equal(counts.get("c-g-to|G")?.count, 1);
});

test("buildVisualAnomalyRecords records projection, family mix, sharp corners, and source shape ids", () => {
  const edge = line(
    "e-mix",
    ["A"],
    {
      route_id: "A",
      from_stop_id: "A40",
      to_stop_id: "A41",
      from_stop_name: "High",
      to_stop_name: "Low",
      "shape_id": "gtfs-a",
      from_projection_dist_m: 200,
      to_projection_dist_m: 40,
    },
  );
  const mixed = line(
    "mix-1",
    ["A", "1", "L"],
    { source_edge_ids: ["e-mix"] },
    [
      [-73.99, 40.7],
      [-73.98, 40.8],
      [-73.99, 40.7],
    ],
  );
  const records = buildVisualAnomalyRecords([mixed], new Map([["e-mix", edge]]), {
    maxSegmentAnomalyM: 250,
    sparseLongSliceM: 600,
    projectionAnomalyM: 125,
  });
  assert.equal(records.length, 1);
  assert.ok(records[0].reasons.includes("unrelated_route_family_mix"));
  assert.ok(records[0].reasons.includes("projection_gt_125m"));
  assert.ok(records[0].reasons.includes("max_segment_gt_250m"));
  assert.ok(records[0].reasons.includes("sharp_angle_gt_120deg"));
  assert.deepEqual(records[0].gtfsPolylineIds, ["gtfs-a"]);
  assert.deepEqual(records[0].stop_pairs, ["High → Low"]);
  assert.ok(records[0].severity > records[0].severity - 1);
});

test("buildVisualAnomalyRecords flags a low-detail long three-point slice and sorts by severity", () => {
  const long = line("long-1", ["G"], { source_edge_ids: [] }, [
    [-73.99, 40.7],
    [-73.985, 40.705],
    [-73.98, 40.71],
  ]);
  const sparse = line("sparse-2", ["2"], { source_edge_ids: [] }, [
    [-73.99, 40.7],
    [-73.95, 40.8],
  ]);
  const records = buildVisualAnomalyRecords([long, sparse], new Map(), {
    maxSegmentAnomalyM: 250,
    sparseLongSliceM: 600,
    projectionAnomalyM: 125,
  });
  assert.ok(records.length >= 2);
  assert.ok(records[0].severity >= records[1].severity);
  assert.ok(records.some((row) => row.reasons.includes("low_detail_straight_long_slice") || row.reasons.includes("sparse_long_slice")));
});

test("buildVisualAnomalyRecords ignores an unknown route family of size two", () => {
  const ok = line("ok-mix", ["A", "SIR-UNKNOWN"], {}, [
    [-73.99, 40.75],
    [-73.99005, 40.75004],
    [-73.9901, 40.75008],
  ]);
  const records = buildVisualAnomalyRecords([ok], new Map(), {
    maxSegmentAnomalyM: 250,
    sparseLongSliceM: 600,
    projectionAnomalyM: 125,
  });
  assert.deepEqual(records, []);
});
