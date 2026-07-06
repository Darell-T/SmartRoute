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
  /srCurrentLocationFill/,
  "current-location marker should animate the blue fill within the white ring",
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
