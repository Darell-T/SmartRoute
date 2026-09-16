import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  buildStationAnchors,
  splitStationAnchorCollections,
  stripRuntimeStationAnchorDebugProperties,
  subwayBulletName,
} from "../station-anchors/index.ts";
import {
  ambiguousDebugFeature,
  rawStationDebugFeature,
  rejectedDebugFeature,
} from "../station-anchors/debug-features.ts";
import { darkenHexColor } from "../mta-colors.ts";
import type {
  AnyGeometry,
  Feature,
  FeatureCollection,
  JsonValue,
  LineStringGeometry,
  PointGeometry,
} from "../types.ts";
import type { StationFeature } from "../station-anchors/types.ts";
import {
  isJsonNumber,
  isJsonObject,
  isJsonString,
  parsedJson,
  propertyKey,
  propertyNumber,
  stringListOf,
} from "../visual-network/shared/route-config.ts";

type Position = [number, number];
const ORIGIN_POINT: Position = [-73, 40];

function isPosition(value: JsonValue | undefined): value is Position {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  );
}

function isPointGeometry(value: JsonValue | undefined): value is PointGeometry {
  return isJsonObject(value) && value.type === "Point" && isPosition(value.coordinates);
}

function isLineStringGeometry(value: JsonValue | undefined): value is LineStringGeometry {
  return (
    isJsonObject(value) &&
    value.type === "LineString" &&
    Array.isArray(value.coordinates) &&
    value.coordinates.every(isPosition)
  );
}

function isFeature(value: JsonValue | undefined): value is Feature<AnyGeometry> {
  return (
    isJsonObject(value) &&
    value.type === "Feature" &&
    isJsonObject(value.properties) &&
    (isPointGeometry(value.geometry) || isLineStringGeometry(value.geometry))
  );
}

function readFeatureCollection(relativePath: string): FeatureCollection {
  const parsed = parsedJson(readFileSync(join(process.cwd(), relativePath), "utf8"));
  assert.ok(isJsonObject(parsed));
  assert.equal(parsed.type, "FeatureCollection");
  assert.ok(Array.isArray(parsed.features));
  assert.ok(parsed.features.every(isFeature));
  return { type: "FeatureCollection", features: parsed.features };
}

function pos(lon: number, lat: number): Position {
  return [lon, lat];
}
type StationTestProperties = {
  color?: string;
  corridor_id?: string;
  physical_bundle_id?: string;
  route_ids?: string[];
  color_route_ids?: string[];
  lane_offset_baked?: boolean;
};

function lineFeature(
  id: string,
  routeIds: string[],
  coordinates: Position[],
  extra: StationTestProperties = {},
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
        geometry: { type: "Point" as const, coordinates: ORIGIN_POINT },
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
  assert.ok(label.properties.label_offset);
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
  const barCoordinates = bar.geometry.coordinates;
  assert.ok(Array.isArray(barCoordinates[0]));
  const lats = barCoordinates.map((coord: Position) => coord[1]);
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
  assert.ok(bar.properties.snapped_coordinate);
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

  assert.ok(badge.properties.icon_offset);
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

  assert.ok(shortBadge.properties.icon_offset);
  assert.ok(longBadge.properties.icon_offset);
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
    assert.ok(badge.properties.icon_offset);
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

test("diamond, shuttle, and unknown route badges use the MTA icon ids", () => {
  const routes = ["6X", "7X", "FX", "FS", "SI", "ZZ"];
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("mixed-bullets", routes, [
        [-73.001, 40],
        [-72.999, 40],
      ], { color: "#808183" }),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("icon-1", "Icons", routes, [-73, 40.00004])],
  };
  const badges = splitStationAnchorCollections(
    buildStationAnchors({ visual, stations }).anchors,
  ).badges.features;
  const byRoute = Object.fromEntries(badges.map((badge) => [badge.properties.route_id, badge.properties.icon_id]));
  assert.equal(byRoute["6X"], "6d");
  assert.equal(byRoute["7X"], "7d");
  assert.equal(byRoute["FX"], "fd");
  assert.equal(byRoute["FS"], "sf");
  assert.equal(byRoute["SI"], "sir");
  assert.equal(byRoute.ZZ, "zz");
});

test("SIR station aliases snap onto SI visual lanes", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("sir", ["SI"], [
        [-73.001, 40],
        [-72.999, 40],
      ], { color: "#0039A6" }),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("sir-1", "St George", ["SIR"], [-73, 40.00004])],
  };
  const result = buildStationAnchors({ visual, stations });
  const dots = splitStationAnchorCollections(result.anchors).dots.features;
  assert.equal(dots.length, 1);
  assert.equal(dots[0].properties.route_ids[0], "SIR");
  assert.equal(result.ambiguous.features.length, 0);
});

test("unknown route color falls back to gray and color_route_ids maps still snap", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      {
        type: "Feature" as const,
        properties: {
          corridor_id: "mapped-lane",
          physical_bundle_id: "mapped-lane",
          route_ids: [],
          color_route_ids: { "#00FF00": ["ZZ"] },
        },
        geometry: {
          type: "LineString" as const,
          coordinates: [
            pos(-73.001, 40),
            pos(-72.999, 40),
          ],
        },
      },
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("zz-1", "Mystery", ["ZZ"], [-73, 40.00004])],
  };
  const result = buildStationAnchors({ visual, stations });
  const dots = splitStationAnchorCollections(result.anchors).dots.features;
  assert.equal(dots.length, 1);
  assert.equal(dots[0].properties.color, "#808183");
});

test("invalid visual geometry and non-point stations are skipped", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      {
        type: "Feature" as const,
        properties: { corridor_id: "point-not-line", route_ids: ["1"] },
        geometry: { type: "Point" as const, coordinates: pos(-73, 40) },
      },
      lineFeature("dup-vertex", ["1"], [
        [-73.001, 40],
        [-73.001, 40],
        [-72.999, 40],
      ]),
      lineFeature("no-routes", ["1"], [
        [-73.001, 40.002],
        [-72.999, 40.002],
      ], { route_ids: [], color_route_ids: [] }),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [
      {
        type: "Feature" as const,
        properties: { station_id: "line-station", name: "Nope", route_ids: ["1"] },
        geometry: {
          type: "LineString" as const,
          coordinates: [
            pos(-73, 40),
            pos(-72.999, 40),
          ],
        },
      },
      station("ok", "Ok", ["1"], [-73, 40.00004]),
      {
        type: "Feature" as const,
        properties: { name: "Bare" },
        geometry: { type: "Point" as const, coordinates: pos(-73, 40.00004) },
      },
    ],
  };
  const result = buildStationAnchors({ visual, stations });
  assert.equal(result.metadata.station_count, 3);
  assert.equal(splitStationAnchorCollections(result.anchors).dots.features.length, 1);
  assert.equal(result.raw.features.length, 2);
});

test("empty collections stay empty and split treats missing features as none", () => {
  const empty = { type: "FeatureCollection" as const, features: [] };
  const result = buildStationAnchors({ visual: empty, stations: empty });
  assert.equal(result.anchors.features.length, 0);
  assert.equal(result.metadata.station_count, 0);
  // SAFETY: split and strip treat a missing features array as empty.
  const missingFeatures = { type: "FeatureCollection" as const };
  const split = splitStationAnchorCollections(missingFeatures);
  assert.equal(split.dots.features.length, 0);
  const stripped = stripRuntimeStationAnchorDebugProperties(missingFeatures);
  assert.equal(stripped.features.length, 0);
});

test("a near-miss sibling route is rejected while the matching route still snaps", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("red-near", ["1"], [
        [-73.001, 40],
        [-72.999, 40],
      ]),
      lineFeature("red-far", ["2"], [
        [-73.01, 40.01],
        [-73.00, 40.01],
      ]),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("mix", "Mix", ["1", "2"], [-73, 40.00004])],
  };
  const result = buildStationAnchors({ visual, stations });
  assert.ok(result.rejected.features.length >= 1);
  assert.equal(result.rejected.features[0].properties.reason, "snap_distance_above_threshold");
  const dots = splitStationAnchorCollections(result.anchors).dots.features;
  assert.ok(dots.some((feature) => feature.properties.route_ids.includes("1")));
});

test("nearby same-bundle projections merge into one shared stop", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("lane-a", ["4"], [
        [-73.001, 40],
        [-72.999, 40],
      ], { physical_bundle_id: "green-lex", corridor_id: "lex-a", color: "#00933C" }),
      lineFeature("lane-b", ["5"], [
        [-73.001, 40.0004],
        [-72.999, 40.0004],
      ], { physical_bundle_id: "green-lex", corridor_id: "lex-b", color: "#00933C" }),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("lex", "Union Sq", ["4", "5"], [-73, 40.00008])],
  };
  const result = buildStationAnchors({ visual, stations });
  const shared = splitStationAnchorCollections(result.anchors).sharedStops.features;
  assert.equal(shared.length, 1);
  assert.deepEqual(shared[0].properties.route_ids, ["4", "5"]);
});

test("routes on lanes farther than the cluster merge gate stay as separate stops", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("south", ["2"], [
        [-73.001, 40],
        [-72.999, 40],
      ], { color: "#EE352E" }),
      lineFeature("north", ["5"], [
        [-73.001, 40.0009],
        [-72.999, 40.0009],
      ], { color: "#00933C" }),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("gap", "Gap", ["2", "5"], [-73, 40.00045])],
  };
  const result = buildStationAnchors({ visual, stations });
  const collections = splitStationAnchorCollections(result.anchors);
  assert.equal(collections.dots.features.length, 2);
  assert.equal(collections.sharedStops.features.length, 0);
  assert.equal(result.metadata.station_count, 1);
});

test("a string route_ids value is not a visual lane and still counts the station", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      {
        type: "Feature" as const,
        properties: { corridor_id: "s", route_ids: "1", color: "#EE352E" },
        geometry: {
          type: "LineString" as const,
          coordinates: [pos(-73.001, 40), pos(-72.999, 40)],
        },
      },
      { type: "NotAFeature" as const, properties: { route_ids: ["1"] } },
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [
      station("s1", "S1", ["1"], [-73, 40.00004]),
      12,
      null,
    ],
  };
  const result = buildStationAnchors({ visual, stations });
  assert.equal(result.metadata.visual_feature_count, 0);
  assert.equal(result.metadata.station_count, 3);
  assert.equal(result.anchors.features.length, 0);
  assert.equal(result.ambiguous.features.length, 1);
});

test("subwayBulletName maps diamond, shuttle, and SIR ids and lowercases the rest", () => {
  assert.equal(subwayBulletName("6X"), "6d");
  assert.equal(subwayBulletName("7X"), "7d");
  assert.equal(subwayBulletName("FX"), "fd");
  assert.equal(subwayBulletName("FS"), "sf");
  assert.equal(subwayBulletName("SI"), "sir");
  assert.equal(subwayBulletName("SIR"), "sir");
  assert.equal(subwayBulletName("A"), "a");
  assert.equal(subwayBulletName("  q  "), "q");
  assert.equal(subwayBulletName(""), "");
  assert.equal(subwayBulletName(), "");
  assert.equal(subwayBulletName(null), "");
});

test("visual lanes fall back through id fields and skip sparse vertices", () => {
  const sparseCoords: Array<Position | undefined> = [pos(-73.001, 40.01), undefined, pos(-72.999, 40.01)];
  const visual = {
    type: "FeatureCollection" as const,
    metadata: {
      generated_at: "2026-01-01",
      visual_geometry_source: "opendata",
      visual_geometry_source_dataset_id: "s692-irgq",
    },
    features: [
      {
        type: "Feature" as const,
        properties: { bundle_id: "only-bundle", route_ids: ["1"], color: "#EE352E" },
        geometry: { type: "LineString" as const, coordinates: [pos(-73.001, 40), pos(-72.999, 40)] },
      },
      {
        type: "Feature" as const,
        properties: { lane_group_id: "only-group", route_ids: ["2"], color: "#EE352E" },
        geometry: { type: "LineString" as const, coordinates: [pos(-73.001, 40.002), pos(-72.999, 40.002)] },
      },
      {
        type: "Feature" as const,
        properties: { source_corridor_id: "only-source", route_ids: ["3"], color: "#EE352E" },
        geometry: { type: "LineString" as const, coordinates: [pos(-73.001, 40.004), pos(-72.999, 40.004)] },
      },
      {
        type: "Feature" as const,
        properties: { segment_id: "only-segment", route_ids: ["4"], color: "#00933C" },
        geometry: { type: "LineString" as const, coordinates: [pos(-73.001, 40.006), pos(-72.999, 40.006)] },
      },
      {
        type: "Feature" as const,
        properties: { route_ids: ["5"], color: "#00933C" },
        geometry: { type: "LineString" as const, coordinates: [pos(-73.001, 40.008), pos(-72.999, 40.008)] },
      },
      {
        type: "Feature" as const,
        properties: { corridor_id: "sparse", route_ids: ["6"], color: "#00933C" },
        geometry: {
          type: "LineString" as const,
          // SAFETY: projectPointToLineString skips missing vertices after a runtime check.
          coordinates: sparseCoords as Position[],
        },
      },
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [
      station("bundle-st", "Bundle", ["1"], pos(-73, 40.00004)),
      station("group-st", "Group", ["2"], pos(-73, 40.00204)),
    ],
  };
  const result = buildStationAnchors({
    visual,
    stations,
    options: { maxSnapDistanceM: 80 },
  });
  assert.equal(result.metadata.max_snap_distance_m, 80);
  assert.equal(result.metadata.visual_generated_at, "2026-01-01");
  assert.equal(result.metadata.visual_geometry_source, "opendata");
  const dots = splitStationAnchorCollections(result.anchors).dots.features;
  assert.ok(dots.some((feature) => feature.properties.snapped_visual_feature_ids.includes("only-bundle")));
  assert.ok(dots.some((feature) => feature.properties.snapped_visual_feature_ids.includes("only-group")));
});

test("color_route_ids accepts a null-prototype map and skips class-prototype bags", () => {
  const nullProto = Object.assign(Object.create(null), { "#00FF00": ["ZZ"] });
  const classProto = Object.assign(Object.create(Date.prototype), { "#00FF00": ["YY"] });
  const mixedValues = { "#00FF00": "not-an-array", "#EE352E": ["1"] };
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      {
        type: "Feature" as const,
        properties: {
          corridor_id: "null-proto",
          route_ids: [],
          color_route_ids: nullProto,
        },
        geometry: { type: "LineString" as const, coordinates: [pos(-73.001, 40), pos(-72.999, 40)] },
      },
      {
        type: "Feature" as const,
        properties: {
          corridor_id: "class-proto",
          route_ids: [],
          color_route_ids: classProto,
        },
        geometry: { type: "LineString" as const, coordinates: [pos(-73.001, 40.002), pos(-72.999, 40.002)] },
      },
      {
        type: "Feature" as const,
        properties: {
          corridor_id: "mixed-values",
          route_ids: [],
          color_route_ids: mixedValues,
        },
        geometry: { type: "LineString" as const, coordinates: [pos(-73.001, 40.004), pos(-72.999, 40.004)] },
      },
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [
      station("zz", "NullProto", ["ZZ"], pos(-73, 40.00004)),
      station("yy", "ClassProto", ["YY"], pos(-73, 40.00204)),
      station("one", "Mixed", ["1"], pos(-73, 40.00404)),
    ],
  };
  const result = buildStationAnchors({
    visual,
    stations,
  });
  const dots = splitStationAnchorCollections(result.anchors).dots.features;
  assert.ok(dots.some((feature) => feature.properties.route_ids.includes("ZZ")));
  assert.equal(dots.some((feature) => feature.properties.route_ids.includes("YY")), false);
  assert.ok(dots.some((feature) => feature.properties.route_ids.includes("1")));
});

test("debug constructors fall back to feature id and empty name", () => {
  const station: StationFeature = {
    type: "Feature",
    id: "feat-id",
    geometry: { type: "Point", coordinates: ORIGIN_POINT },
    properties: {},
  };
  const raw = rawStationDebugFeature(station, ["1"]);
  assert.equal(raw.properties.station_id, "feat-id");
  assert.equal(raw.properties.name, "");
  const rejected = rejectedDebugFeature(station, {
    routeId: "1",
    visualFeature: {
      feature: lineFeature("lane", ["1"], [pos(-73.001, 40), pos(-72.999, 40)]),
      index: 0,
      id: "lane",
      coordinates: [pos(-73.001, 40), pos(-72.999, 40)],
      routeIds: ["1"],
      colorRouteIds: ["1"],
      allRouteIds: ["1"],
      color: "#EE352E",
      corridorId: "lane",
      physicalBundleId: "lane",
    },
    score: 99,
    coordinate: ORIGIN_POINT,
    distance_m: 99,
    segment_index: 0,
    segment_t: 0,
    tangent_bearing: 90,
  });
  assert.equal(rejected.properties.station_id, "feat-id");
  const nameless: StationFeature = {
    type: "Feature",
    geometry: { type: "Point", coordinates: ORIGIN_POINT },
    properties: { station_id: "named-id" },
  };
  const ambiguous = ambiguousDebugFeature(nameless, ["1"], "no_valid_projection");
  assert.equal(ambiguous.properties.station_id, "named-id");
  assert.equal(ambiguous.properties.name, "");
});

test("a station identified only by feature id still snaps", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [lineFeature("red-id", ["1"], [pos(-73.001, 40), pos(-72.999, 40)])],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [{
      type: "Feature" as const,
      id: "only-id",
      properties: { route_ids: ["1"] },
      geometry: { type: "Point" as const, coordinates: pos(-73, 40.00004) },
    }],
  };
  const result = buildStationAnchors({ visual, stations });
  const dots = splitStationAnchorCollections(result.anchors).dots.features;
  assert.equal(dots.length, 1);
  assert.equal(dots[0].properties.station_id, "only-id");
});

test("unknown route ids sort after MTA order and equal unknown ranks compare by name", () => {
  const visual = {
    type: "FeatureCollection" as const,
    features: [
      lineFeature("unk", ["YA", "YB"], [pos(-73.001, 40), pos(-72.999, 40)], { color: "#808183" }),
    ],
  };
  const stations = {
    type: "FeatureCollection" as const,
    features: [station("unk", "Unknowns", ["YB", "YA"], pos(-73, 40.00004))],
  };
  const result = buildStationAnchors({ visual, stations });
  const anchors = result.anchors.features.filter((feature) =>
    ["single_stop_dot", "shared_stop_dot", "shared_stop_bar"].includes(feature.properties.marker_type),
  );
  assert.ok(anchors.length >= 1);
  assert.deepEqual(anchors[0].properties.route_ids, ["YA", "YB"]);
});

test("the shipped network projects every station into a runtime or diagnostic collection", () => {
  const visual = readFeatureCollection("public/subway-network.visual.geojson");
  const stations = readFeatureCollection("public/subway-network.stations.geojson");

  const result = buildStationAnchors({ visual, stations });
  const projectedStationIds = new Set(
    [result.anchors, result.ambiguous].flatMap((collection) =>
      collection.features
        .map((feature) => feature.properties.station_id)
        .filter((stationId): stationId is string => isJsonString(stationId)),
    ),
  );
  const inputStationIds = stations.features
    .map((feature) => feature.properties.station_id)
    .filter((stationId): stationId is string => isJsonString(stationId));

  assert.equal(result.metadata.station_count, stations.features.length);
  assert.ok(inputStationIds.length > 0);
  assert.ok(inputStationIds.every((stationId) => projectedStationIds.has(stationId)));
  assert.ok(splitStationAnchorCollections(result.anchors).badges.features.length > 0);
});

test("JSON property decoders keep strings, drop non-finite numbers, and stringify the rest", () => {
  assert.equal(propertyKey("A"), "A");
  assert.equal(propertyKey(null), "");
  assert.equal(propertyKey(undefined), "");
  assert.equal(propertyKey(6), "6");
  assert.equal(propertyNumber(12.5), 12.5);
  assert.equal(propertyNumber(Number.NaN), undefined);
  assert.equal(propertyNumber("12"), undefined);
  assert.equal(isJsonNumber(1), true);
  assert.equal(isJsonNumber(null), false);
  assert.deepEqual(stringListOf(["B", 1]), ["B", "1"]);
  assert.deepEqual(stringListOf("B"), []);
  const parsed = parsedJson("{\"type\":\"FeatureCollection\",\"features\":[]}");
  assert.ok(isJsonObject(parsed));
  assert.equal(parsed.type, "FeatureCollection");
});
