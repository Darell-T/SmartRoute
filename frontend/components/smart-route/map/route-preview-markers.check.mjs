import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();
const markerSource = fs.readFileSync(
  path.join(ROOT, "components/smart-route/map/route-preview-markers.ts"),
  "utf8",
);
const mapSource = fs.readFileSync(
  path.join(ROOT, "components/smart-route/map/smart-route-map.tsx"),
  "utf8",
);

assert.match(
  markerSource,
  /sr-current-location-marker__accuracy/,
  "current-location marker should include a zoom-scaled accuracy area",
);
assert.match(
  markerSource,
  /sr-current-location-marker__fill/,
  "current-location marker should keep the blue fill within the white ring",
);
assert.match(
  markerSource,
  /prefers-reduced-motion/,
  "current-location marker animation should respect reduced-motion settings",
);
assert.match(
  markerSource,
  /metersPerPixelAtLatitude/,
  "accuracy halo should be computed from real meters-to-pixels math",
);
assert.match(
  mapSource,
  /accuracyMeters:\s*position\.coords\.accuracy/,
  "SmartRouteMap should pass browser geolocation accuracy to the origin marker",
);
assert.match(
  mapSource,
  /map\.current\.on\("zoom",\s*syncCurrentLocationAccuracy\)/,
  "accuracy halo should resize when the map zoom changes",
);
assert.doesNotMatch(
  markerSource,
  /rgba\(56,\s*189,\s*248,\s*0\.18\)/,
  "current-location marker should not regress to the old static cyan halo",
);
assert.match(
  markerSource,
  /height:31px/,
  "destination pin element should end at the visual pin tip for stable map anchoring",
);
assert.match(
  markerSource,
  /<svg width="34" height="31" viewBox="0 0 34 31"/,
  "destination pin SVG should not include visual space below the geographic tip",
);
assert.doesNotMatch(
  markerSource,
  /<ellipse/,
  "destination pin should not carry a below-tip ellipse that shifts the perceived anchor",
);
assert.match(
  mapSource,
  /anchor:\s*"bottom"[\s\S]*offset:\s*\[0,\s*0\]/,
  "MapLibre should anchor the destination pin bottom directly on the destination coordinate",
);
