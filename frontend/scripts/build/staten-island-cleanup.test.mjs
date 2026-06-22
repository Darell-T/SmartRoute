import assert from "node:assert/strict";
import { test } from "node:test";

import { cleanStatenIslandLine } from "./staten-island-cleanup.mjs";

// Synthetic SIR along a horizontal line at lat 40.55.
const LAT = 40.55;
const M_PER_DEG_LON = 111320 * Math.cos((LAT * Math.PI) / 180);
const lonAt = (m) => -74.25 + m / M_PER_DEG_LON;

function si(id, fromM, toM, latOffsetM = 0, routes = ["SI"]) {
  const y = LAT + latOffsetM / 111320;
  const coordinates = [];
  for (let m = fromM; m < toM; m += 100) coordinates.push([lonAt(m), y]);
  coordinates.push([lonAt(toM), y]);
  return {
    type: "Feature",
    properties: { corridor_id: id, route_ids: routes, color: "#0078C6", visual_feature_type: "bundle_lane" },
    geometry: { type: "LineString", coordinates },
  };
}

const FROM = [lonAt(0), LAT]; // Tottenville side
const TO = [lonAt(10000), LAT]; // St George side

test("keeps the stitched mainline chain and drops parallel slivers", () => {
  const features = [
    si("main-1", 0, 4000),
    si("main-2", 4050, 7000), // 50m seam to main-1
    si("main-3", 7000, 10000),
    si("sliver", 5000, 5200, 15), // 200m second-track shadow, 15m offset
    si("twig", 8000, 8120, 60), // 120m dangling twig
  ];
  const summary = cleanStatenIslandLine(features, { fromCoord: FROM, toCoord: TO });

  const ids = features.map((f) => f.properties.corridor_id);
  assert.ok(ids.includes("main-1") && ids.includes("main-2") && ids.includes("main-3"));
  assert.ok(!ids.includes("sliver"), "parallel shadow must be dropped");
  assert.ok(!ids.includes("twig"), "dangling twig must be dropped");
  assert.equal(summary.dropped, 2);
});

test("stitches small seams between consecutive mainline fragments", () => {
  const features = [
    si("main-1", 0, 4000),
    si("main-2", 4070, 10000), // 70m seam
  ];
  cleanStatenIslandLine(features, { fromCoord: FROM, toCoord: TO });

  const stitches = features.filter((f) =>
    String(f.properties.corridor_id || "").startsWith("si-stitch"),
  );
  assert.equal(stitches.length, 1, "seam must be bridged");
  assert.ok((features[0].properties.route_ids || []).includes("SI"));
  const stitch = stitches[0];
  assert.deepEqual(stitch.properties.route_ids, ["SI"]);
  assert.equal(stitch.properties.color, "#0078C6");
});

test("long genuinely-offset SI geometry is kept (safety)", () => {
  const features = [
    si("main-1", 0, 10000),
    si("branch", 3000, 3800, 400), // 800m long, 400m offset: not a shadow
  ];
  const summary = cleanStatenIslandLine(features, { fromCoord: FROM, toCoord: TO });

  const ids = features.map((f) => f.properties.corridor_id);
  assert.ok(ids.includes("branch"), "long non-shadow geometry must survive");
  assert.equal(summary.dropped, 0);
});

test("non-SI features are never touched", () => {
  const features = [
    si("main-1", 0, 10000),
    si("red", 5000, 5100, 10, ["2"]),
  ];
  cleanStatenIslandLine(features, { fromCoord: FROM, toCoord: TO });
  assert.ok(features.some((f) => f.properties.corridor_id === "red"));
});
