import { test } from "node:test";
import assert from "node:assert/strict";
import { bridgeRouteGaps } from "./bridge-route-gaps.mjs";

const DEG_PER_M_LAT = 1 / 111320;
const DEG_PER_M_LON = 1 / (111320 * Math.cos((40.68 * Math.PI) / 180));
const P = (lon0, lat0, dxM, dyM) => [lon0 + dxM * DEG_PER_M_LON, lat0 + dyM * DEG_PER_M_LAT];

function line(routeIds, coords, extra = {}) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: { route_ids: routeIds, color: "#6CBE45", ...extra },
  };
}

const O = [-73.99, 40.68];

test("extends a dangling endpoint across a small same-route gap without adding a separate rendered connector", () => {
  const a = line(["G"], [P(...O, 0, 0), P(...O, 0, 100)]);
  const b = line(["G"], [P(...O, 0, 116), P(...O, 0, 220)]); // 16m gap from a's end
  const { features, bridgeCount } = bridgeRouteGaps([a, b], { minGapM: 6, maxGapM: 28 });
  assert.equal(bridgeCount, 1, "should repair exactly one seam");
  assert.equal(features.length, 2, "repair is integrated into an existing line, not appended as a mini-line");
  assert.equal(features.filter((f) => f.properties.route_gap_bridge).length, 0);
  assert.deepEqual(features[0].properties.route_ids, ["G"]);
  assert.ok(features[0].properties.route_gap_integrated);
  assert.deepEqual(features[0].geometry.coordinates.at(-1), b.geometry.coordinates[0]);
});

test("does NOT bridge a gap larger than maxGapM (avoids chord-cutting real gaps)", () => {
  const a = line(["G"], [P(...O, 0, 0), P(...O, 0, 100)]);
  const b = line(["G"], [P(...O, 0, 300), P(...O, 0, 400)]); // 200m gap
  const { bridgeCount } = bridgeRouteGaps([a, b], { minGapM: 6, maxGapM: 28 });
  assert.equal(bridgeCount, 0);
});

test("does NOT bridge endpoints that are already joined (< minGapM)", () => {
  const a = line(["G"], [P(...O, 0, 0), P(...O, 0, 100)]);
  const b = line(["G"], [P(...O, 0, 102), P(...O, 0, 200)]); // 2m -> already joined
  const { bridgeCount } = bridgeRouteGaps([a, b], { minGapM: 6, maxGapM: 28 });
  assert.equal(bridgeCount, 0);
});

test("does NOT bridge across different routes that happen to be near", () => {
  const g = line(["G"], [P(...O, 0, 0), P(...O, 0, 100)]);
  const f = line(["F"], [P(...O, 0, 116), P(...O, 0, 220)], { color: "#FF6319" });
  const { bridgeCount } = bridgeRouteGaps([g, f], { minGapM: 6, maxGapM: 28 });
  assert.equal(bridgeCount, 0, "different routes must not be welded together");
});

test("does NOT bridge broad bundle route_ids when active color lanes do not match", () => {
  const eLane = line(
    ["E", "F", "G"],
    [P(...O, 0, 0), P(...O, 0, 100)],
    { color: "#0A84FF", color_route_ids: ["E"] },
  );
  const gLane = line(
    ["E", "F", "G"],
    [P(...O, 0, 116), P(...O, 0, 220)],
    { color: "#6CBE45", color_route_ids: ["G"] },
  );
  const { bridgeCount } = bridgeRouteGaps([eLane, gLane], { minGapM: 6, maxGapM: 28 });
  assert.equal(
    bridgeCount,
    0,
    "broad shared bundle route_ids must not weld different active color lanes",
  );
});

test("bridges a close same-route seam even when the dangling source was flagged as an orphan", () => {
  const orphan = line(["E"], [P(...O, 0, 0), P(...O, 0, 100)], { qa_orphan_severity: "error" });
  const spine = line(["E"], [P(...O, 0, 116), P(...O, 0, 220)]);
  const { features, bridgeCount } = bridgeRouteGaps([orphan, spine], { minGapM: 6, maxGapM: 28 });
  assert.equal(bridgeCount, 1, "repairable orphan-classified same-route seams should be bridged");
  assert.equal(features.filter((f) => f.properties.route_gap_bridge).length, 0);
  assert.deepEqual(features[0].properties.route_ids, ["E"]);
  assert.ok(features[0].properties.route_gap_integrated);
});

test("never mutates or drops the input features", () => {
  const a = line(["G"], [P(...O, 0, 0), P(...O, 0, 100)]);
  const b = line(["G"], [P(...O, 0, 116), P(...O, 0, 220)]);
  const before = JSON.stringify([a, b]);
  const { features } = bridgeRouteGaps([a, b], {});
  assert.equal(JSON.stringify([a, b]), before, "inputs unchanged");
  assert.equal(features.length, 2, "features are repaired in place conceptually, not duplicated");
});

test("dedupes: a single 16m gap yields one bridge, not one per endpoint", () => {
  const a = line(["G"], [P(...O, 0, 0), P(...O, 0, 100)]);
  const b = line(["G"], [P(...O, 0, 116), P(...O, 0, 220)]);
  const { bridgeCount } = bridgeRouteGaps([a, b], { minGapM: 6, maxGapM: 28 });
  assert.equal(bridgeCount, 1);
});

test("bridges a hard-corner same-route gap with a sampled curved connector", () => {
  const northbound = line(["G"], [P(...O, 0, 0), P(...O, 0, 100)]);
  const eastbound = line(["G"], [P(...O, 16, 100), P(...O, 120, 100)]);
  const { features, bridgeCount } = bridgeRouteGaps([northbound, eastbound], {
    minGapM: 6,
    maxGapM: 28,
    maxJoinTurnDeg: 60,
  });
  assert.equal(bridgeCount, 1, "small same-route hard-corner gaps should be closed");
  assert.equal(features.filter((f) => f.properties.route_gap_bridge).length, 0);
  assert.ok(features[0].geometry.coordinates.length > northbound.geometry.coordinates.length);
  assert.equal(features[0].properties.route_gap_bridge_curved, true);
});

test("repairs the route-safe side when only one direction has the shared active route set", () => {
  const branch = line(["4", "5"], [P(...O, 0, 0), P(...O, 0, 100)]);
  const trunk = line(["5"], [P(...O, 16, 100), P(...O, 120, 100)]);
  const { features, bridgeCount } = bridgeRouteGaps([branch, trunk], {
    minGapM: 6,
    maxGapM: 28,
  });
  assert.equal(bridgeCount, 1);
  assert.equal(features.filter((f) => f.properties.route_gap_bridge).length, 0);
  assert.deepEqual(features[1].properties.route_ids, ["5"]);
  assert.ok(features[1].properties.route_gap_integrated);
  assert.equal(features[0].properties.route_gap_integrated, undefined);
});

test("integrated repair updates length metadata to the repaired geometry", () => {
  const a = line(["G"], [P(...O, 0, 0), P(...O, 0, 100)], { length_m: 1000 });
  const b = line(["G"], [P(...O, 0, 116), P(...O, 0, 220)], { length_m: 1000 });
  const { features } = bridgeRouteGaps([a, b], { minGapM: 6, maxGapM: 28 });
  assert.ok(features[0].properties.length_m > 100 && features[0].properties.length_m < 130);
});

test("adds a route-subset connector for same-color broad branch splits without extending either broad route set", () => {
  const astoriaBranch = line(
    ["N", "W"],
    [P(...O, 0, 0), P(...O, 0, 100)],
    { color: "#FCCC0A", color_route_ids: ["N", "W"] },
  );
  const queensSharedRun = line(
    ["N", "R"],
    [P(...O, 0, 183), P(...O, 0, 260)],
    { color: "#FCCC0A", color_route_ids: ["N", "R"] },
  );

  const { features, bridgeCount } = bridgeRouteGaps([astoriaBranch, queensSharedRun], {
    minGapM: 6,
    maxGapM: 28,
    allowSubsetRouteConnectors: true,
    subsetConnectorMaxGapM: 95,
  });

  assert.equal(bridgeCount, 1);
  assert.equal(features.length, 3, "broad-to-broad subset seams use a separate exact-route connector");
  assert.equal(features[0].properties.route_gap_integrated, undefined);
  assert.equal(features[1].properties.route_gap_integrated, undefined);

  const connector = features[2];
  assert.equal(connector.properties.route_gap_bridge, true);
  assert.equal(connector.properties.route_gap_bridge_subset_connector, true);
  assert.deepEqual(connector.properties.route_ids, ["N"]);
  assert.deepEqual(connector.properties.color_route_ids, ["N"]);
  assert.equal(connector.properties.color, "#FCCC0A");
  assert.deepEqual(connector.geometry.coordinates[0], astoriaBranch.geometry.coordinates.at(-1));
  assert.deepEqual(connector.geometry.coordinates.at(-1), queensSharedRun.geometry.coordinates[0]);
});

test("does not add a route-subset connector to the middle of another broad route feature", () => {
  const branch = line(
    ["N", "W"],
    [P(...O, 20, 100), P(...O, 8, 100)],
    { color: "#FCCC0A", color_route_ids: ["N", "W"] },
  );
  const sharedRun = line(
    ["N", "R"],
    [P(...O, 0, 0), P(...O, 0, 200)],
    { color: "#FCCC0A", color_route_ids: ["N", "R"] },
  );

  const { features, bridgeCount } = bridgeRouteGaps([branch, sharedRun], {
    minGapM: 6,
    maxGapM: 28,
    allowSubsetRouteConnectors: true,
    subsetConnectorMaxGapM: 95,
  });

  assert.equal(bridgeCount, 0);
  assert.equal(features.length, 2);
});
