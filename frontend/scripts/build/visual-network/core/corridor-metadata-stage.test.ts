import assert from "node:assert/strict";
import { test } from "node:test";
import { buildCorridorMetadataStage } from "./corridor-metadata-stage.ts";
import type { LineFeature } from "../shared/types.ts";

function corridor(
  corridorId: string,
  routeIds: string[],
  fromStop: string,
  toStop: string,
  coords: LineFeature["geometry"]["coordinates"],
): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      from_stop_id: fromStop,
      to_stop_id: toStop,
      from_stop_name: fromStop,
      to_stop_name: toStop,
    },
  };
}

test("empty corridors produce no anchors and no lane groups", () => {
  const first = buildCorridorMetadataStage({ corridorFeatures: [], junctionSnapMaxM: 25 });
  const second = buildCorridorMetadataStage({ corridorFeatures: [], junctionSnapMaxM: 25 });
  assert.equal(first.junctionSnapDiagnostics.anchorFeatures.length, 0);
  assert.equal(first.laneChainDiagnostics.lane_group_count, 0);
  assert.deepEqual(first, second);
});

test("same-stop endpoints snap onto one GTFS anchor and chain shared colors", () => {
  const a = corridor("c-a", ["A"], "A40", "A41", [
    [-73.99, 40.75],
    [-73.99, 40.751],
  ]);
  const b = corridor("c-b", ["C"], "A40", "A42", [
    [-73.99005, 40.75],
    [-73.99005, 40.752],
  ]);
  const features = [a, b];
  const result = buildCorridorMetadataStage({ corridorFeatures: features, junctionSnapMaxM: 25 });
  assert.equal(result.junctionSnapDiagnostics.anchorFeatures.length >= 1, true);
  assert.equal(a.properties.from_anchor_id, b.properties.from_anchor_id);
  assert.equal(result.laneChainDiagnostics.lane_group_count >= 1, true);

  const a2 = corridor("c-a", ["A"], "A40", "A41", [
    [-73.99, 40.75],
    [-73.99, 40.751],
  ]);
  const b2 = corridor("c-b", ["C"], "A40", "A42", [
    [-73.99005, 40.75],
    [-73.99005, 40.752],
  ]);
  const again = buildCorridorMetadataStage({ corridorFeatures: [a2, b2], junctionSnapMaxM: 25 });
  assert.deepEqual(again.junctionSnapDiagnostics.anchorFeatures.map((f) => f.properties.anchor_id),
    result.junctionSnapDiagnostics.anchorFeatures.map((f) => f.properties.anchor_id));
});

test("geometry endpoints without stop ids become opendata anchors", () => {
  const a = corridor("c-a", ["A"], "", "", [
    [-73.99, 40.75],
    [-73.99, 40.751],
  ]);
  const b = corridor("c-b", ["C"], "", "", [
    [-73.99002, 40.75],
    [-73.99002, 40.752],
  ]);
  const result = buildCorridorMetadataStage({ corridorFeatures: [a, b], junctionSnapMaxM: 25 });
  assert.ok(result.junctionSnapDiagnostics.anchorFeatures.some((feature) => feature.properties.anchor_source === "geometry_endpoint"));
  assert.ok(result.junctionSnapDiagnostics.snapFeatures.length >= 1);
});

test("skips a snap beyond junctionSnapMaxM and a coincident endpoint", () => {
  const near = corridor("c-near", ["A"], "A40", "A41", [
    [-73.99, 40.75],
    [-73.99, 40.751],
  ]);
  const far = corridor("c-far", ["C"], "A40", "A42", [
    [-73.9894, 40.75],
    [-73.9894, 40.751],
  ]);
  const coincident = corridor("c-same", ["E"], "A41", "A43", [
    [-73.99, 40.751],
    [-73.99, 40.752],
  ]);
  const result = buildCorridorMetadataStage({
    corridorFeatures: [near, far, coincident],
    junctionSnapMaxM: 25,
  });
  const farSnap = result.junctionSnapDiagnostics.snapFeatures.filter(
    (feature) => feature.properties.corridor_id === "c-far" || feature.properties.corridor_id === "c-near",
  );
  assert.equal(farSnap.length, 0);
  assert.ok(result.junctionSnapDiagnostics.anchorFeatures.length >= 1);
});

test("chains three shared-color neighbors through union-find path compression", () => {
  const a = corridor("c-a", ["A"], "A40", "A41", [
    [-73.99, 40.75],
    [-73.99, 40.751],
  ]);
  const b = corridor("c-b", ["C"], "A41", "A42", [
    [-73.99, 40.751],
    [-73.99, 40.752],
  ]);
  const c = corridor("c-c", ["E"], "A42", "A43", [
    [-73.99, 40.752],
    [-73.99, 40.753],
  ]);
  const result = buildCorridorMetadataStage({ corridorFeatures: [a, b, c], junctionSnapMaxM: 25 });
  assert.equal(a.properties.lane_group_id, b.properties.lane_group_id);
  assert.equal(b.properties.lane_group_id, c.properties.lane_group_id);
  assert.equal(result.laneChainDiagnostics.chain_slot_feature_count, 3);
});

test("undefined stop ids become geometry anchors and missing route ids stay local", () => {
  const a = corridor("c-a", ["A"], "A40", "A41", [
    [-73.99, 40.75],
    [-73.99, 40.751],
  ]);
  delete a.properties.from_stop_id;
  delete a.properties.from_stop_name;
  const b = corridor("c-b", [], "A41", "A42", [
    [-73.99, 40.751],
    [-73.99, 40.752],
  ]);
  delete b.properties.route_ids;
  const result = buildCorridorMetadataStage({ corridorFeatures: [a, b], junctionSnapMaxM: 25 });
  assert.ok(result.junctionSnapDiagnostics.anchorFeatures.length >= 1);
  assert.equal(b.properties.lane_slot_source, "local");
  assert.equal(a.properties.from_anchor_id != null, true);
});
