import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ambiguousDebugFeature,
  rawStationDebugFeature,
  rejectedDebugFeature,
  snapDebugFeature,
} from "./debug-features.ts";
import { STATION_DEBUG_MARKERS, type Projection, type StationFeature } from "./types.ts";

function station(): StationFeature {
  return {
    type: "Feature",
    id: "101",
    properties: { station_id: "101", name: "Canal St", route_ids: ["6"] },
    geometry: { type: "Point", coordinates: [-74.0, 40.72] },
  };
}

function projection(): Projection {
  return {
    coordinate: [-74.001, 40.721],
    distance_m: 12,
    segment_index: 0,
    segment_t: 0.5,
    tangent_bearing: 90,
    routeId: "6",
    visualFeature: {
      feature: {
        type: "Feature",
        id: "lane-6",
        geometry: { type: "LineString", coordinates: [[-74.0, 40.72], [-74.002, 40.73]] },
        properties: {},
      },
      index: 0,
      id: "lane-6",
      coordinates: [[-74.0, 40.72], [-74.002, 40.73]],
      routeIds: ["6"],
      colorRouteIds: ["6"],
      allRouteIds: ["6"],
      color: "#00933C",
      corridorId: "green-lex",
      physicalBundleId: "green-lex",
    },
    score: 1,
  };
}

test("debug features label raw, snap, rejected, and ambiguous station markers", () => {
  const raw = rawStationDebugFeature(station(), ["6"]);
  assert.equal(raw.properties.marker_type, STATION_DEBUG_MARKERS.raw);
  assert.deepEqual(raw.geometry, station().geometry);

  const snap = snapDebugFeature(station(), projection());
  assert.equal(snap.properties.marker_type, STATION_DEBUG_MARKERS.snap);
  assert.equal(snap.properties.snapped_visual_feature_id, "lane-6");
  assert.equal(snap.geometry.type, "LineString");

  const rejected = rejectedDebugFeature(station(), projection());
  assert.equal(rejected.properties.marker_type, STATION_DEBUG_MARKERS.rejected);
  assert.equal(rejected.properties.reason, "snap_distance_above_threshold");

  const ambiguous = ambiguousDebugFeature(station(), ["6"], "no_projection", {
    debug_candidate_count: 2,
  });
  assert.equal(ambiguous.properties.marker_type, STATION_DEBUG_MARKERS.ambiguous);
  assert.equal(ambiguous.properties.debug_candidate_count, 2);

  const again = rawStationDebugFeature(station(), ["6"]);
  assert.deepEqual(again, raw);
});

test("debug features fall back to empty ids when station properties are missing", () => {
  const bare: StationFeature = {
    type: "Feature",
    properties: {},
    geometry: { type: "Point", coordinates: [-74, 40] },
  };
  const raw = rawStationDebugFeature(bare, []);
  assert.equal(raw.properties.station_id, "");
  assert.equal(raw.properties.name, "");
});
