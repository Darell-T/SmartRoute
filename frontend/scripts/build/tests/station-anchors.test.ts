import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildStationAnchors,
  splitStationAnchorCollections,
  stripRuntimeStationAnchorDebugProperties,
} from "../station-anchors/index.ts";
import { darkenHexColor } from "../mta-colors.ts";

type Position = [number, number];
type FeatureProperties = Record<string, any>;

function lineFeature(
  id: string,
  routeIds: string[],
  coordinates: Position[],
  extra: FeatureProperties = {},
) {
  return {
    type: "Feature" as const,
    properties: {
      corridor_id: id,
      physical_bundle_id: id,
      route_ids: routeIds,
      color_route_ids: routeIds,
      color: extra.color ?? "#EE352E",
      lane_offset_baked: true,
      ...extra,
    },
    geometry: {
      type: "LineString" as const,
      coordinates,
    },
  };
}

function station(
  id: string,
  name: string,
  routeIds: string[],
  coordinate: Position,
) {
  return {
    type: "Feature" as const,
    properties: {
      station_id: id,
      name,
      route_ids: routeIds,
      route_count: routeIds.length,
      is_transfer: routeIds.length > 1,
      track_bearing: 90,
    },
    geometry: {
      type: "Point" as const,
      coordinates: coordinate,
    },
  };
}

test("single-route station emits one snapped stop dot, label, and badge", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-main", ["1"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("101", "Simple", ["1"], [-73, 40.00008])],
  };

  const result = buildStationAnchors({ visual, stations });
  const collections = splitStationAnchorCollections(result.anchors);

  assert.equal(collections.dots.features.length, 1);
  assert.equal(collections.sharedStops.features.length, 0);
  assert.equal(collections.labels.features.length, 1);
  assert.equal(collections.badges.features.length, 1);
  assert.equal(collections.dots.features[0].properties.marker_type, "single_stop_dot");
  // The dot is colored to match its line (Apple-style bead on the colored line).
  assert.equal(collections.dots.features[0].properties.color, "#EE352E");
  assert.ok(collections.dots.features[0].properties.snap_distance_m < 15);
});

test("runtime station anchor stripping removes debug-only properties without mutating the debug collection", () => {
  const debugAnchors = {
    type: "FeatureCollection" as const,
    metadata: { anchor_feature_count: 1 },
    features: [
      {
        type: "Feature" as const,
        properties: {
          marker_type: "single_stop_dot",
          station_id: "101",
          debug_candidate_count: 4,
          debug_rejected_candidate_count: 2,
          debug_cluster_id: "101-cluster-0",
        },
        geometry: { type: "Point" as const, coordinates: [-73, 40] as Position },
      },
    ],
  };

  const runtimeAnchors =
    stripRuntimeStationAnchorDebugProperties(debugAnchors);

  assert.deepEqual(runtimeAnchors.metadata, debugAnchors.metadata);
  assert.equal(
    Object.hasOwn(runtimeAnchors.features[0].properties, "debug_candidate_count"),
    false,
  );
  assert.equal(
    Object.hasOwn(
      runtimeAnchors.features[0].properties,
      "debug_rejected_candidate_count",
    ),
    false,
  );
  assert.equal(
    Object.hasOwn(runtimeAnchors.features[0].properties, "debug_cluster_id"),
    false,
  );
  assert.equal(debugAnchors.features[0].properties.debug_candidate_count, 4);
  assert.equal(debugAnchors.features[0].properties.debug_cluster_id, "101-cluster-0");
});

test("same-color shared stop emits a compact shared stop dot", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-shared", ["2", "3"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("201", "Shared Red", ["2", "3"], [-73, 40.00004])],
  };

  const result = buildStationAnchors({ visual, stations });
  const shared = splitStationAnchorCollections(result.anchors).sharedStops.features;

  assert.equal(shared.length, 1);
  assert.equal(shared[0].properties.marker_type, "shared_stop_dot");
  assert.deepEqual(shared[0].properties.route_ids, ["2", "3"]);
  // Same-color multi-route stop must take the LINE color, not a neutral white
  // dot -- 2 and 3 are both red, so the shared dot is red.
  assert.equal(shared[0].properties.color, "#EE352E");
});

test("multi-color shared stop emits one normal-crossing shared stop bar", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-green", ["2", "5"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("301", "Red Green", ["2", "5"], [-73, 40.00004])],
  };

  const result = buildStationAnchors({ visual, stations });
  const shared = splitStationAnchorCollections(result.anchors).sharedStops.features;

  assert.equal(shared.length, 1);
  assert.equal(shared[0].properties.marker_type, "shared_stop_bar");
  // A genuine multi-color transfer stays a neutral white node (not a line color).
  assert.equal(shared[0].properties.color, "#f4f6f8");
  assert.equal(shared[0].geometry.type, "LineString");
  assert.equal(shared[0].geometry.coordinates.length, 2);
  assert.ok(Math.abs(shared[0].geometry.coordinates[0][0] - shared[0].geometry.coordinates[1][0]) < 0.00001);
  assert.ok(Math.abs(shared[0].geometry.coordinates[0][1] - shared[0].geometry.coordinates[1][1]) > 0.0001);
  assert.ok(Math.abs(shared[0].geometry.coordinates[0][1] - shared[0].geometry.coordinates[1][1]) < 0.00018);
  const label = splitStationAnchorCollections(result.anchors).labels.features[0];
  assert.equal(label.properties.label_anchor, "bottom");
  assert.ok(label.properties.label_offset[1] < 0);
});

test("shared stop bar spans every served lane and centers on the bundle", () => {
  // Two parallel lanes (~20m apart) at a multi-color transfer. The bar must
  // CROSS both lanes (not sit on the nearest one) and center on the geometric
  // midpoint between them, so it reads as an interchange capsule on top of the
  // lines rather than a short tick on one edge lane.
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-lane", ["2"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
      lineFeature("green-lane", ["5"], [
        [-73.001, 40.00018],
        [-72.999, 40.00018],
      ], { color: "#00933C" }),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("302", "Parallel Transfer", ["2", "5"], [-73, 40.00002])],
  };

  const result = buildStationAnchors({ visual, stations });
  const shared = splitStationAnchorCollections(result.anchors).sharedStops.features;
  const bar = shared[0];
  assert.ok(bar);
  assert.equal(bar.geometry.type, "LineString");
  const lats = (bar.geometry.coordinates as Position[]).map((coord) => coord[1]);
  const lo = Math.min(...lats);
  const hi = Math.max(...lats);
  const midLat = (lo + hi) / 2;

  assert.equal(bar.properties.marker_type, "shared_stop_bar");
  // The bar reaches BOTH lanes (40 and 40.00018), covering the whole bundle.
  assert.ok(lo <= 40 + 1e-6, `bar should reach the lane at 40, got low end ${lo}`);
  assert.ok(
    hi >= 0.00018 + 40 - 1e-6,
    `bar should reach the lane at 40.00018, got high end ${hi}`,
  );
  // Centered between the lanes (~40.00009), not pinned to the nearest one.
  assert.ok(
    Math.abs(midLat - 40.00009) < 0.00002,
    `bar should center between the lanes, got ${midLat}`,
  );
  assert.ok(
    Math.abs(bar.properties.snapped_coordinate[1] - midLat) < 1e-6,
    "snapped_coordinate matches the bar midpoint",
  );
});

test("ten-route station wraps route badges into two centered rows", () => {
  const routes = ["2", "3", "4", "5", "B", "D", "N", "Q", "R", "W"];
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("mega", routes, [
        [-73.001, 40],
        [-72.999, 40],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("401", "Mega", routes, [-73, 40.00004])],
  };

  const result = buildStationAnchors({ visual, stations });
  const badges = splitStationAnchorCollections(result.anchors).badges.features;
  const rows = new Set(badges.map((feature) => feature.properties.badge_row));

  assert.equal(badges.length, 10);
  assert.deepEqual([...rows].sort(), [0, 1]);
  assert.ok(badges.every((feature) => Array.isArray(feature.properties.icon_offset)));
  assert.ok(badges.every((feature) => feature.properties.badge_count === 10));
});

test("wrong nearby route is rejected even when geometrically closer", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("blue-near", ["A"], [
        [-73.001, 40.00001],
        [-72.999, 40.00001],
      ], { color: "#0A84FF" }),
      lineFeature("red-served", ["1"], [
        [-73.001, 40.0005],
        [-72.999, 40.0005],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("501", "Route Match", ["1"], [-73, 40])],
  };

  const result = buildStationAnchors({ visual, stations });
  const dot = splitStationAnchorCollections(result.anchors).dots.features[0];

  assert.equal(dot.properties.snapped_visual_feature_ids[0], "red-served");
  assert.ok(dot.properties.snap_distance_m > 40);
});

test("far snap candidates are rejected into ambiguous debug output", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-far", ["1"], [
        [-73.01, 40.01],
        [-73.00, 40.01],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("601", "Too Far", ["1"], [-73, 40])],
  };

  const result = buildStationAnchors({ visual, stations });

  assert.equal(result.anchors.features.length, 0);
  assert.equal(result.ambiguous.features.length, 1);
  assert.equal(result.ambiguous.features[0].properties.reason, "no_valid_projection");
});

test("single-route badge clears the label text below the station", () => {
  // Single-route stations anchor the label BELOW the point. The bullet must
  // sit BELOW the label text (Apple: name first, bullet row underneath), not
  // at the legacy +28px offset that lands inside the first text line.
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-single", ["1"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("701", "Short", ["1"], [-73, 40.00004])],
  };

  const result = buildStationAnchors({ visual, stations });
  const badge = splitStationAnchorCollections(result.anchors).badges.features[0];

  assert.ok(
    badge.properties.icon_offset[1] >= 60,
    `single-route badge y offset should clear the label (got ${badge.properties.icon_offset[1]})`,
  );
});

test("long wrapped names push the single-route badge further down", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-long", ["1"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [
      station("702", "Short", ["1"], [-73, 40.00004]),
      station(
        "703",
        "Van Cortlandt Park-242 St Terminal",
        ["1"],
        [-72.9995, 40.00004],
      ),
    ],
  };

  const result = buildStationAnchors({ visual, stations });
  const badges = splitStationAnchorCollections(result.anchors).badges.features;
  const shortBadge = badges.find((f) => f.properties.station_id === "702");
  const longBadge = badges.find((f) => f.properties.station_id === "703");
  assert.ok(shortBadge);
  assert.ok(longBadge);

  assert.ok(
    longBadge.properties.icon_offset[1] > shortBadge.properties.icon_offset[1],
    "wrapped names need a larger badge clearance",
  );
});

test("multi-route badges keep the compact below-point offset", () => {
  // Multi-route stations anchor the label ABOVE the point, so badges stay
  // close under the marker (no label to clear).
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-shared-badges", ["2", "3"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("704", "Shared", ["2", "3"], [-73, 40.00004])],
  };

  const result = buildStationAnchors({ visual, stations });
  const badges = splitStationAnchorCollections(result.anchors).badges.features;

  for (const badge of badges) {
    assert.ok(
      badge.properties.icon_offset[1] < 45,
      `multi-route badge should stay compact (got ${badge.properties.icon_offset[1]})`,
    );
  }
});

test("stop dots carry a darkened same-hue rim color", () => {
  // Apple-style bead: line-color fill with a darker rim of the SAME hue
  // (not a near-black ring). The builder bakes dot_color for the runtime.
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-rim", ["1"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
      lineFeature("red-rim-shared", ["2", "3"], [
        [-73.001, 40.002],
        [-72.999, 40.002],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [
      station("801", "Rim Single", ["1"], [-73, 40.00004]),
      station("802", "Rim Shared", ["2", "3"], [-73, 40.00204]),
    ],
  };

  const result = buildStationAnchors({ visual, stations });
  const collections = splitStationAnchorCollections(result.anchors);
  const single = collections.dots.features[0];
  const shared = collections.sharedStops.features.find(
    (f) => f.properties.marker_type === "shared_stop_dot",
  );
  assert.ok(shared);

  const expected = darkenHexColor("#EE352E", 0.45);
  assert.equal(single.properties.dot_color, expected);
  assert.equal(shared.properties.dot_color, expected);
  assert.notEqual(single.properties.dot_color, single.properties.color);
});

test("generic S shuttle stations snap onto FS/GS/H lanes", () => {
  // stations.geojson publishes the three physically distinct shuttles as a
  // plain "S"; the visual lanes carry FS/GS/H. The Rockaway Park stops were
  // dropped entirely because "S" matched no lane.
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("rockaway-shuttle", ["H"], [
        [-73.001, 40],
        [-72.999, 40],
      ], { color: "#808183" }),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("901", "Beach 90 St", ["S"], [-73, 40.00004])],
  };

  const result = buildStationAnchors({ visual, stations });
  const dots = splitStationAnchorCollections(result.anchors).dots.features;

  assert.equal(dots.length, 1);
  assert.equal(dots[0].properties.color, "#808183");
  assert.equal(result.ambiguous.features.length, 0);
});

test("a route past the strict gate is rescued even when sibling routes snap", () => {
  // Court Sq: the 7 projects at ~20m but the G terminal lane sits ~127m out.
  // The relaxed retry must run PER ROUTE, not only when the whole station
  // failed -- otherwise the G silently vanishes from its own terminal.
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("seven", ["7"], [
        [-73.001, 40],
        [-72.999, 40],
      ], { color: "#B933AD" }),
      lineFeature("g-terminal", ["G"], [
        // ~111m north of the station point: outside 90m, inside 140m.
        [-73.001, 40.001],
        [-72.999, 40.001],
      ], { color: "#6CBE45" }),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("719+G22", "Court Sq", ["7", "G"], [-73, 40.00004])],
  };

  const result = buildStationAnchors({ visual, stations });
  const collections = splitStationAnchorCollections(result.anchors);
  const dotRoutes = collections.dots.features.flatMap(
    (f) => f.properties.route_ids,
  );

  assert.ok(dotRoutes.includes("7"), "7 keeps its dot");
  assert.ok(dotRoutes.includes("G"), "G terminal dot must exist");
});

test("stations slightly past the strict snap gate fall back to a relaxed tier", () => {
  // Terminals like Wakefield-241 St sit 92-114m from their schematic lane --
  // just past the strict 90m gate. They must still get a marker (low
  // confidence) instead of disappearing from the map.
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-terminal", ["2"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [
      // ~111m north of the lane: rejected by the 90m gate, inside 140m.
      station("201x", "Wakefield-241 St", ["2"], [-73, 40.001]),
    ],
  };

  const result = buildStationAnchors({ visual, stations });
  const dots = splitStationAnchorCollections(result.anchors).dots.features;

  assert.equal(result.ambiguous.features.length, 0);
  assert.equal(dots.length, 1);
  assert.equal(dots[0].properties.snap_confidence, "low");
});
