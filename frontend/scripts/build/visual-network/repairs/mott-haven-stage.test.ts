import assert from "node:assert/strict";
import { test } from "node:test";
import { applyMottHavenStage } from "./mott-haven-stage.ts";
import type { LineFeature } from "../shared/types.ts";

function line(id: string, routes: string[], coordinates: Array<[number, number]>): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: { corridor_id: id, route_ids: routes, color: "#00933C" },
  };
}

test("Mott Haven stage is a no-op when visualFeatures is missing", () => {
  const bundleArtifacts = {};
  applyMottHavenStage({ bundleArtifacts });
  assert.equal("visualFeatures" in bundleArtifacts, false);
});

test("Mott Haven stage fails schematic QA when no junction is present", () => {
  const exits: Array<number | undefined> = [];
  const original = process.exit;
  // SAFETY: test stub matches process.exit's call signature and never returns.
  process.exit = ((code?: number) => {
    exits.push(code);
    throw new Error("qa-exit");
  }) as typeof process.exit;
  try {
    assert.throws(
      () => applyMottHavenStage({ bundleArtifacts: { visualFeatures: [] } }),
      /qa-exit/,
    );
    assert.deepEqual(exits, [1]);
    const elsewhere = [
      line("elsewhere", ["1"], [[-73.99, 40.75], [-73.99, 40.76]]),
    ];
    assert.throws(
      () => applyMottHavenStage({ bundleArtifacts: { visualFeatures: elsewhere } }),
      /qa-exit/,
    );
    assert.deepEqual(elsewhere[0].geometry.coordinates, [[-73.99, 40.75], [-73.99, 40.76]]);
  } finally {
    process.exit = original;
  }
});

const ORIGIN: [number, number] = [-73.928, 40.81725];
const M_PER_DEG_LAT = 110574;

function metersPerDegLng(lat: number): number {
  return 111320 * Math.cos((lat * Math.PI) / 180);
}

function p(eastM: number, northM: number): [number, number] {
  return [
    ORIGIN[0] + eastM / metersPerDegLng(ORIGIN[1]),
    ORIGIN[1] + northM / M_PER_DEG_LAT,
  ];
}

function green(id: string, routes: string[], coordinates: Array<[number, number]>): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: { corridor_id: id, route_ids: routes, color: "#00933C" },
  };
}

test("Mott Haven stage authors the 5 lens and 6 merge on a Grand Concourse junction", () => {
  const trunk = green("trunk-45", ["4", "5"], [
    p(0, 0),
    p(-8, -400),
    p(-14, -800),
    p(-20, -1200),
    p(-25, -1600),
    p(-28, -1900),
  ]);
  const branch5 = green("branch-5", ["5"], [
    p(900, 70),
    p(650, 40),
    p(430, -10),
    p(250, -25),
    p(90, -45),
    p(20, -35),
  ]);
  const fourStem = green("stem-4", ["4"], [
    p(0, 380),
    p(0, 250),
    p(0, 140),
  ]);
  const sixBranch = green("branch-6", ["6"], [
    p(760, 80),
    p(560, 60),
    p(360, 20),
    p(180, -80),
    p(-80, -300),
    p(-220, -500),
  ]);
  const sixShared = green("shared-46", ["4", "6"], [
    p(0, 180),
    p(0, 60),
    p(0, -80),
    p(0, -220),
    p(0, -420),
    p(0, -620),
  ]);
  const twoRef: LineFeature = {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [p(-520, 145), p(-220, 126), p(0, 110), p(440, 76)],
    },
    properties: { corridor_id: "red-2", route_ids: ["2"], color: "#EE352E" },
  };
  const features = [trunk, branch5, fourStem, sixBranch, sixShared, twoRef];
  const clone = JSON.parse(JSON.stringify(features));
  const original = process.exit;
  // SAFETY: test stub matches process.exit's call signature and never returns.
  process.exit = ((code?: number) => {
    throw new Error(`qa-exit ${code}`);
  }) as typeof process.exit;
  try {
    try {
      applyMottHavenStage({ bundleArtifacts: { visualFeatures: features } });
    } catch (error) {
      assert.match(String(error), /qa-exit/);
    }
    try {
      applyMottHavenStage({ bundleArtifacts: { visualFeatures: clone } });
    } catch (error) {
      assert.match(String(error), /qa-exit/);
    }
  } finally {
    process.exit = original;
  }
  assert.equal(features[1].properties.mott_haven_lens, true);
  assert.equal(clone[1].properties.mott_haven_lens, true);
  assert.equal(features[4].properties.mott_haven_six_shared_mainline, true);
});

function stubQaExit(run: () => void): void {
  const original = process.exit;
  // SAFETY: test stub matches process.exit's call signature and never returns.
  process.exit = ((code?: number) => {
    throw new Error(`qa-exit ${code}`);
  }) as typeof process.exit;
  try {
    try {
      run();
    } catch (error) {
      assert.match(String(error), /qa-exit/);
    }
  } finally {
    process.exit = original;
  }
}

test("Mott Haven stage straightens a long southbound 4/5 trunk before the 5 lens", () => {
  const trunk = green("trunk-45", ["4", "5"], [
    p(0, 0),
    p(-2, -200),
    p(-4, -400),
    p(-6, -600),
    p(-8, -800),
    p(-10, -1000),
    p(-12, -1200),
    p(-14, -1400),
    p(-16, -1600),
    p(-20, -1900),
  ]);
  const branch5 = green("branch-5", ["5"], [
    p(900, 70),
    p(650, 40),
    p(430, -10),
    p(250, -25),
    p(90, -45),
    p(20, -35),
  ]);
  const fourStem = green("stem-4", ["4"], [
    p(0, 380),
    p(0, 250),
    p(0, 140),
    p(0, 80),
  ]);
  const sixBranch = green("branch-6", ["6"], [
    p(760, 80),
    p(560, 60),
    p(360, 20),
    p(180, -80),
    p(-80, -300),
    p(-220, -500),
  ]);
  const sixShared = green("shared-46", ["4", "6"], [
    p(0, 180),
    p(0, 60),
    p(0, -80),
    p(0, -220),
    p(0, -420),
    p(0, -620),
  ]);
  const features = [trunk, branch5, fourStem, sixBranch, sixShared];
  stubQaExit(() => applyMottHavenStage({ bundleArtifacts: { visualFeatures: features } }));
  assert.equal(trunk.properties.mott_haven_mainline_straightened, true);
  assert.equal(fourStem.properties.mott_haven_four_continuity, true);
  assert.equal(branch5.properties.mott_haven_lens, true);
  assert.equal(branch5.properties.mott_haven_parallel_reference_used, false);
  assert.equal(branch5.properties.mott_haven_parallel_reference_distance_m, null);
  assert.equal(sixBranch.properties.mott_haven_six_merge, true);
  const trunkRoutes = trunk.properties.route_ids;
  assert.ok(Array.isArray(trunkRoutes));
  assert.equal(trunkRoutes.join(","), "4,5");
});

test("Mott Haven stage falls back to the trunk heading when the 4 stem is degenerate", () => {
  const trunk = green("trunk-45", ["4", "5"], [
    p(0, 0),
    p(-2, -200),
    p(-4, -400),
    p(-6, -600),
    p(-8, -800),
    p(-10, -1000),
    p(-12, -1200),
    p(-14, -1400),
    p(-16, -1600),
    p(-20, -1900),
  ]);
  const branch5 = green("branch-5", ["5"], [
    p(900, 70),
    p(650, 40),
    p(430, -10),
    p(250, -25),
    p(90, -45),
    p(20, -35),
  ]);
  const fourStem = green("stem-4", ["4"], [
    p(0.2, 0.2),
    p(0.1, 0.1),
    p(0, 0.05),
  ]);
  const features = [trunk, branch5, fourStem];
  stubQaExit(() => applyMottHavenStage({ bundleArtifacts: { visualFeatures: features } }));
  assert.equal(branch5.properties.mott_haven_lens, true);
  assert.equal(fourStem.properties.mott_haven_four_continuity, undefined);
});

test("Mott Haven stage authors the 5 lens without a 6 merge when 6 members are missing", () => {
  const trunk = green("trunk-45", ["4", "5"], [
    p(0, 0),
    p(-8, -400),
    p(-14, -800),
    p(-20, -1200),
    p(-25, -1600),
    p(-28, -1900),
  ]);
  const branch5 = green("branch-5", ["5"], [
    p(900, 70),
    p(650, 40),
    p(430, -10),
    p(250, -25),
    p(90, -45),
    p(20, -35),
  ]);
  const features = [trunk, branch5];
  stubQaExit(() => applyMottHavenStage({ bundleArtifacts: { visualFeatures: features } }));
  assert.equal(branch5.properties.mott_haven_lens, true);
  assert.equal(branch5.properties.mott_haven_six_merge, undefined);
});

test("Mott Haven stage skips a one-vertex 5 and 6 and ignores missing color or route ids", () => {
  const trunk = green("trunk-45", ["4", "5"], [
    p(0, 0),
    p(-8, -400),
    p(-14, -800),
    p(-20, -1200),
    p(-25, -1600),
    p(-28, -1900),
  ]);
  const branch5 = green("branch-5", ["5"], [p(20, -35)]);
  const sixBranch = green("branch-6", ["6"], [p(180, -80)]);
  const sixShared = green("shared-46", ["4", "6"], [
    p(0, 180),
    p(0, 60),
    p(0, -80),
  ]);
  const colorless: LineFeature = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [p(10, 10), p(12, 12)] },
    properties: { corridor_id: "no-color", route_ids: ["4"] },
  };
  const noRoutes = green("no-routes", ["4"], [p(-10, 10), p(-12, 0)]);
  delete noRoutes.properties.route_ids;
  const features = [trunk, branch5, sixBranch, sixShared, colorless, noRoutes];
  stubQaExit(() => applyMottHavenStage({ bundleArtifacts: { visualFeatures: features } }));
  assert.equal(branch5.properties.mott_haven_lens, undefined);
  assert.equal(sixBranch.properties.mott_haven_six_merge, undefined);
  assert.equal(colorless.properties.mott_haven_lens, undefined);
});

test("Mott Haven stage leaves a northbound 4 stem and a short trunk unstraightened", () => {
  const shortTrunk = green("trunk-45", ["4", "5"], [
    p(0, 0),
    p(-2, -80),
    p(-4, -160),
    p(-6, -240),
  ]);
  const branch5 = green("branch-5", ["5"], [
    p(900, 70),
    p(650, 40),
    p(430, -10),
    p(250, -25),
    p(90, -45),
    p(20, -35),
  ]);
  const northStem = green("stem-4", ["4"], [
    p(0, 40),
    p(0, 180),
    p(0, 320),
  ]);
  stubQaExit(() => applyMottHavenStage({
    bundleArtifacts: { visualFeatures: [shortTrunk, branch5, northStem] },
  }));
  assert.equal(shortTrunk.properties.mott_haven_mainline_straightened, undefined);
});

test("Mott Haven stage does not join a 4 stem that is already on the trunk or 500m away", () => {
  const trunk = green("trunk-45", ["4", "5"], [
    p(0, 0),
    p(-2, -200),
    p(-4, -400),
    p(-6, -600),
    p(-8, -800),
    p(-10, -1000),
    p(-12, -1200),
    p(-14, -1400),
    p(-16, -1600),
    p(-20, -1900),
  ]);
  const branch5 = green("branch-5", ["5"], [
    p(900, 70),
    p(650, 40),
    p(430, -10),
    p(250, -25),
    p(90, -45),
    p(20, -35),
  ]);
  const touching = green("stem-4-touch", ["4"], [p(0, 30), p(0, 16), p(0, 8)]);
  stubQaExit(() => applyMottHavenStage({
    bundleArtifacts: { visualFeatures: [trunk, branch5, touching] },
  }));
  assert.equal(touching.properties.mott_haven_four_continuity, undefined);

  const far = green("stem-4-far", ["4"], [p(0, 900), p(0, 700), p(0, 520)]);
  const trunk2 = green("trunk-45-b", ["4", "5"], structuredClone(trunk.geometry.coordinates));
  const branch2 = green("branch-5-b", ["5"], structuredClone(branch5.geometry.coordinates));
  stubQaExit(() => applyMottHavenStage({
    bundleArtifacts: { visualFeatures: [trunk2, branch2, far] },
  }));
  assert.equal(far.properties.mott_haven_four_continuity, undefined);
});

test("Mott Haven stage ignores a colorless red 2 and a lowercase green 5 still authors the lens", () => {
  const trunk = green("trunk-45", ["4", "5"], [
    p(0, 0),
    p(-8, -400),
    p(-14, -800),
    p(-20, -1200),
    p(-25, -1600),
    p(-28, -1900),
  ]);
  const branch5 = green("branch-5", ["5"], [
    p(900, 70),
    p(650, 40),
    p(430, -10),
    p(250, -25),
    p(90, -45),
    p(20, -35),
  ]);
  branch5.properties.color = "#00933c";
  const red2: LineFeature = {
    type: "Feature",
    geometry: { type: "LineString", coordinates: [p(-520, 145), p(-220, 126), p(0, 110)] },
    properties: { corridor_id: "red-2", route_ids: ["2"] },
  };
  stubQaExit(() => applyMottHavenStage({
    bundleArtifacts: { visualFeatures: [trunk, branch5, red2] },
  }));
  assert.equal(branch5.properties.mott_haven_lens, true);
  assert.equal(branch5.properties.mott_haven_parallel_reference_used, false);
});
