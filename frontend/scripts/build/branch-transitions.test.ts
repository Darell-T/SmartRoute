// frontend/scripts/build/branch-transitions.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBranchTransitions } from "./branch-transitions.ts";
import type { Position } from "./types.ts";

type LaneInput = {
  bundle_id: string;
  color: string;
  from_anchor_id: string | null;
  to_anchor_id: string | null;
  coordinates: Position[];
  [key: string]: unknown;
};

function lane({ bundle_id, color, from_anchor_id, to_anchor_id, coordinates, ...props }: LaneInput) {
  return {
    type: "Feature" as const,
    geometry: { type: "LineString" as const, coordinates },
    properties: {
      visual_feature_type: "bundle_lane",
      bundle_id,
      color,
      from_anchor_id,
      to_anchor_id,
      ...props,
    },
  };
}

test("buildBranchTransitions returns { transitions, coincidentSkipped } shape", () => {
  const result = buildBranchTransitions([], { maxBridgeM: 90 });
  assert.ok(Array.isArray(result.transitions));
  assert.equal(typeof result.coincidentSkipped, "number");
});

test("buildBranchTransitions connects two same-color lanes meeting at one anchor", () => {
  const lanes = [
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: "A",
      to_anchor_id: "B",
      coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
    }),
    lane({
      bundle_id: "b2",
      color: "#EE352E",
      from_anchor_id: "B",
      to_anchor_id: "C",
      coordinates: [[-73.9849, 40.7001], [-73.98, 40.70]],
    }),
  ];
  const { transitions } = buildBranchTransitions(lanes, { maxBridgeM: 90 });
  assert.equal(transitions.length, 1);
  assert.equal(transitions[0].properties.color, "#EE352E");
  assert.equal(transitions[0].properties.anchor_id, "B");
  assert.equal(transitions[0].geometry.type, "LineString");
  assert.equal(transitions[0].geometry.coordinates.length, 2);
});

test("buildBranchTransitions does not bridge across different colors", () => {
  const lanes = [
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: "A",
      to_anchor_id: "B",
      coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
    }),
    lane({
      bundle_id: "b2",
      color: "#FF6319",
      from_anchor_id: "B",
      to_anchor_id: "C",
      coordinates: [[-73.9849, 40.7001], [-73.98, 40.70]],
    }),
  ];
  const { transitions } = buildBranchTransitions(lanes, { maxBridgeM: 90 });
  assert.equal(transitions.length, 0);
});

test("buildBranchTransitions rejects pairs farther than maxBridgeM apart", () => {
  const lanes = [
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: "A",
      to_anchor_id: "B",
      coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
    }),
    lane({
      bundle_id: "b2",
      color: "#EE352E",
      from_anchor_id: "B",
      to_anchor_id: "C",
      coordinates: [[-73.984, 40.71], [-73.98, 40.71]],  // ~1100m north of b1's endpoint
    }),
  ];
  const { transitions } = buildBranchTransitions(lanes, { maxBridgeM: 90 });
  assert.equal(transitions.length, 0);
});

test("buildBranchTransitions does not bridge lanes within the same bundle", () => {
  // Two same-color lanes that share an anchor but are part of the same bundle
  // (which means they're already drawn via the bundle's spine, no connector needed).
  const lanes = [
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: "A",
      to_anchor_id: "B",
      coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
    }),
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: "B",
      to_anchor_id: "C",
      coordinates: [[-73.9849, 40.7001], [-73.98, 40.70]],
    }),
  ];
  const { transitions } = buildBranchTransitions(lanes, { maxBridgeM: 90 });
  assert.equal(transitions.length, 0);
});

test("buildBranchTransitions handles three lanes meeting at one anchor", () => {
  // Three same-color lanes meeting at anchor "X": should produce all
  // cross-bundle pairs (3 pairs from 3 entries if all 3 are different bundles).
  const lanes = [
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: "A",
      to_anchor_id: "X",
      coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
    }),
    lane({
      bundle_id: "b2",
      color: "#EE352E",
      from_anchor_id: "X",
      to_anchor_id: "C",
      coordinates: [[-73.985, 40.7001], [-73.98, 40.70]],
    }),
    lane({
      bundle_id: "b3",
      color: "#EE352E",
      from_anchor_id: "X",
      to_anchor_id: "D",
      coordinates: [[-73.9851, 40.6999], [-73.98, 40.69]],
    }),
  ];
  const { transitions } = buildBranchTransitions(lanes, { maxBridgeM: 90 });
  // Three different bundles share anchor X with same color: C(3,2) = 3 pairs.
  assert.equal(transitions.length, 3);
  for (const f of transitions) {
    assert.equal(f.properties.anchor_id, "X");
    assert.equal(f.properties.color, "#EE352E");
  }
});

test("buildBranchTransitions ignores null anchors", () => {
  const lanes = [
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: null,
      to_anchor_id: "B",
      coordinates: [[-73.99, 40.70], [-73.99, 40.7004]],
    }),
    lane({
      bundle_id: "b2",
      color: "#EE352E",
      from_anchor_id: null,
      to_anchor_id: "B",
      coordinates: [[-73.99001, 40.70], [-73.99, 40.70]],
    }),
  ];
  const { transitions } = buildBranchTransitions(lanes, { maxBridgeM: 90 });
  // Both have null from_anchor_id but share to_anchor_id "B" -> 1 transition.
  assert.equal(transitions.length, 1);
  assert.equal(transitions[0].properties.anchor_id, "B");
});

test("buildBranchTransitions records raw length_m (not rounded) for each transition", () => {
  const lanes = [
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: "A",
      to_anchor_id: "B",
      coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
    }),
    lane({
      bundle_id: "b2",
      color: "#EE352E",
      from_anchor_id: "B",
      to_anchor_id: "C",
      coordinates: [[-73.9849, 40.7001], [-73.98, 40.70]],
    }),
  ];
  const { transitions } = buildBranchTransitions(lanes, { maxBridgeM: 90 });
  assert.equal(transitions.length, 1);
  assert.ok(transitions[0].properties.length_m > 0, "length_m should be > 0");
  assert.ok(transitions[0].properties.length_m < 90, "length_m should be < maxBridgeM");
  // Raw float, not rounded -- distance between [-73.985,40.70] and [-73.9849,40.7001]
  // should be a non-round number with sub-cm precision retained.
  const v = transitions[0].properties.length_m;
  // If rounded via toFixed(2), v would be exactly e.g. 13.30 (max 2 decimal digits).
  // The raw float should have many more digits in general.
  const str = String(v);
  assert.ok(
    !/^\d+\.\d{1,2}$/.test(str) || str.endsWith("0"),
    `length_m=${str} looks artificially rounded to 2 decimals; raw float expected`,
  );
});

test("buildBranchTransitions drops coincident pairs and reports coincidentSkipped count", () => {
  // Two same-color cross-bundle lanes whose endpoints are essentially identical
  // (< 0.5 m apart) should not produce a transition feature, but the count
  // should be reported.
  const lanes = [
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: "A",
      to_anchor_id: "B",
      coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
    }),
    lane({
      bundle_id: "b2",
      color: "#EE352E",
      from_anchor_id: "B",
      to_anchor_id: "C",
      // ~0 m from b1's `to` endpoint (just floating-point noise).
      coordinates: [[-73.985000001, 40.700000001], [-73.98, 40.70]],
    }),
  ];
  const { transitions, coincidentSkipped } = buildBranchTransitions(lanes, {
    maxBridgeM: 90,
    minBridgeM: 0.5,
  });
  assert.equal(transitions.length, 0, "coincident pair should NOT emit a transition");
  assert.equal(coincidentSkipped, 1, "coincidentSkipped should count the dropped pair");
});

test("buildBranchTransitions canonicalizes bundle_id_from / bundle_id_to lexicographically", () => {
  // Build the same logical transition twice with the lane order reversed.
  // The output should be identical (same bundle_id_from / bundle_id_to) so
  // the deterministic sort downstream is stable.
  const laneA = lane({
    bundle_id: "b09",
    color: "#EE352E",
    from_anchor_id: "A",
    to_anchor_id: "X",
    coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
  });
  const laneB = lane({
    bundle_id: "b03",
    color: "#EE352E",
    from_anchor_id: "X",
    to_anchor_id: "B",
    coordinates: [[-73.9849, 40.7001], [-73.98, 40.70]],
  });
  const r1 = buildBranchTransitions([laneA, laneB], { maxBridgeM: 90 });
  const r2 = buildBranchTransitions([laneB, laneA], { maxBridgeM: 90 });
  assert.equal(r1.transitions.length, 1);
  assert.equal(r2.transitions.length, 1);
  // Smaller id ("b03") should be `_from`, larger ("b09") should be `_to`,
  // regardless of input order.
  assert.equal(r1.transitions[0].properties.bundle_id_from, "b03");
  assert.equal(r1.transitions[0].properties.bundle_id_to, "b09");
  assert.equal(r2.transitions[0].properties.bundle_id_from, "b03");
  assert.equal(r2.transitions[0].properties.bundle_id_to, "b09");
});

test("buildBranchTransitions: minBridgeM=0 keeps coincident pairs", () => {
  // Passing minBridgeM: 0 disables the coincidence filter -- useful for
  // diagnostic dumps where every potential pair is wanted.
  const lanes = [
    lane({
      bundle_id: "b1",
      color: "#EE352E",
      from_anchor_id: "A",
      to_anchor_id: "B",
      coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
    }),
    lane({
      bundle_id: "b2",
      color: "#EE352E",
      from_anchor_id: "B",
      to_anchor_id: "C",
      coordinates: [[-73.985, 40.70], [-73.98, 40.70]],
    }),
  ];
  const { transitions, coincidentSkipped } = buildBranchTransitions(lanes, {
    maxBridgeM: 90,
    minBridgeM: 0,
  });
  assert.equal(transitions.length, 1, "with minBridgeM=0 the coincident pair is kept");
  assert.equal(coincidentSkipped, 0);
});

test("buildBranchTransitions skips internal materialized bundle fanout connectors", () => {
  const lanes = [
    lane({
      bundle_id: "shared",
      color: "#FCCC0A",
      from_anchor_id: "A",
      to_anchor_id: "X",
      coordinates: [[-73.99, 40.70], [-73.985, 40.70]],
      materialized_bundle_id: "pb-test",
      bundle_materialization_role: "shared_spine",
    }),
    lane({
      bundle_id: "fanout",
      color: "#FCCC0A",
      from_anchor_id: "X",
      to_anchor_id: "B",
      coordinates: [[-73.9849, 40.7001], [-73.98, 40.70]],
      materialized_bundle_id: "pb-test",
      bundle_materialization_role: "fanout",
    }),
  ];

  const { transitions } = buildBranchTransitions(lanes, { maxBridgeM: 90 });

  assert.equal(transitions.length, 0);
});
