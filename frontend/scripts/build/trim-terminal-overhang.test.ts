import assert from "node:assert/strict";
import { test } from "node:test";

import { trimTerminalOverhang } from "./trim-terminal-overhang.ts";
import type { Feature, FeatureCollection, LineStringGeometry, PointGeometry, Position } from "./types.ts";

// Local helpers: build features along a horizontal line at lat 40.7 where
// 0.001 deg lon ~= 84.5m. Distances below are approximate meters.
const LAT = 40.7;
const M_PER_DEG_LON = 111320 * Math.cos((LAT * Math.PI) / 180);
const lonAt = (m: number): number => -74 + m / M_PER_DEG_LON;

type TestLineProperties = {
  route_ids?: string[];
  visual_feature_type: string;
  length_m?: number;
};

type TestStationProperties = {
  station_id: string;
  name: string;
  route_ids: string[];
};

type TestLineFeature = Feature<LineStringGeometry, TestLineProperties>;
type TestStationFeature = Feature<PointGeometry, TestStationProperties>;
type TestStationCollection = FeatureCollection<TestStationFeature>;
type TestTerminal = { route: string; coord?: Position };

function lineFeature(routes: string[], fromM: number, toM: number, step = 50): TestLineFeature {
  const coordinates: Position[] = [];
  for (let m = fromM; m < toM; m += step) coordinates.push([lonAt(m), LAT]);
  coordinates.push([lonAt(toM), LAT]);
  return {
    type: "Feature",
    properties: { route_ids: routes, visual_feature_type: "bundle_lane" },
    geometry: { type: "LineString", coordinates },
  };
}

function station(name: string, routes: string[], atM: number, latOffsetM = 0): TestStationFeature {
  return {
    type: "Feature",
    properties: { station_id: name, name, route_ids: routes },
    geometry: {
      type: "Point",
      coordinates: [lonAt(atM), LAT + latOffsetM / 111320],
    },
  };
}

// GTFS-terminal entry for the helper's terminal gate.
function terminal(route: string, atM: number, latOffsetM = 0): TestTerminal {
  return { route, coord: [lonAt(atM), LAT + latOffsetM / 111320] };
}

function lengthM(feature: TestLineFeature): number {
  const cs = feature.geometry.coordinates;
  let total = 0;
  for (let i = 1; i < cs.length; i += 1) {
    total += Math.abs(cs[i][0] - cs[i - 1][0]) * M_PER_DEG_LON;
  }
  return total;
}

test("free ends are trimmed back to the outermost station plus grace", () => {
  const features = [lineFeature(["A"], 0, 1000)];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [station("s1", ["A"], 100), station("s2", ["A"], 700)],
  };

  const summary = trimTerminalOverhang({
    features,
    stations,
    terminals: [terminal("A", 100), terminal("A", 700)],
  });

  // 0..80 and 720..1000 removed (20m grace on both sides).
  const len = lengthM(features[0]);
  assert.ok(len > 600 && len < 680, `expected ~640m, got ${len}`);
  assert.equal(summary.trimmedEnds, 2);
});

test("endpoints continuing into another lane of the same route are left alone", () => {
  const features = [
    lineFeature(["A"], 0, 500),
    lineFeature(["A"], 500, 1000),
  ];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [station("s1", ["A"], 250), station("s2", ["A"], 750)],
  };

  trimTerminalOverhang({
    features,
    stations,
    terminals: [terminal("A", 250), terminal("A", 750)],
  });

  // Inner endpoints (at 500) untouched; outer ends trimmed to stations.
  const first = features[0].geometry.coordinates;
  const second = features[1].geometry.coordinates;
  assert.ok(Math.abs(first[first.length - 1][0] - lonAt(500)) < 1e-9);
  assert.ok(Math.abs(second[0][0] - lonAt(500)) < 1e-9);
  assert.ok(lengthM(features[0]) < 290);
  assert.ok(lengthM(features[1]) < 290);
});

test("shared lane trims to the farthest route's terminal, not the nearest", () => {
  // F,M share the lane; M ends at 400 but F runs to 900. The free end must
  // keep serving F to 900 (+grace), not cut at M's terminal.
  const features = [lineFeature(["F", "M"], 0, 1000)];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [
      station("m-end", ["M"], 400),
      station("f-end", ["F"], 900),
      station("start", ["F", "M"], 50),
    ],
  };

  trimTerminalOverhang({
    features,
    stations,
    terminals: [terminal("M", 400), terminal("F", 900), terminal("F", 50)],
  });

  // Trimmed to F's terminal at 900 plus 20m grace -> ~920m kept. M's closer
  // terminal at 400 must NOT shorten the shared lane.
  const len = lengthM(features[0]);
  assert.ok(len > 905 && len < 935, `expected ~920m kept, got ${len}`);
});

test("features with no projecting stations are untouched", () => {
  const features = [lineFeature(["A"], 0, 1000)];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [station("far", ["A"], 500, 800)], // 800m lateral: ignored
  };

  trimTerminalOverhang({ features, stations });
  assert.ok(lengthM(features[0]) > 990);
});

test("generic S shuttle stations anchor trims on FS/GS/H lanes", () => {
  const features = [lineFeature(["H"], 0, 1000)];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [station("rock", ["S"], 200), station("rock2", ["S"], 800)],
  };

  const summary = trimTerminalOverhang({
    features,
    stations,
    terminals: [terminal("S", 200), terminal("S", 800)],
  });
  assert.equal(summary.trimmedEnds, 2);
  const len = lengthM(features[0]);
  assert.ok(len > 580 && len < 680, `expected ~640m, got ${len}`);
});

test("mid-service geometry is never cut when the boundary is not a terminal", () => {
  // The Nostrand regression: stations.geojson lists the branch stations as
  // weekday "2" only, so a [5] lane finds no route-matched anchor for 4km and
  // the old logic ate the whole branch. The terminal gate must deny any cut
  // whose boundary station is not a true GTFS terminal of the lane's routes.
  const features = [lineFeature(["5"], 0, 5000)];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [
      station("president", ["2"], 500),
      station("sterling", ["2"], 1200),
      station("church", ["2"], 2400),
      station("franklin", ["5"], 4200),
    ],
  };

  const summary = trimTerminalOverhang({
    features,
    stations,
    // The 5's true terminal projects at the line START (Brooklyn College),
    // nowhere near the 4200m boundary the station anchors produce.
    terminals: [terminal("5", 0)],
  });

  assert.equal(summary.trimmedEnds, 0);
  assert.ok(lengthM(features[0]) > 4950, "branch must keep its full length");
});

test("stationless spur features hanging off the network are dropped", () => {
  // Yard leads / non-revenue tails are often emitted as their own feature:
  // attached to the revenue lane at one end, free at the other, with no
  // station anywhere along them. They must disappear entirely.
  const main = lineFeature(["A"], 0, 1000);
  const spur = lineFeature(["A"], 1000, 2400);
  const features = [main, spur];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [station("s1", ["A"], 100), station("s2", ["A"], 950)],
  };

  const summary = trimTerminalOverhang({ features, stations });

  assert.equal(summary.droppedSpurs, 1);
  assert.equal(features.length, 1);
  assert.equal(features[0], main);
});

test("legs carrying other-route stations are never dropped as spurs", () => {
  // The Far Rockaway regression: the A's Rockaway Park leg has stations along
  // it, but stations.geojson lists them as route "S" (the shuttle). Route-
  // matched station checks see a "stationless" 2.9km piece with one free end
  // and delete revenue track. ANY station along a feature must veto the drop.
  const main = lineFeature(["A"], 0, 1000);
  const leg = lineFeature(["A"], 1000, 3900);
  const features = [main, leg];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [
      station("s1", ["A"], 500),
      // Shuttle-listed stations along the leg (no route overlap with "A").
      station("beach90", ["S"], 1800),
      station("beach105", ["S"], 3000),
    ],
  };

  const summary = trimTerminalOverhang({ features, stations });

  assert.equal(summary.droppedSpurs, 0);
  assert.equal(features.length, 2, "the revenue leg must survive");
});

test("stationless connectors attached at both ends are kept", () => {
  // Junction connectors / bridges legitimately have no stations; they are
  // attached to the network at BOTH ends and must survive.
  const a = lineFeature(["A"], 0, 1000);
  const connector = lineFeature(["A"], 1000, 1400);
  const b = lineFeature(["A"], 1400, 2400);
  const features = [a, connector, b];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [
      station("s1", ["A"], 500),
      station("s2", ["A"], 2000),
    ],
  };

  const summary = trimTerminalOverhang({ features, stations });

  assert.equal(summary.droppedSpurs, 0);
  assert.equal(features.length, 3);
});

test("merge ends touching a sparse-vertex trunk mid-segment are attached", () => {
  // The B/D 6th Av merge regression: a connector ends a few meters from the
  // MIDDLE of a long trunk segment whose vertices are hundreds of meters
  // apart. Vertex-distance says "free" and the stationless connector gets
  // dropped; segment-distance must say "attached" and keep it.
  const trunk: TestLineFeature = {
    type: "Feature",
    properties: { route_ids: ["B", "D", "F"], visual_feature_type: "bundle_lane" },
    geometry: {
      type: "LineString",
      // Two vertices 1000m apart -- nothing within 25m of the join point's
      // nearest VERTEX, but the segment passes right under it.
      coordinates: [[lonAt(0), LAT], [lonAt(1000), LAT]],
    },
  };
  // Stationless B/D merge connector: one end touches the trunk mid-segment
  // (at ~500m along it), the other end is genuinely free.
  const connector: TestLineFeature = {
    type: "Feature",
    properties: { route_ids: ["B", "D"], visual_feature_type: "bundle_lane" },
    geometry: {
      type: "LineString",
      coordinates: [
        [lonAt(500), LAT + 5 / 111320], // 5m from the trunk segment
        [lonAt(500), LAT + 400 / 111320],
      ],
    },
  };
  const features = [trunk, connector];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [
      station("t1", ["F"], 100),
      station("t2", ["F"], 900),
      // CPW-side station keeps the connector's far end anchored too.
      station("cpw", ["B", "D"], 500, 420),
    ],
  };

  trimTerminalOverhang({ features, stations });

  assert.equal(features.length, 2, "merge connector must not be dropped");
  // The trunk-side end is ATTACHED (touches the trunk segment) -- the
  // connector must keep its full length, not be cut back to a stub around
  // its only anchoring station.
  const kept = connector.geometry.coordinates;
  const keptLen = Math.abs(kept[kept.length - 1][1] - kept[0][1]) * 111320;
  assert.ok(
    keptLen > 350,
    "connector should stay ~400m, got " + Math.round(keptLen) + "m",
  );
});

test("small overhangs below the threshold are not trimmed", () => {
  const features = [lineFeature(["A"], 0, 1000)];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [station("s1", ["A"], 30), station("s2", ["A"], 980)],
  };

  const summary = trimTerminalOverhang({
    features,
    stations,
    terminals: [terminal("A", 30), terminal("A", 980)],
  });
  assert.equal(summary.trimmedEnds, 0);
  assert.ok(lengthM(features[0]) > 990);
});

test("terminal overhang is a no-op for empty features and malformed station documents", () => {
  const emptyFeatures: TestLineFeature[] = [];
  const first = trimTerminalOverhang({
    features: emptyFeatures,
    stations: { type: "FeatureCollection", features: [] },
  });
  const second = trimTerminalOverhang({
    features: emptyFeatures,
    stations: null,
  });
  assert.equal(first.trimmedEnds, 0);
  assert.equal(first.droppedSpurs, 0);
  assert.deepEqual(emptyFeatures, []);
  assert.equal(JSON.stringify(first), JSON.stringify(second));

  const kept = [lineFeature(["A"], 0, 400)];
  const before = structuredClone(kept[0].geometry.coordinates);
  trimTerminalOverhang({
    features: kept,
    stations: { type: "NotACollection" },
  });
  assert.deepEqual(kept[0].geometry.coordinates, before);
});

test("terminal overhang updates length_m and skips empty-route and non-line features", () => {
  const kept = lineFeature(["A"], 0, 1000);
  kept.properties.length_m = 1000;
  const point = {
    type: "Feature" as const,
    properties: { route_ids: ["A"], visual_feature_type: "bundle_lane" },
    geometry: { type: "Point" as const, coordinates: [lonAt(500), LAT] },
  };
  const noRoutes = lineFeature([], 0, 400);
  // SAFETY: production skips Point geometry and empty route_ids after runtime checks.
  const features = [kept, point, noRoutes] as TestLineFeature[];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [station("s1", ["A"], 100), station("s2", ["A"], 700)],
  };
  const summary = trimTerminalOverhang({
    features,
    stations,
    terminals: [terminal("A", 100), terminal("A", 700)],
    options: { graceM: 20, minTrimM: 40 },
  });
  assert.equal(summary.trimmedEnds, 2);
  assert.ok((kept.properties.length_m ?? 0) < 700);
  assert.equal(features.length, 3);
  assert.deepEqual(noRoutes.geometry.coordinates[0], [lonAt(0), LAT]);
});

test("terminal overhang matches SIR aliases and skips malformed station records", () => {
  const features = [lineFeature(["SI"], 0, 1000)];
  const stations = {
    type: "FeatureCollection",
    features: [
      null,
      [1, 2],
      "skip",
      { geometry: { coordinates: ["x", 40.7] } },
      { properties: { route_ids: "SI" } },
      station("tottenville", ["SIR"], 120),
      station("st-george", ["SIR"], 780),
    ],
  };
  const summary = trimTerminalOverhang({
    features,
    stations,
    terminals: [terminal("SIR", 120), terminal("SIR", 780)],
  });
  assert.equal(summary.trimmedEnds, 2);
  const len = lengthM(features[0]);
  assert.ok(len > 600 && len < 720, `expected SIR trim, got ${len}`);
});

test("terminal overhang ignores zero-length vertices and junction-capture stations", () => {
  const trunk: TestLineFeature = {
    type: "Feature",
    properties: { route_ids: ["A"], visual_feature_type: "bundle_lane" },
    geometry: {
      type: "LineString",
      coordinates: [
        [lonAt(0), LAT],
        [lonAt(0), LAT],
        [lonAt(500), LAT],
        [lonAt(1000), LAT],
      ],
    },
  };
  const connector: TestLineFeature = {
    type: "Feature",
    properties: { route_ids: ["A"], visual_feature_type: "bundle_lane" },
    geometry: {
      type: "LineString",
      coordinates: [
        [lonAt(0), LAT],
        [lonAt(0), LAT + 400 / 111320],
      ],
    },
  };
  const features = [trunk, connector];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [
      station("s1", ["A"], 80),
      station("s2", ["A"], 900),
      station("off-junction", ["A"], 0, 80),
    ],
  };
  trimTerminalOverhang({
    features,
    stations,
    terminals: [terminal("A", 80), terminal("A", 900)],
  });
  assert.equal(features.length, 2);
  assert.ok(Math.abs(trunk.geometry.coordinates[0][0] - lonAt(0)) < 1e-6 || lengthM(trunk) > 400);
});

test("terminal overhang skips terminals whose coordinates are missing", () => {
  const features = [lineFeature(["A"], 0, 1000)];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [station("s1", ["A"], 100), station("s2", ["A"], 700)],
  };
  const summary = trimTerminalOverhang({
    features,
    stations,
    terminals: [{ route: "A" }],
  });
  assert.equal(summary.trimmedEnds, 0);
  assert.ok(lengthM(features[0]) > 990);
});

test("terminal overhang expands unknown routes, skips a far neighbor, and uses default grace", () => {
  const kept = lineFeature(["Z"], 0, 1000);
  const far = lineFeature(["Z"], 5000, 6000);
  const noIds = lineFeature(["A"], 0, 400);
  delete noIds.properties.route_ids;
  const features = [kept, far, noIds];
  const stations: TestStationCollection = {
    type: "FeatureCollection",
    features: [station("s1", ["Z"], 120), station("s2", ["Z"], 780)],
  };
  const summary = trimTerminalOverhang({
    features,
    stations,
    terminals: [terminal("Z", 120), terminal("Z", 780)],
    options: { minTrimM: 40 },
  });
  assert.equal(summary.trimmedEnds, 2);
  assert.ok(lengthM(kept) < 900);
  assert.ok(lengthM(far) > 990);
  assert.deepEqual(noIds.geometry.coordinates[0], [lonAt(0), LAT]);
});

test("garbage station documents and child entries never trim a lane", () => {
  const features = [lineFeature(["A"], 0, 1000)];
  const original = features[0].geometry.coordinates.map((coord) => [...coord]);
  const skipped = [
    trimTerminalOverhang({ features, stations: null, terminals: [terminal("A", 100)] }),
    trimTerminalOverhang({ features, stations: [], terminals: [terminal("A", 100)] }),
    trimTerminalOverhang({
      features,
      stations: {
        type: "FeatureCollection",
        features: [
          null,
          12,
          {
            type: "Feature",
            geometry: { type: "Point", coordinates: "bad" },
            properties: { route_ids: "A" },
          },
        ],
      },
      terminals: [terminal("A", 100)],
    }),
  ];
  assert.equal(skipped.every((summary) => summary.trimmedEnds === 0), true);
  assert.deepEqual(features[0].geometry.coordinates, original);
});
