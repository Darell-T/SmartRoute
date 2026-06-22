// Route-stop feature builders: coords-first, interpolation fallback, walk
// dashes. Run: node --experimental-strip-types --test components/map/route-stops.check.mjs
import assert from "node:assert/strict";
import test from "node:test";
import polyline from "@mapbox/polyline";

import {
  buildRouteStopFeatures,
  buildWalkFeatures,
  interpolateAlongLine,
} from "./route-stops-features.ts";

// Encoded path along a straight west->east line (lat,lng pairs for encode).
const LINE = [
  [40.7, -73.99],
  [40.7, -73.98],
  [40.7, -73.97],
];
const ENCODED = polyline.encode(LINE);

function subwayStep(extra = {}) {
  return {
    type: "SUBWAY",
    route_id: "Q",
    train_line: "Q",
    line_color: "#FCCC0A",
    departure_stop: "A St",
    arrival_stop: "C St",
    polyline: { encodedPolyline: ENCODED },
    ...extra,
  };
}

test("uses real intermediate stop locations when present", () => {
  const steps = [
    subwayStep({
      intermediate_stop_locations: [
        { name: "A St", lat: 40.7, lng: -73.99 },
        { name: "B St", lat: 40.7, lng: -73.98 },
        { name: "C St", lat: 40.7, lng: -73.97 },
      ],
    }),
  ];
  const fc = buildRouteStopFeatures(steps);
  assert.equal(fc.features.length, 3);
  const b = fc.features[1];
  assert.deepEqual(b.geometry.coordinates, [-73.98, 40.7]);
  assert.equal(b.properties.name, "B St");
  assert.equal(b.properties.color, "#FCCC0A");
  assert.equal(b.properties.line, "Q");
  // First + last stops are shown as roundel badges, so their dot labels blank.
  assert.equal(fc.features[0].properties.name, "", "origin label blanked");
  assert.equal(fc.features[2].properties.name, "", "destination label blanked");
});

test("snaps off-line stop coordinates onto the drawn polyline", () => {
  const steps = [
    subwayStep({
      intermediate_stop_locations: [
        // Curbside-style coords offset north of the line (lat 40.7).
        { name: "A St", lat: 40.7008, lng: -73.99 },
        { name: "B St", lat: 40.7012, lng: -73.98 },
        { name: "C St", lat: 40.6995, lng: -73.97 },
      ],
    }),
  ];
  const fc = buildRouteStopFeatures(steps);
  assert.equal(fc.features.length, 3);
  // All three snap back onto the line's latitude (40.7) while keeping lng.
  for (const f of fc.features) {
    assert.ok(Math.abs(f.geometry.coordinates[1] - 40.7) < 1e-6, `snapped lat, got ${f.geometry.coordinates[1]}`);
  }
  assert.ok(Math.abs(fc.features[1].geometry.coordinates[0] - -73.98) < 1e-6);
});

test("falls back to interpolating named stops along the decoded polyline", () => {
  const steps = [
    subwayStep({
      intermediate_stops: ["A St", "B St", "C St"],
      intermediate_stop_locations: [],
    }),
  ];
  const fc = buildRouteStopFeatures(steps);
  assert.equal(fc.features.length, 3);
  const mid = fc.features[1].geometry.coordinates;
  assert.ok(Math.abs(mid[0] - -73.98) < 1e-6, `interpolated mid lng, got ${mid[0]}`);
  assert.equal(fc.features[1].properties.interpolated, true);
});

test("falls back to stop_count dots when enrichment has no coords or names", () => {
  // A leg whose GTFS lookup came back empty still has Google's stop count +
  // polyline, so dots must still render (unlabeled) along the line.
  const steps = [
    subwayStep({
      intermediate_stop_locations: [],
      intermediate_stops: [],
      stop_count: 3,
    }),
  ];
  const fc = buildRouteStopFeatures(steps);
  assert.equal(fc.features.length, 4, "stop_count + 1 evenly spaced dots");
  assert.ok(fc.features.every((f) => f.properties.name === ""), "fallback dots are unlabeled");
  assert.ok(
    fc.features.every((f) => Math.abs(f.geometry.coordinates[1] - 40.7) < 1e-6),
    "fallback dots sit on the line",
  );
});

test("walk steps become dashed-line features; transit steps do not", () => {
  const steps = [
    { type: "WALK", polyline: { encodedPolyline: ENCODED } },
    subwayStep(),
  ];
  const fc = buildWalkFeatures(steps);
  assert.equal(fc.features.length, 1);
  assert.equal(fc.features[0].geometry.type, "LineString");
  assert.equal(fc.features[0].geometry.coordinates.length, 3);
});

test("steps without polylines or stops yield no features", () => {
  const fc = buildRouteStopFeatures([{ type: "SUBWAY", route_id: "Q" }]);
  assert.equal(fc.features.length, 0);
  const walks = buildWalkFeatures([{ type: "WALK" }]);
  assert.equal(walks.features.length, 0);
});

test("interpolateAlongLine midpoints", () => {
  const coords = [
    [0, 0],
    [10, 0],
  ];
  assert.deepEqual(interpolateAlongLine(coords, 0.5), [5, 0]);
});
