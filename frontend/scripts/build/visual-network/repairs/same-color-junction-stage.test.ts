import assert from "node:assert/strict";
import { test } from "node:test";
import { applySameColorJunctionStage } from "./same-color-junction-stage.ts";
import type { LineFeature, Position } from "../shared/types.ts";

const O: Position = [-73.98, 40.76];
const DEG_LAT = 1 / 110574;
const DEG_LON = 1 / (111320 * Math.cos((40.76 * Math.PI) / 180));
const P = (dxM: number, dyM: number): Position => [O[0] + dxM * DEG_LON, O[1] + dyM * DEG_LAT];

function lane(
  corridorId: string,
  routeIds: string[],
  coords: Position[],
  extra: LineFeature["properties"] = {},
): LineFeature {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      corridor_id: corridorId,
      route_ids: routeIds,
      color: "#FF6319",
      visual_feature_type: "bundle_lane",
      ...extra,
    },
  };
}

function cloneFeatures(features: LineFeature[]): LineFeature[] {
  return JSON.parse(JSON.stringify(features));
}

function runStage(visualFeatures: LineFeature[] | undefined, snapDistM = 12, fanoutBlendM = 100) {
  const bundleArtifacts = { visualFeatures };
  applySameColorJunctionStage({
    bundleArtifacts,
    sameColorSnapDistM: snapDistM,
    fanoutBlendM,
  });
  return bundleArtifacts;
}

test("same-color junction stage snaps a converging dangling orange endpoint onto the trunk", () => {
  const trunk = lane("M", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = lane("BD", ["B", "D"], [P(48, 8), P(35, 30), P(20, 60)]);
  const artifacts = runStage([trunk, branch]);
  const out = artifacts.visualFeatures?.find((feature) => feature.properties.corridor_id === "BD");
  assert.ok(out);
  assert.equal(out.properties.same_color_endpoint_snapped, true);
  assert.notEqual(out.geometry.coordinates[0][1], branch.geometry.coordinates[0][1]);
});

test("same-color junction stage does not snap parallel same-color lanes", () => {
  const trunk = lane("M", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const parallel = lane("BD", ["B", "D"], [P(0, 8), P(40, 8), P(90, 8)]);
  const artifacts = runStage([trunk, parallel]);
  assert.equal(artifacts.visualFeatures?.[1].properties.same_color_endpoint_snapped, undefined);
  assert.deepEqual(artifacts.visualFeatures?.[1].geometry.coordinates, parallel.geometry.coordinates);
});

test("same-color junction stage is deterministic and ignores empty or missing visualFeatures", () => {
  assert.deepEqual(runStage([]).visualFeatures, []);
  assert.equal(runStage(undefined).visualFeatures, undefined);

  const trunk = lane("M", ["M"], [P(-50, 0), P(0, 0), P(50, 0), P(150, 0)]);
  const branch = lane("BD", ["B", "D"], [P(48, 8), P(35, 30), P(20, 60)]);
  const first = runStage(cloneFeatures([trunk, branch]));
  const second = runStage(cloneFeatures([trunk, branch]));
  assert.equal(JSON.stringify(first.visualFeatures), JSON.stringify(second.visualFeatures));
});

test("same-color junction stage leaves a one-point linestring in place", () => {
  const stub = lane("stub", ["F"], [P(0, 0)]);
  const artifacts = runStage([stub]);
  assert.equal(artifacts.visualFeatures?.[0].properties.corridor_id, "stub");
  assert.deepEqual(artifacts.visualFeatures?.[0].geometry.coordinates, [[P(0, 0)[0], P(0, 0)[1]]]);
});

test("same-color junction stage co-locates a long parallel orange overlay onto the local", () => {
  const LAT = 40.74;
  const mLat = 110574;
  const mLng = 111320 * Math.cos((LAT * Math.PI) / 180);
  const pt = (xM: number, yM: number): Position => [xM / mLng, LAT + yM / mLat];
  const expressCoords: Position[] = [];
  for (let x = 0; x <= 4000; x += 50) {
    let y = 18;
    if (x < 800) y = 18 + (800 - x) * 0.2;
    else if (x > 2800) y = 18 + (x - 2800) * 0.2;
    expressCoords.push(pt(x, y));
  }
  const localCoords: Position[] = [];
  for (let x = 0; x <= 4000; x += 50) localCoords.push(pt(x, 0));
  const express = lane("express", ["F"], expressCoords);
  const local = lane("local", ["F", "M"], localCoords);
  const artifacts = runStage([express, local]);
  const moved = artifacts.visualFeatures?.find((feature) => feature.properties.corridor_id === "express");
  assert.ok(moved);
  assert.equal(moved.properties.same_color_colocated, true);
});

test("same-color junction stage flattens a baked joint step and drops the stale stitch", () => {
  const LAT = 40.65;
  const mLat = 110574;
  const mLng = 111320 * Math.cos((LAT * Math.PI) / 180);
  const baked = (x0: number, x1: number, yM: number): Position[] => {
    const coords: Position[] = [];
    for (let s = x0; s <= x1; s += 25) coords.push([s / mLng, LAT + yM / mLat]);
    return coords;
  };
  const south = lane("south", ["G"], baked(0, 600, 6), { lane_slot_semantic: 0.5, lane_offset_baked: true });
  const north = lane("north", ["G"], baked(600, 1200, 0), { lane_slot_semantic: 0, lane_offset_baked: true });
  const stitchEnd = south.geometry.coordinates.at(-1);
  const stitchStart = north.geometry.coordinates[0];
  assert.ok(stitchEnd && stitchStart);
  const stitch = lane("stitch", ["G"], [stitchEnd, stitchStart], { lane_slot_semantic: 0 });
  const artifacts = runStage([south, north, stitch], 12, 100);
  assert.equal(
    artifacts.visualFeatures?.some((feature) => feature.properties.corridor_id === "stitch"),
    false,
    "stale joint stitch should be dropped after the taper",
  );
  const southOut = artifacts.visualFeatures?.find((feature) => feature.properties.corridor_id === "south");
  assert.ok(southOut);
  assert.equal(southOut.properties.joint_offset_taper_baked, true);
});
