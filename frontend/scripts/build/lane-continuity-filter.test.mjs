import { test } from "node:test";
import assert from "node:assert/strict";
import { filterBogusTransitions, markOrphanLanes, removeOrphanErrorLanes } from "./lane-continuity-filter.mjs";

test("removeOrphanErrorLanes drops only both-ends-dangling error orphans", () => {
  const stray = { type: "Feature", geometry: { type: "LineString", coordinates: [[0, 0], [0, 1]] }, properties: { qa_orphan_origin: true, qa_orphan_severity: "error", qa_orphan_from_is_terminal: false, qa_orphan_to_is_terminal: false } };
  const terminalAnchored = { type: "Feature", geometry: { type: "LineString", coordinates: [[1, 0], [1, 1]] }, properties: { qa_orphan_origin: true, qa_orphan_severity: "error", qa_orphan_from_is_terminal: true, qa_orphan_to_is_terminal: false } };
  const warn = { type: "Feature", geometry: { type: "LineString", coordinates: [[2, 0], [2, 1]] }, properties: { qa_orphan_origin: true, qa_orphan_severity: "warn", qa_orphan_from_is_terminal: true, qa_orphan_to_is_terminal: true } };
  const normal = { type: "Feature", geometry: { type: "LineString", coordinates: [[3, 0], [3, 1]] }, properties: {} };
  const { features, removedCount } = removeOrphanErrorLanes([stray, terminalAnchored, warn, normal]);
  assert.equal(removedCount, 1);
  assert.ok(!features.includes(stray));
  assert.ok(features.includes(terminalAnchored) && features.includes(warn) && features.includes(normal));
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeLine(coords, props) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: props,
  };
}

function makeBundle(bundleId, corridorId, routeIds, color, fromAnchor, toAnchor) {
  return makeLine(
    [[0, 0], [0, 1]],
    {
      visual_feature_type: "bundle_lane",
      bundle_id: bundleId,
      corridor_id: corridorId,
      lane_slot_source: "bundle",
      route_ids: routeIds,
      color_route_ids: routeIds.filter((r) => r === routeIds[0]),
      color,
      spine_id: `spine-${bundleId}`,
      base_spine_hash: `hash-${bundleId}`,
      from_anchor_id: fromAnchor ?? null,
      to_anchor_id: toAnchor ?? null,
      from_stop_id: fromAnchor ?? null,
      to_stop_id: toAnchor ?? null,
      length_m: 100,
    },
  );
}

function makeTransition(fromBid, toBid, anchorId, color, routeIds, colorRouteIds, classification, lengthM) {
  return makeLine(
    [[0, 0], [0.0001, 0.0001]],
    {
      visual_feature_type: "bundle_lane",
      feature_type: "branch_transition",
      lane_slot_source: "branch_transition",
      bundle_id: `transition-${fromBid}-${toBid}`,
      bundle_id_from: fromBid,
      bundle_id_to: toBid,
      anchor_id: anchorId,
      from_anchor_id: anchorId,
      to_anchor_id: anchorId,
      color,
      route_ids: routeIds,
      color_route_ids: colorRouteIds,
      transition_classification: classification,
      length_m: lengthM,
    },
  );
}

// ---------------------------------------------------------------------------
// filterBogusTransitions
// ---------------------------------------------------------------------------

test("filterBogusTransitions: keeps valid safe_same_route_continuation", () => {
  const b1 = makeBundle("b1", "corr-1", ["A", "C"], "#0A84FF", "anc-1", "anc-2");
  const b2 = makeBundle("b2", "corr-2", ["A", "C"], "#0A84FF", "anc-2", "anc-3");
  const t = makeTransition("b1", "b2", "anc-2", "#0A84FF", ["A", "C"], ["A", "C"], "safe_same_route_continuation", 5);

  const index = new Map([
    ["b1", new Set(["A", "C"])],
    ["b2", new Set(["A", "C"])],
  ]);

  const { kept, dropped } = filterBogusTransitions([b1, b2, t], index);
  assert.equal(kept.length, 3, "should keep all 3 including valid transition");
  assert.equal(dropped.length, 0);
});

test("filterBogusTransitions: drops transition whose color is absent from both endpoints", () => {
  const b1 = makeBundle("b1", "corr-1", ["A", "C"], "#0A84FF", "anc-1", "anc-2");
  const b2 = makeBundle("b2", "corr-2", ["1", "2", "3"], "#EE352E", "anc-2", "anc-3");
  // Transition carries Q (yellow) but neither b1 nor b2 have Q
  const t = makeTransition("b1", "b2", "anc-2", "#FCCC0A", ["N", "Q"], ["N", "Q"], "likely_branch_exit", 10);

  const index = new Map([
    ["b1", new Set(["A", "C"])],
    ["b2", new Set(["1", "2", "3"])],
  ]);

  const { kept, dropped } = filterBogusTransitions([b1, b2, t], index);
  assert.equal(dropped.length, 1);
  assert.ok(dropped[0].reason.includes("bogus_route_mismatch"));
  assert.equal(kept.length, 2);
});

test("filterBogusTransitions: drops safe_same_route_continuation with empty intersection", () => {
  // b1 has A, b2 has C — no common routes, but classified as safe_same_route
  const b1 = makeBundle("b1", "corr-1", ["A"], "#0A84FF", "anc-1", "anc-2");
  const b2 = makeBundle("b2", "corr-2", ["C"], "#0A84FF", "anc-2", "anc-3");
  const t = makeTransition("b1", "b2", "anc-2", "#0A84FF", ["A", "C"], ["A", "C"], "safe_same_route_continuation", 8);

  const index = new Map([
    ["b1", new Set(["A"])],
    ["b2", new Set(["C"])],
  ]);

  const { kept, dropped } = filterBogusTransitions([b1, b2, t], index);
  assert.equal(dropped.length, 1);
  assert.ok(dropped[0].reason.includes("bogus_classification:safe_same_route_but_empty_intersect"));
});

test("filterBogusTransitions: drops likely_branch_exit longer than 25m", () => {
  const b1 = makeBundle("b1", "corr-1", ["B", "Q"], "#FF6319", "anc-1", "anc-2");
  const b2 = makeBundle("b2", "corr-2", ["B"], "#FF6319", "anc-2", "anc-3");
  const t = makeTransition("b1", "b2", "anc-2", "#FF6319", ["B", "Q"], ["B", "Q"], "likely_branch_exit", 30);

  const index = new Map([
    ["b1", new Set(["B", "Q"])],
    ["b2", new Set(["B"])],
  ]);

  const { kept, dropped } = filterBogusTransitions([b1, b2, t], index);
  assert.equal(dropped.length, 1);
  assert.ok(dropped[0].reason.includes("length_exceeds_25m"));
});

test("filterBogusTransitions: keeps likely_branch_exit at exactly 25m", () => {
  const b1 = makeBundle("b1", "corr-1", ["B", "Q"], "#FF6319", "anc-1", "anc-2");
  const b2 = makeBundle("b2", "corr-2", ["B"], "#FF6319", "anc-2", "anc-3");
  const t = makeTransition("b1", "b2", "anc-2", "#FF6319", ["B", "Q"], ["B", "Q"], "likely_branch_exit", 25);

  const index = new Map([
    ["b1", new Set(["B", "Q"])],
    ["b2", new Set(["B"])],
  ]);

  const { kept, dropped } = filterBogusTransitions([b1, b2, t], index);
  assert.equal(dropped.length, 0);
  assert.equal(kept.length, 3);
});

test("filterBogusTransitions: non-transition features always kept", () => {
  const b1 = makeBundle("b1", "corr-1", ["A"], "#0A84FF", "anc-1", "anc-2");
  const index = new Map([["b1", new Set(["A"])]]);
  const { kept, dropped } = filterBogusTransitions([b1], index);
  assert.equal(kept.length, 1);
  assert.equal(dropped.length, 0);
});

// ---------------------------------------------------------------------------
// markOrphanLanes
// ---------------------------------------------------------------------------

test("markOrphanLanes: connected feature is NOT marked orphan", () => {
  // Two features sharing anchor anc-2 for route "1"
  const f1 = makeLine([[0, 0], [0, 1]], {
    lane_slot_source: "bundle",
    route_ids: ["1"],
    color_route_ids: ["1"],
    from_anchor_id: "anc-1",
    to_anchor_id: "anc-2",
    from_stop_id: "anc-1",
    to_stop_id: "anc-2",
  });
  const f2 = makeLine([[0, 1], [0, 2]], {
    lane_slot_source: "bundle",
    route_ids: ["1"],
    color_route_ids: ["1"],
    from_anchor_id: "anc-2",
    to_anchor_id: "anc-3",
    from_stop_id: "anc-2",
    to_stop_id: "anc-3",
  });

  markOrphanLanes([f1, f2], new Set());
  assert.equal(f1.properties.qa_orphan_origin, undefined, "f1 should not be orphaned (has neighbor at anc-2)");
  assert.equal(f2.properties.qa_orphan_origin, undefined, "f2 should not be orphaned (has neighbor at anc-2)");
});

test("markOrphanLanes: isolated feature (no neighbors) IS marked orphan", () => {
  const isolated = makeLine([[5, 5], [5.001, 5.001]], {
    lane_slot_source: "bundle",
    route_ids: ["X"],
    color_route_ids: ["X"],
    from_anchor_id: "unique-1",
    to_anchor_id: "unique-2",
    from_stop_id: "stopX",
    to_stop_id: "stopY",
  });

  markOrphanLanes([isolated], new Set());
  assert.equal(isolated.properties.qa_orphan_origin, true);
  assert.equal(isolated.properties.qa_orphan_severity, "error");
});

test("markOrphanLanes: isolated feature with both stops as terminals is 'warn'", () => {
  const isolated = makeLine([[5, 5], [5.001, 5.001]], {
    lane_slot_source: "bundle",
    route_ids: ["X"],
    color_route_ids: ["X"],
    from_anchor_id: "terminal-1",
    to_anchor_id: "terminal-2",
    from_stop_id: "stopA",
    to_stop_id: "stopB",
  });

  const terminals = new Set(["stopA", "stopB"]);
  markOrphanLanes([isolated], terminals);
  assert.equal(isolated.properties.qa_orphan_origin, true);
  assert.equal(isolated.properties.qa_orphan_severity, "warn");
  assert.equal(isolated.properties.qa_orphan_from_is_terminal, true);
  assert.equal(isolated.properties.qa_orphan_to_is_terminal, true);
});

test("markOrphanLanes: branch_transition features are never marked orphan", () => {
  const trans = makeLine([[0, 0], [0.0001, 0.0001]], {
    lane_slot_source: "branch_transition",
    route_ids: ["A"],
    color_route_ids: ["A"],
    bundle_id_from: "b1",
    bundle_id_to: "b2",
    from_anchor_id: "anc-1",
    to_anchor_id: "anc-1",
    from_stop_id: null,
    to_stop_id: null,
  });

  markOrphanLanes([trans], new Set());
  assert.equal(trans.properties.qa_orphan_origin, undefined, "transitions should never be marked orphan");
});

test("markOrphanLanes: returns the same array (mutation, not copy)", () => {
  const features = [
    makeLine([[0, 0], [0, 1]], {
      lane_slot_source: "bundle",
      route_ids: ["1"],
      from_anchor_id: "a",
      to_anchor_id: "b",
      from_stop_id: null,
      to_stop_id: null,
    }),
  ];
  const result = markOrphanLanes(features, new Set());
  assert.strictEqual(result, features, "should return same array reference");
});
