import assert from "node:assert/strict";
import test from "node:test";

import { colocateSameColorStretches } from "./colocate-same-color.mjs";

const M_PER_DEG_LAT = 110574;
const LAT = 40.74;
const M_PER_DEG_LNG = 111320 * Math.cos((LAT * Math.PI) / 180);

function pt(xM, yM) {
  return [xM / M_PER_DEG_LNG, LAT + yM / M_PER_DEG_LAT];
}

function lateralM(point) {
  return (point[1] - LAT) * M_PER_DEG_LAT;
}

// West->east line from x0..x1 at lateral yM, vertices every stepM.
function line(x0, x1, yM, stepM = 50) {
  const coords = [];
  for (let x = x0; x <= x1; x += stepM) coords.push(pt(x, yM));
  return coords;
}

function lane(coords, routeIds, color, id) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      visual_feature_type: "bundle_lane",
      route_ids: routeIds,
      color,
      corridor_id: id,
    },
  };
}

// QB shape: express (F) runs 18m from the local (F+M) for 2km in the middle;
// its ends swing far away (>30m) so the run is interior.
function expressCoords() {
  const coords = [];
  for (let x = 0; x <= 4000; x += 50) {
    let y;
    if (x < 800) y = 18 + (800 - x) * 0.2; // swings out to ~178m at x=0
    else if (x <= 2800) y = 18; // parallel stretch
    else y = 18 + (x - 2800) * 0.2;
    coords.push(pt(x, y));
  }
  return coords;
}

test("pulls the fewer-route lane onto its same-color neighbor over the parallel stretch", () => {
  const express = lane(expressCoords(), ["F"], "#FF6319", "express");
  const local = lane(line(0, 4000, 0), ["F", "M"], "#FF6319", "local");

  const result = colocateSameColorStretches([express, local], {
    minGapM: 10,
    maxGapM: 30,
    minStretchM: 500,
    blendM: 100,
  });

  assert.equal(result.count, 1, "one stretch co-located");
  const coords = express.geometry.coordinates;
  // Deep inside the stretch (x ~1800): on the local track.
  const mid = coords[Math.round(1800 / 50)];
  assert.ok(Math.abs(lateralM(mid)) < 0.1, `mid on local track, got ${lateralM(mid)}m`);
  // Far ends: unmoved.
  assert.ok(Math.abs(lateralM(coords[0]) - 178) < 2, "west end unmoved");
  // Local never moves.
  assert.ok(local.geometry.coordinates.every((p) => Math.abs(lateralM(p)) < 1e-9));
  assert.equal(express.properties.same_color_colocated, true);
  assert.equal(local.properties.same_color_colocated, undefined);
});

test("route-count tie: the longer overlay moves onto the shorter local alignment", () => {
  // Build-stage shape of the QB pair: the F lane is route_ids [F] and the
  // local lane is still [M] (F joins it in a later pass). Counts tie, so
  // length decides: the long through-running overlay moves; the short local
  // alignment (which owns the intermediate stations) stays.
  const express = lane(expressCoords(), ["F"], "#FF6319", "express-long");
  const local = lane(line(600, 3400, 0), ["M"], "#FF6319", "local-short");

  const result = colocateSameColorStretches([express, local], {
    minGapM: 10,
    maxGapM: 30,
    minStretchM: 500,
    blendM: 100,
  });

  assert.equal(result.count, 1);
  const mid = express.geometry.coordinates[Math.round(1800 / 50)];
  assert.ok(Math.abs(lateralM(mid)) < 0.1, "long overlay pulled onto the local track");
  assert.ok(local.geometry.coordinates.every((p) => Math.abs(lateralM(p)) < 1e-9), "local never moves");
});

test("close pairs below minGapM are left alone (already fuse in paint)", () => {
  const a = lane(line(0, 2000, 6), ["4", "5"], "#00933C", "a");
  const b = lane(line(0, 2000, 0), ["4", "6"], "#00933C", "b");
  const result = colocateSameColorStretches([a, b], { minGapM: 10, maxGapM: 30, minStretchM: 500 });
  assert.equal(result.count, 0);
  assert.ok(Math.abs(lateralM(a.geometry.coordinates[10]) - 6) < 0.01);
});

test("different colors and short overlaps are ignored", () => {
  const orange = lane(line(0, 2000, 18), ["F"], "#FF6319", "orange");
  const blue = lane(line(0, 2000, 0), ["E"], "#0039A6", "blue");
  assert.equal(colocateSameColorStretches([orange, blue], {}).count, 0);

  const shortA = lane(line(0, 300, 18), ["N", "W"], "#FCCC0A", "short-a");
  const shortB = lane(line(0, 300, 0), ["R"], "#FCCC0A", "short-b");
  assert.equal(
    colocateSameColorStretches([shortA, shortB], { minStretchM: 500 }).count,
    0,
  );
});
