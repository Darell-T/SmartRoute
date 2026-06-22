import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const root = fileURLToPath(new URL("../..", import.meta.url));
const subwayNetworkPath = resolve(root, "components/map/subway-network.ts");
const stationAnchorsPath = resolve(
  root,
  "public/subway-network.station-anchors.geojson",
);
const visualPath = resolve(root, "public/subway-network.visual.geojson");

const source = readFileSync(subwayNetworkPath, "utf8").replace(/\r\n/g, "\n");
const anchors = JSON.parse(readFileSync(stationAnchorsPath, "utf8"));
const visual = JSON.parse(readFileSync(visualPath, "utf8"));

function metersPerDegreeLng(lat) {
  return 111_320 * Math.cos((lat * Math.PI) / 180);
}

function distanceM(a, b) {
  const lat = (a[1] + b[1]) / 2;
  return Math.hypot(
    (a[0] - b[0]) * metersPerDegreeLng(lat),
    (a[1] - b[1]) * 110_540,
  );
}

function segmentDistanceM(point, a, b) {
  const lat = (point[1] + a[1] + b[1]) / 3;
  const scaleX = metersPerDegreeLng(lat);
  const px = (point[0] - a[0]) * scaleX;
  const py = (point[1] - a[1]) * 110_540;
  const bx = (b[0] - a[0]) * scaleX;
  const by = (b[1] - a[1]) * 110_540;
  const length2 = bx * bx + by * by;
  if (length2 === 0) return Math.hypot(px, py);
  const t = Math.max(0, Math.min(1, (px * bx + py * by) / length2));
  return Math.hypot(px - bx * t, py - by * t);
}

const visualLineStrings = (visual.features ?? []).flatMap((feature) => {
  if (feature.geometry?.type === "LineString") return [feature.geometry.coordinates];
  if (feature.geometry?.type === "MultiLineString") return feature.geometry.coordinates;
  return [];
});

function nearestVisualLineDistanceM(point) {
  let best = Infinity;
  for (const line of visualLineStrings) {
    for (let i = 1; i < line.length; i += 1) {
      best = Math.min(best, segmentDistanceM(point, line[i - 1], line[i]));
    }
  }
  return best;
}

function markerCenter(feature) {
  if (feature.geometry?.type === "Point") return feature.geometry.coordinates;
  if (feature.geometry?.type === "LineString") {
    const [start, end] = feature.geometry.coordinates;
    return [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
  }
  return null;
}

function markerFeatures(markerType) {
  return (anchors.features ?? []).filter(
    (feature) => feature.properties?.marker_type === markerType,
  );
}

function assertIncludes(needle, message) {
  assert.ok(source.includes(needle), message);
}

function assertMatches(pattern, message) {
  assert.ok(pattern.test(source), message);
}

assert.equal(anchors.type, "FeatureCollection", "station anchors must be GeoJSON");
assert.equal(
  anchors.metadata?.visual_generated_at,
  visual.metadata?.generated_at,
  "station anchors must be rebuilt from the current visual subway geometry",
);
assert.equal(
  anchors.metadata?.visual_geometry_source,
  visual.metadata?.visual_geometry_source,
  "station anchors must record the visual geometry source they were snapped to",
);

const singleDots = markerFeatures("single_stop_dot");
const sharedDots = markerFeatures("shared_stop_dot");
const sharedBars = markerFeatures("shared_stop_bar");
const labels = markerFeatures("station_label");
const badges = markerFeatures("station_route_badge");

assert.ok(singleDots.length > 0, "station anchors should include single_stop_dot");
assert.ok(sharedDots.length > 0, "station anchors should include shared_stop_dot");
assert.ok(sharedBars.length > 0, "station anchors should include shared_stop_bar");
assert.ok(labels.length > 0, "station anchors should include station_label");
assert.ok(badges.length > 0, "station anchors should include station_route_badge");

// Shared-stop bars are interchange capsules: they span the full served lane
// bundle so they cross EVERY line they serve, not just the nearest lane. They
// may therefore be longer than a single-lane tick, but must stay bounded and
// must actually lie on the lines (the bar crosses the bundle).
const MAX_SHARED_BAR_LENGTH_M = 60;
for (const feature of sharedBars) {
  assert.equal(feature.geometry?.type, "LineString", "shared bars must be LineString features");
  assert.equal(feature.geometry.coordinates.length, 2, "shared bars are a single segment");
  const [barStart, barEnd] = feature.geometry.coordinates;
  const lengthM = distanceM(barStart, barEnd);
  assert.ok(
    lengthM <= MAX_SHARED_BAR_LENGTH_M,
    `${feature.properties?.name} shared-stop bar is implausibly long: ${lengthM.toFixed(1)}m`,
  );
  // The bar must cross the bundle: at its closest approach it sits on a line.
  let minOnLine = Infinity;
  for (let i = 0; i <= 12; i += 1) {
    const t = i / 12;
    minOnLine = Math.min(
      minOnLine,
      nearestVisualLineDistanceM([
        barStart[0] + (barEnd[0] - barStart[0]) * t,
        barStart[1] + (barEnd[1] - barStart[1]) * t,
      ]),
    );
  }
  assert.ok(
    minOnLine <= 3,
    `${feature.properties?.name} shared-stop bar should lie on its served lanes (closest ${minOnLine.toFixed(1)}m)`,
  );
}

// Point stop markers (single beads / same-color shared dots) sit ON their
// line. Shared-stop bars instead center on the bundle MIDPOINT and bridge the
// lanes with the bar (checked above), so they are excluded from the
// center-on-a-single-lane assertion.
const pointStopMarkers = [...singleDots, ...sharedDots];
for (const feature of pointStopMarkers) {
  const center = markerCenter(feature);
  assert.ok(center, `${feature.properties?.name} stop marker must have a measurable center`);
  const centerDistanceM = nearestVisualLineDistanceM(center);
  assert.ok(
    centerDistanceM <= 1.5,
    `${feature.properties?.name} ${feature.properties?.marker_type} center is ${centerDistanceM.toFixed(1)}m from visual line`,
  );
}

// snapped_coordinate must match the rendered marker center for every stop type.
for (const feature of [...pointStopMarkers, ...sharedBars]) {
  const center = markerCenter(feature);
  assert.ok(
    distanceM(center, feature.properties?.snapped_coordinate) <= 0.5,
    `${feature.properties?.name} snapped_coordinate should match rendered marker center`,
  );
}

for (const feature of labels) {
  assert.ok(Array.isArray(feature.properties?.label_offset), "labels need precomputed label_offset");
  assert.ok(
    typeof feature.properties?.label_anchor === "string",
    "labels need precomputed label_anchor",
  );
}

for (const feature of badges) {
  assert.ok(feature.properties?.icon_id, "route badges need icon_id");
  assert.ok(Array.isArray(feature.properties?.icon_offset), "route badges need icon_offset");
}

assertIncludes(
  'export const SUBWAY_STATION_DOTS_SOURCE_ID = "sr-subway-station-dots"',
  "station dots must use their own MapLibre source",
);
assertIncludes(
  'export const SUBWAY_STATION_SHARED_STOPS_SOURCE_ID =\n  "sr-subway-station-shared-stops"',
  "shared stops must use their own MapLibre source",
);
assertIncludes(
  'export const SUBWAY_STATION_LABELS_SOURCE_ID = "sr-subway-station-labels"',
  "station labels must use their own MapLibre source",
);
assertIncludes(
  '"line-cap": "round"',
  "shared-stop bars should use rounded line caps",
);
assertIncludes(
  '"icon-image": ["get", "icon_id"]',
  "route badge layer must use generated icon_id",
);
assertIncludes(
  '"icon-offset": ["get", "icon_offset"]',
  "route badge layer must use generated icon_offset",
);
assertIncludes(
  '"text-offset": ["coalesce", ["get", "label_offset"], ["literal", [0, 1.0]]]',
  "station labels must use generated label_offset",
);
assertMatches(
  /SUBWAY_STATION_SINGLE_DOTS_LAYER_ID[\s\S]*?"circle-opacity"[\s\S]*?12\.8,\s*0,\s*13\.6,\s*0\.7,/,
  "single station dots should stay hidden until z12.8 and fade in gently",
);
assertMatches(
  /SUBWAY_STATION_SHARED_DOTS_LAYER_ID[\s\S]*?"circle-opacity"[\s\S]*?12\.8,\s*0,\s*13\.6,\s*0\.85,/,
  "shared station dots should stay hidden until z12.8 and fade in gently",
);
assertMatches(
  /SUBWAY_STATION_SHARED_BAR_CASING_LAYER_ID[\s\S]*?"line-opacity"[\s\S]*?12\.4,\s*0,\s*13\.2,\s*0\.65,/,
  "shared-stop bar casing should not cut through line bundles at low zoom",
);
assertMatches(
  /SUBWAY_STATION_SHARED_BAR_FILL_LAYER_ID[\s\S]*?"line-opacity"[\s\S]*?12\.4,\s*0,\s*13\.2,\s*0\.75,/,
  "shared-stop bar fill should not cut through line bundles at low zoom",
);

assert.ok(!source.includes("oklch("), "MapLibre paint must not use oklch colors");

console.log(
  JSON.stringify(
    {
      single_stop_dot: singleDots.length,
      shared_stop_dot: sharedDots.length,
      shared_stop_bar: sharedBars.length,
      station_label: labels.length,
      station_route_badge: badges.length,
      max_shared_bar_length_m: Math.max(
        ...sharedBars.map((feature) =>
          distanceM(feature.geometry.coordinates[0], feature.geometry.coordinates[1]),
        ),
      ),
      max_point_stop_center_distance_m: Math.max(
        ...pointStopMarkers.map((feature) => nearestVisualLineDistanceM(markerCenter(feature))),
      ),
    },
    null,
    2,
  ),
);
