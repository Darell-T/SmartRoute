import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { applySharedCorridorSeparationStage } from "./shared-corridor-separation-stage.ts";
import { minSeparationM, sliceArc } from "../../brighton-bq-church-spacing.ts";
import { clipRedundantDekalbLanes } from "../repairs/dekalb-same-color-collapse-stage.ts";
import type { LineFeature, Position } from "../shared/types.ts";

function pos(lon: number, lat: number): Position {
  return [lon, lat];
}

function eastDeg(lat: number, eastM: number): number {
  return eastM / (111320 * Math.cos((lat * Math.PI) / 180));
}

function stubHotspotExit(run: () => void): void {
  const original = process.exit;
  // SAFETY: test stub matches process.exit's call signature and never returns.
  process.exit = ((code?: number) => {
    throw new Error(`hotspot-exit ${code}`);
  }) as typeof process.exit;
  try {
    try {
      run();
    } catch (error) {
      assert.match(String(error), /hotspot-exit/);
    }
  } finally {
    process.exit = original;
  }
}

function tempReportPath() {
  return join(mkdtempSync(join(tmpdir(), "shared-corridor-")), "report.json");
}

function lane(
  corridorId: string,
  routeId: string,
  color: string,
  coords: LineFeature["geometry"]["coordinates"],
): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      visual_feature_type: "bundle_lane",
      corridor_id: corridorId,
      bundle_id: `bundle-${corridorId}`,
      route_id: routeId,
      route_ids: [routeId],
      color,
    },
  };
}

test("empty visual features write a zero-pair report and leave geometry untouched", () => {
  const visualFeatures: LineFeature[] = [];
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.equal(report.summary.pairs_scanned, 0);
  assert.equal(report.summary.pairs_with_a_fix, 0);
  assert.equal(visualFeatures.length, 0);
  assert.equal(report.hotspots.length, 2);
  assert.equal(report.hotspots.every((h: { passed: boolean }) => h.passed), true);
});

test("same-color neighbors are not classified as a shared-corridor pair", () => {
  const coords: LineFeature["geometry"]["coordinates"] = [
    [-73.99, 40.75],
    [-73.99, 40.753],
  ];
  const visualFeatures = [
    lane("c-red-a", "1", "#EE352E", coords),
    lane("c-red-b", "2", "#EE352E", coords.map(([lon, lat]) => [lon + 0.00002, lat])),
  ];
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.equal(report.summary.pairs_scanned, 0);
  assert.deepEqual(visualFeatures[0].geometry.coordinates, coords);
});

function cloneLine(coords: LineFeature["geometry"]["coordinates"]) {
  return coords.map(([lon, lat]): [number, number] => [lon, lat]);
}

test("cross-color overlapping lanes are classified and the stage is deterministic", () => {
  const red: LineFeature["geometry"]["coordinates"] = [
    [-73.99, 40.75],
    [-73.99, 40.753],
  ];
  const blue = cloneLine(red).map(([lon, lat]): [number, number] => [lon + 0.00002, lat]);
  const first = [
    lane("c-red", "1", "#EE352E", cloneLine(red)),
    lane("c-blue", "A", "#0A84FF", cloneLine(blue)),
  ];
  const second = [
    lane("c-red", "1", "#EE352E", cloneLine(red)),
    lane("c-blue", "A", "#0A84FF", cloneLine(blue)),
  ];
  const reportA = tempReportPath();
  const reportB = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures: first },
    separationReportJsonPath: reportA,
  });
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures: second },
    separationReportJsonPath: reportB,
  });
  const a = JSON.parse(readFileSync(reportA, "utf8"));
  const b = JSON.parse(readFileSync(reportB, "utf8"));
  assert.equal(a.summary.pairs_scanned, b.summary.pairs_scanned);
  assert.equal(a.summary.pairs_with_a_fix, b.summary.pairs_with_a_fix);
  assert.equal(JSON.stringify(first.map((f) => f.geometry.coordinates)), JSON.stringify(second.map((f) => f.geometry.coordinates)));
  assert.equal(first[0].properties.route_id, "1");
  assert.equal(first[1].properties.route_id, "A");
});

function pinchedPair() {
  const red: LineFeature["geometry"]["coordinates"] = [];
  const blue: LineFeature["geometry"]["coordinates"] = [];
  const lat0 = 40.75;
  const lat1 = 40.758;
  const steps = 48;
  const mLon = 111320 * Math.cos((40.754 * Math.PI) / 180);
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const lat = lat0 + (lat1 - lat0) * t;
    const offsetM = 2 + 45 * (2 * t - 1) ** 2;
    red.push([-73.99, lat]);
    blue.push([-73.99 + offsetM / mLon, lat]);
  }
  return { red, blue };
}

test("cross-color pinched neighbors are scanned and locally separated", () => {
  const { red, blue } = pinchedPair();
  const visualFeatures = [
    lane("c-red", "1", "#EE352E", red),
    lane("c-blue", "A", "#0A84FF", blue),
  ];
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.ok(report.summary.pairs_scanned >= 1);
  assert.ok(report.summary.pairs_with_a_fix >= 1);
  assert.equal(visualFeatures[0].properties.route_id, "1");
  assert.equal(visualFeatures[1].properties.route_id, "A");
});

function pinchedAt(lon: number, lat0: number, lat1: number) {
  const red: LineFeature["geometry"]["coordinates"] = [];
  const other: LineFeature["geometry"]["coordinates"] = [];
  const steps = 48;
  const midLat = (lat0 + lat1) / 2;
  const mLon = 111320 * Math.cos((midLat * Math.PI) / 180);
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const lat = lat0 + (lat1 - lat0) * t;
    const offsetM = 2 + 45 * (2 * t - 1) ** 2;
    red.push([lon, lat]);
    other.push([lon + offsetM / mLon, lat]);
  }
  return { red, other };
}

test("Newkirk Plaza B/Q vertices are measured in the hotspot lat band", () => {
  const { red: bCoords, other: qCoords } = pinchedAt(-73.962, 40.6302, 40.6378);
  const visualFeatures = [
    lane("brighton-b", "B", "#FF6319", bCoords),
    lane("brighton-q", "Q", "#FCCC0A", qCoords),
  ];
  visualFeatures[0].properties.route_ids = ["B"];
  visualFeatures[1].properties.route_ids = ["Q"];
  const fanout: LineFeature = {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [
        [-73.97, 40.75],
        [-73.969, 40.751],
      ],
    },
    properties: {
      visual_feature_type: "bundle_lane",
      corridor_id: "fanout",
      bundle_id: "bundle-fanout",
      route_id: "B",
      route_ids: ["B"],
      color: "#FF6319",
      bundle_materialization_role: "fanout",
    },
  };
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures: [...visualFeatures, fanout] },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const newkirk = report.hotspots.find((h: { name: string }) => h.name === "bq_newkirk_plaza_separation");
  assert.ok(newkirk);
  assert.match(newkirk.detail, /worst B\/Q separation/);
  assert.equal(newkirk.passed, true);
});

test("Flatbush/Atlantic zone measures cross-color separation away from junctions", () => {
  const { red, other } = pinchedAt(-73.98, 40.6835, 40.6915);
  const visualFeatures = [
    lane("flat-2", "2", "#EE352E", red),
    lane("flat-a", "A", "#0A84FF", other),
  ];
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const hotspot = report.hotspots.find(
    (h: { name: string }) => h.name === "flatbush_atlantic_no_near_total_overlap",
  );
  assert.ok(hotspot);
  assert.match(hotspot.detail, /worst cross-color separation/);
  assert.equal(hotspot.passed, true);
});

test("unlabeled, colorless, reverse, and short features are classified without crashing", () => {
  const { red, blue } = pinchedPair();
  const reverseBlue = [...blue].reverse();
  const unlabeled = lane("c-red", "1", "#EE352E", red);
  delete unlabeled.properties.bundle_id;
  delete unlabeled.properties.corridor_id;
  const visualFeatures: LineFeature[] = [
    unlabeled,
    lane("c-blue", "A", "#0A84FF", reverseBlue),
    {
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[-73.99, 40.75]] },
      properties: { color: "#00933C", route_ids: "G" },
    },
    {
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[-73.99, 40.75], [-73.99, 40.76]] },
      properties: { corridor_id: "no-color", route_ids: ["L"] },
    },
  ];
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.ok(report.summary.pairs_scanned >= 1);
  assert.equal(report.pairs[0].a_id, "unknown");
});

test("two overlapping cross-color pairs leave a claimed-range skip on the later pair", () => {
  const { red, blue } = pinchedPair();
  const green = blue.map(([lon, lat]): [number, number] => [lon + 0.00001, lat]);
  const visualFeatures = [
    lane("c-red", "1", "#EE352E", red),
    lane("c-blue", "A", "#0A84FF", blue),
    lane("c-green", "G", "#6CBE45", green),
  ];
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.ok(report.summary.pairs_scanned >= 2);
  const skipped = report.pairs.flatMap((pair: { skipped_reasons: string[] }) => pair.skipped_reasons);
  assert.ok(
    skipped.some((reason: string) => reason.includes("claimed") || reason.includes("near_junction") || reason.includes("fit_")),
  );
});

test("a long pinched corridor is windowed into more than one local fit", () => {
  const { red, other } = pinchedAt(-73.99, 40.70, 40.72);
  const visualFeatures = [
    lane("long-red", "1", "#EE352E", red),
    lane("long-blue", "A", "#0A84FF", other),
  ];
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.ok(report.summary.pairs_scanned >= 1);
  assert.ok(report.pairs[0].windows_attempted >= 2);
});

function parallelNs(
  lon: number,
  lat0: number,
  lat1: number,
  offsetM: number,
  steps = 48,
) {
  const red: Position[] = [];
  const blue: Position[] = [];
  const dLon = eastDeg((lat0 + lat1) / 2, offsetM);
  for (let i = 0; i <= steps; i += 1) {
    const lat = lat0 + (lat1 - lat0) * (i / steps);
    red.push(pos(lon, lat));
    blue.push(pos(lon + dLon, lat));
  }
  return { red, blue };
}

test("zero-length vertices still classify a fully overlapping cross-color pair", () => {
  const { red, blue } = parallelNs(-73.99, 40.75, 40.758, 4);
  red.splice(4, 0, red[4]);
  const labeled = lane("dup-red", "1", "#EE352E", red);
  labeled.properties.physical_bundle_id = "pb-dup-red";
  const visualFeatures = [labeled, lane("dup-blue", "A", "#0A84FF", blue)];
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.ok(report.summary.pairs_scanned >= 1);
  assert.equal(report.pairs[0].a_id, "pb-dup-red");
  assert.ok(report.pairs[0].min_before_m < 13);
});

test("two close pinches merge and two far pinches claim the same red twice", () => {
  const lon = -73.97;
  const lat0 = 40.70;
  const lat1 = 40.72;
  const steps = 80;
  const closeRed: Position[] = [];
  const closeBlue: Position[] = [];
  const farRed: Position[] = [];
  const farBlue: Position[] = [];
  const farGreen: Position[] = [];
  const mid = (lat0 + lat1) / 2;
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const lat = lat0 + (lat1 - lat0) * t;
    const closeM = (t >= 0.22 && t <= 0.34) || (t >= 0.38 && t <= 0.50) ? 3 : 20;
    const southM = t < 0.28 ? 3 : 20;
    const northM = t > 0.72 ? 3 : 20;
    closeRed.push(pos(lon, lat));
    closeBlue.push(pos(lon + eastDeg(mid, closeM), lat));
    farRed.push(pos(lon + 0.01, lat));
    farBlue.push(pos(lon + 0.01 + eastDeg(mid, southM), lat));
    farGreen.push(pos(lon + 0.01 + eastDeg(mid, northM), lat));
  }
  const mergedPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: {
      visualFeatures: [
        lane("merge-red", "1", "#EE352E", closeRed),
        lane("merge-blue", "A", "#0A84FF", closeBlue),
      ],
    },
    separationReportJsonPath: mergedPath,
  });
  const merged = JSON.parse(readFileSync(mergedPath, "utf8"));
  assert.ok(merged.summary.pairs_scanned >= 1);
  assert.ok(merged.pairs[0].windows_attempted >= 1);

  const claimedPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: {
      visualFeatures: [
        lane("claim-red", "1", "#EE352E", farRed),
        lane("claim-blue", "A", "#0A84FF", farBlue),
        lane("claim-green", "G", "#6CBE45", farGreen),
      ],
    },
    separationReportJsonPath: claimedPath,
  });
  const claimed = JSON.parse(readFileSync(claimedPath, "utf8"));
  assert.ok(claimed.summary.pairs_scanned >= 2);
});

test("a short head pinch grows the window and a 150m pinch is still scanned", () => {
  const grown = parallelNs(-73.96, 40.75, 40.7524, 18, 24);
  const dLon = eastDeg(40.751, 4);
  for (let i = 0; i <= 4; i += 1) grown.blue[i][0] = grown.red[i][0] + dLon;
  const grownPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: {
      visualFeatures: [
        lane("grow-red", "1", "#EE352E", grown.red),
        lane("grow-blue", "A", "#0A84FF", grown.blue),
      ],
    },
    separationReportJsonPath: grownPath,
  });
  const grownReport = JSON.parse(readFileSync(grownPath, "utf8"));
  assert.ok(grownReport.summary.pairs_scanned >= 1);
  assert.ok(grownReport.pairs[0].windows_attempted >= 1 || grownReport.pairs[0].min_before_m < 13);

  const short = parallelNs(-73.95, 40.75, 40.7514, 2, 20);
  const shortPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: {
      visualFeatures: [
        lane("short-red", "1", "#EE352E", short.red),
        lane("short-blue", "A", "#0A84FF", short.blue),
      ],
    },
    separationReportJsonPath: shortPath,
  });
  const shortReport = JSON.parse(readFileSync(shortPath, "utf8"));
  assert.ok(shortReport.summary.pairs_scanned >= 1);
  assert.ok(shortReport.pairs[0].windows_attempted >= 1 || shortReport.pairs[0].min_before_m < 13);
});

test("neighbors 18m apart are scanned with no deficient pocket", () => {
  const { red, blue } = parallelNs(-73.94, 40.75, 40.758, 18);
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: {
      visualFeatures: [
        lane("wide-red", "1", "#EE352E", red),
        lane("wide-blue", "A", "#0A84FF", blue),
      ],
    },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.ok(report.summary.pairs_scanned >= 1);
  assert.equal(report.pairs[0].windows_attempted, 0);
  assert.ok(report.pairs[0].min_before_m >= 13);
});

test("weaving sides attempt a sign-consistent refit and stay deterministic", () => {
  const red: Position[] = [];
  const blue: Position[] = [];
  const lat0 = 40.68;
  const lat1 = 40.715;
  const steps = 80;
  const mid = (lat0 + lat1) / 2;
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const lat = lat0 + (lat1 - lat0) * t;
    const side = t < 0.4 ? 1 : -1;
    red.push(pos(-73.93, lat));
    blue.push(pos(-73.93 + side * eastDeg(mid, 5), lat));
  }
  const first = [lane("weave-red", "1", "#EE352E", cloneLine(red)), lane("weave-blue", "A", "#0A84FF", cloneLine(blue))];
  const second = [lane("weave-red", "1", "#EE352E", cloneLine(red)), lane("weave-blue", "A", "#0A84FF", cloneLine(blue))];
  const reportA = tempReportPath();
  const reportB = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures: first },
    separationReportJsonPath: reportA,
  });
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures: second },
    separationReportJsonPath: reportB,
  });
  const a = JSON.parse(readFileSync(reportA, "utf8"));
  const b = JSON.parse(readFileSync(reportB, "utf8"));
  assert.equal(a.summary.pairs_scanned, b.summary.pairs_scanned);
  assert.equal(a.summary.pairs_with_a_fix, b.summary.pairs_with_a_fix);
  assert.ok(a.pairs[0].windows_attempted >= 2);
  assert.equal(JSON.stringify(first.map((f) => f.geometry.coordinates)), JSON.stringify(second.map((f) => f.geometry.coordinates)));
});

test("reversed overlapping lanes and a tail pinch still classify a pair", () => {
  const { red, blue } = parallelNs(-73.92, 40.75, 40.758, 4);
  const tailRed: Position[] = [];
  const tailBlue: Position[] = [];
  const lat0 = 40.74;
  const lat1 = 40.747;
  const steps = 40;
  const mid = (lat0 + lat1) / 2;
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const lat = lat0 + (lat1 - lat0) * t;
    const offsetM = t > 0.78 ? 3 : 18;
    tailRed.push(pos(-73.91, lat));
    tailBlue.push(pos(-73.91 + eastDeg(mid, offsetM), lat));
  }
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: {
      visualFeatures: [
        lane("rev-red", "1", "#EE352E", red),
        lane("rev-blue", "A", "#0A84FF", [...blue].reverse()),
        lane("tail-red", "2", "#EE352E", tailRed),
        lane("tail-a", "A", "#0A84FF", tailBlue),
      ],
    },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.ok(report.summary.pairs_scanned >= 2);
});

test("non-LineString geometry, string route_ids, and endpoint clusters do not crash", () => {
  const { red, blue } = parallelNs(-73.90, 40.75, 40.758, 5);
  const stringIds = lane("str-b", "B", "#FF6319", red);
  stringIds.properties.route_ids = "B";
  const clustered: LineFeature = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [pos(-73.90, 40.75), pos(-73.9002, 40.7502)] },
    properties: { color: "#6CBE45", route_ids: ["G"], corridor_id: "cluster-g" },
  };
  const multiPoint = lane("multi", "1", "#EE352E", [pos(-73.90, 40.75), pos(-73.90005, 40.75)]);
  // SAFETY: production skips non-LineString geometry after a coordinate-length check.
  (multiPoint.geometry as { type: string }).type = "MultiPoint";
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: {
      visualFeatures: [
        stringIds,
        lane("str-q", "Q", "#FCCC0A", blue),
        clustered,
        multiPoint,
      ],
    },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.ok(report.summary.pairs_scanned >= 1);
});

test("a branch_tail junction excludes Flatbush overlap samples next to the peel", () => {
  const { red, other } = pinchedAt(-73.98, 40.6835, 40.6915);
  const tail: LineFeature = {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [pos(-73.98, 40.6835), pos(-73.979, 40.682)],
    },
    properties: {
      visual_feature_type: "bundle_lane",
      corridor_id: "flat-tail",
      bundle_id: "bundle-flat-tail",
      route_id: "2",
      route_ids: ["2"],
      color: "#EE352E",
      bundle_materialization_role: "branch_tail",
    },
  };
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({
    bundleArtifacts: { visualFeatures: [lane("flat-2", "2", "#EE352E", red), lane("flat-a", "A", "#0A84FF", other), tail] },
    separationReportJsonPath: reportPath,
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const hotspot = report.hotspots.find(
    (h: { name: string }) => h.name === "flatbush_atlantic_no_near_total_overlap",
  );
  assert.ok(hotspot);
  assert.equal(hotspot.passed, true);
});

test("same-color B vertices in Newkirk are skipped and an unfixed B/Q pair fails the hotspot", () => {
  const { red: b1, other: b2 } = pinchedAt(-73.962, 40.6302, 40.632);
  const qShort: Position[] = [pos(-73.962, 40.633), pos(-73.96201, 40.6338)];
  const bShort: Position[] = [pos(-73.962, 40.633), pos(-73.96201, 40.6338)];
  const outside: Position[] = [pos(-73.962, 40.62), pos(-73.962, 40.621)];
  const visualFeatures = [
    lane("nk-b1", "B", "#FF6319", b1),
    lane("nk-b2", "B", "#FF6319", b2),
    lane("nk-b-out", "B", "#FF6319", outside),
    lane("nk-b-short", "B", "#FF6319", bShort),
    lane("nk-q-short", "Q", "#FCCC0A", qShort),
  ];
  const reportPath = tempReportPath();
  stubHotspotExit(() => {
    applySharedCorridorSeparationStage({
      bundleArtifacts: { visualFeatures },
      separationReportJsonPath: reportPath,
    });
  });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const newkirk = report.hotspots.find((h: { name: string }) => h.name === "bq_newkirk_plaza_separation");
  assert.ok(newkirk);
  assert.equal(newkirk.passed, false);
});

for (const reversed of [false, true]) {
  test(`adjacent curved repair windows preserve lane separation (reversed=${reversed})`, () => {
    // A/C and G traces captured before separation, resampled to 61 points each.
    // The old splice pinched this join to 4.61 m despite each fit passing alone.
    const [blue, green]: Position[][] = JSON.parse(readFileSync(
      join(__dirname, "fixtures/shared-corridor-curved-join.json"), "utf8",
    ));
    const visualFeatures = [
      lane("curve-blue", "A", "#0A84FF", blue),
      lane("curve-green", "G", "#6CBE45", reversed ? green.toReversed() : green),
    ];
    const endpoints = visualFeatures.map(({ geometry: { coordinates } }) => [coordinates[0], coordinates.at(-1)]);
    const reportPath = tempReportPath();
    applySharedCorridorSeparationStage({ bundleArtifacts: { visualFeatures }, separationReportJsonPath: reportPath });
    const report = JSON.parse(readFileSync(reportPath, "utf8"));
    assert.equal(report.summary.windows_fixed, 2);
    const interior = visualFeatures.map((feature) => sliceArc(feature.geometry.coordinates, 200, 1000));
    assert.ok(minSeparationM(interior[0], interior[1]) >= 12, "the join must retain the fitted lane spacing");
    assert.deepEqual(visualFeatures.map(({ geometry: { coordinates } }) => [coordinates[0], coordinates.at(-1)]), endpoints);
  });
}

test("DeKalb duplicate traces are clipped before cross-color separation", () => {
  const { red, blue } = parallelNs(-73.98, 40.685, 40.691, 18);
  const orange = lane("kept-orange", "B", "#FF6319", red);
  const yellow = lane("kept-yellow", "Q", "#FCCC0A", blue);
  orange.properties.bundle_materialization_role = "continuous_lane";
  yellow.properties.bundle_materialization_role = "continuous_lane";
  const duplicate = lane("redundant-bd", "B", "#FF6319", blue.map(([lon, lat]) => [lon - eastDeg(lat, 0.2), lat]));
  duplicate.properties.route_ids = ["B", "D"];
  const bundleArtifacts = { visualFeatures: [orange, yellow, duplicate] };
  clipRedundantDekalbLanes(bundleArtifacts);
  assert.deepEqual(bundleArtifacts.visualFeatures.map((feature) => feature.properties.corridor_id), ["kept-orange", "kept-yellow"]);
  const reportPath = tempReportPath();
  applySharedCorridorSeparationStage({ bundleArtifacts, separationReportJsonPath: reportPath });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  assert.ok(report.hotspots.every((hotspot: { passed: boolean }) => hotspot.passed));
  assert.deepEqual(orange.geometry.coordinates, red);
  assert.deepEqual(yellow.geometry.coordinates, blue);
});
