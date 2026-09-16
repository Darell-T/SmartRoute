import assert from "node:assert/strict";
import { test } from "node:test";

import { cleanStatenIslandLine } from "./staten-island-cleanup.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

// Synthetic SIR along a horizontal line at lat 40.55.
const LAT = 40.55;
const M_PER_DEG_LON = 111320 * Math.cos((LAT * Math.PI) / 180);
const lonAt = (m: number) => -74.25 + m / M_PER_DEG_LON;

type TestFeatureProperties = {
  corridor_id: string;
  route_ids: string[];
  color: string;
  visual_feature_type: string;
  color_route_ids?: string[];
  lane_slot?: number;
  lane_offset_baked?: boolean;
  si_stitch?: boolean;
  length_m?: number;
};

function si(
  id: string,
  fromM: number,
  toM: number,
  latOffsetM = 0,
  routes = ["SI"],
): Feature<LineStringGeometry, TestFeatureProperties> {
  const y = LAT + latOffsetM / 111320;
  const coordinates: Position[] = [];
  for (let m = fromM; m < toM; m += 100) coordinates.push([lonAt(m), y]);
  coordinates.push([lonAt(toM), y]);
  return {
    type: "Feature",
    properties: { corridor_id: id, route_ids: routes, color: "#0078C6", visual_feature_type: "bundle_lane" },
    geometry: { type: "LineString", coordinates },
  };
}

const FROM: Position = [lonAt(0), LAT]; // Tottenville side
const TO: Position = [lonAt(10000), LAT]; // St George side

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
  assert.ok(stitch);
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

test("Staten Island cleanup leaves an empty list empty and is deterministic", () => {
  const first: Feature<LineStringGeometry, TestFeatureProperties>[] = [];
  const second: Feature<LineStringGeometry, TestFeatureProperties>[] = [];
  const firstSummary = cleanStatenIslandLine(first, { fromCoord: FROM, toCoord: TO });
  const secondSummary = cleanStatenIslandLine(second, { fromCoord: FROM, toCoord: TO });
  assert.deepEqual(first, []);
  assert.equal(JSON.stringify(firstSummary), JSON.stringify(secondSummary));
});

test("Staten Island cleanup returns early for a single SI fragment", () => {
  const features = [si("only", 0, 4000)];
  const summary = cleanStatenIslandLine(features, { fromCoord: FROM, toCoord: TO });
  assert.equal(summary.kept, 1);
  assert.equal(summary.dropped, 0);
  assert.equal(summary.stitches, 0);
  assert.equal(summary.connected, undefined);
  assert.equal(features[0].properties.corridor_id, "only");
});

test("Staten Island cleanup leaves disconnected SI islands untouched", () => {
  const features = [
    si("west", 0, 2000),
    si("east", 8000, 10000),
  ];
  const before = features.map((item) => item.properties.corridor_id);
  const summary = cleanStatenIslandLine(features, { fromCoord: FROM, toCoord: TO });
  assert.equal(summary.connected, false);
  assert.equal(summary.dropped, 0);
  assert.deepEqual(features.map((item) => item.properties.corridor_id), before);
});

test("Staten Island cleanup does not stitch seams that already touch or exceed 100m", () => {
  const touching = [
    si("main-1", 0, 4000),
    si("main-2", 4000, 10000),
  ];
  const touchingSummary = cleanStatenIslandLine(touching, { fromCoord: FROM, toCoord: TO });
  assert.equal(touchingSummary.stitches, 0);
  assert.equal(touching.some((item) => String(item.properties.corridor_id).startsWith("si-stitch")), false);

  const wide = [
    si("main-1", 0, 4000),
    si("main-2", 4200, 10000),
  ];
  const wideSummary = cleanStatenIslandLine(wide, { fromCoord: FROM, toCoord: TO });
  assert.equal(wideSummary.stitches, 0);
});

test("Staten Island cleanup skips non-lines and sub-vertex SI geometry", () => {
  const features = [
    si("main-1", 0, 4000),
    si("main-2", 4050, 10000),
    {
      type: "Feature" as const,
      properties: { corridor_id: "point", route_ids: ["SI"], color: "#0078C6", visual_feature_type: "bundle_lane" },
      geometry: { type: "Point" as const, coordinates: FROM },
    },
    {
      type: "Feature" as const,
      properties: { corridor_id: "stub", route_ids: ["SI"], color: "#0078C6", visual_feature_type: "bundle_lane" },
      geometry: { type: "LineString" as const, coordinates: [FROM] },
    },
  ];
  // SAFETY: production skips non-LineString and sub-2-vertex SI fragments.
  const summary = cleanStatenIslandLine(features as ReturnType<typeof si>[], { fromCoord: FROM, toCoord: TO });
  assert.ok(summary.kept >= 2);
  assert.ok(features.some((item) => item.properties.corridor_id === "point"));
  assert.ok(features.some((item) => item.properties.corridor_id === "stub"));
});
