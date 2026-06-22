import assert from "node:assert/strict";
import test from "node:test";

import { applyCulverFgProspectSmoothing } from "./culver-fg-prospect-smoothing.mjs";

const LAT = 40.655;
const LON = -73.977;
const M_PER_DEG_LAT = 110574;
const M_PER_DEG_LNG = 111320 * Math.cos((LAT * Math.PI) / 180);

function P(xM, yM) {
  return [LON + xM / M_PER_DEG_LNG, LAT + yM / M_PER_DEG_LAT];
}

function line(id, color, routes, coords) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {
      visual_feature_type: "bundle_lane",
      corridor_id: id,
      color,
      route_ids: routes,
      color_route_ids: routes,
      lane_offset_baked: true,
    },
  };
}

function bearing(a, b) {
  const x = (b[0] - a[0]) * M_PER_DEG_LNG;
  const y = (b[1] - a[1]) * M_PER_DEG_LAT;
  return Math.atan2(x, y);
}

function tangentDeltaDeg(a, b, c) {
  let delta = Math.abs(bearing(a, b) - bearing(b, c)) * 180 / Math.PI;
  if (delta > 180) delta = 360 - delta;
  return delta;
}

function distPointToLineM(point, coords) {
  const px = (point[0] - LON) * M_PER_DEG_LNG;
  const py = (point[1] - LAT) * M_PER_DEG_LAT;
  let best = Infinity;
  for (let i = 1; i < coords.length; i += 1) {
    const a = coords[i - 1];
    const b = coords[i];
    const ax = (a[0] - LON) * M_PER_DEG_LNG;
    const ay = (a[1] - LAT) * M_PER_DEG_LAT;
    const bx = (b[0] - LON) * M_PER_DEG_LNG;
    const by = (b[1] - LAT) * M_PER_DEG_LAT;
    const dx = bx - ax;
    const dy = by - ay;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy || 1)));
    best = Math.min(best, Math.hypot(px - (ax + dx * t), py - (ay + dy * t)));
  }
  return best;
}

test("Culver F/G Prospect smoothing removes a connected-but-jagged G seam", () => {
  const orange = line("f", "#FF6319", ["F"], [
    P(0, -260),
    P(2, -160),
    P(3, -60),
    P(4, 60),
    P(4, 180),
    P(2, 300),
  ]);
  const greenSouth = line("g-south", "#6CBE45", ["G"], [
    P(14, -260),
    P(15, -160),
    P(13, -70),
    P(8, -20),
    P(1, 0),
  ]);
  // Real artifact orientation: the northern piece ends at the same seam.
  const greenNorth = line("g-north", "#6CBE45", ["G"], [
    P(4, 300),
    P(3, 190),
    P(2, 95),
    P(1, 0),
  ]);

  assert.ok(
    tangentDeltaDeg(
      greenSouth.geometry.coordinates.at(-2),
      greenSouth.geometry.coordinates.at(-1),
      greenNorth.geometry.coordinates.at(-2),
    ) > 10,
    "fixture starts with a visible seam kink",
  );

  const { features, diagnostics } = applyCulverFgProspectSmoothing([orange, greenSouth, greenNorth], {
    bbox: {
      minLon: LON - 0.004,
      maxLon: LON + 0.004,
      minLat: LAT - 0.004,
      maxLat: LAT + 0.004,
    },
    marginM: 180,
    targetSeparationM: 14,
    sampleM: 5,
    smoothingPasses: 2,
  });

  assert.equal(diagnostics.applied, true);
  const south = features.find((feature) => feature.properties.corridor_id === "g-south");
  const north = features.find((feature) => feature.properties.corridor_id === "g-north");
  const seam = south.geometry.coordinates.at(-1);
  assert.deepEqual(seam, north.geometry.coordinates.at(-1));

  const afterDelta = tangentDeltaDeg(
    south.geometry.coordinates.at(-2),
    seam,
    north.geometry.coordinates.at(-2),
  );
  assert.ok(afterDelta < 10, `expected a smooth seam, got ${afterDelta.toFixed(2)}deg`);
  assert.equal(south.properties.culver_fg_prospect_smoothing, true);
  assert.equal(north.properties.culver_fg_prospect_smoothing, true);

  for (const point of south.geometry.coordinates.slice(-8)) {
    assert.ok(distPointToLineM(point, orange.geometry.coordinates) >= 10);
  }
});
