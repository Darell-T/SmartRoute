import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// Guards the single-renderer invariant after the cleanup: the visual.geojson
// network is the only subway renderer, live train markers stay removed, and no
// production code re-introduces a renderer branch keyed on ?subway-visual /
// ?subway-recon or the deleted legacy bbox builder.
const ROOT = path.resolve(import.meta.dirname, "../..");

function read(relPath) {
  return fs.readFileSync(path.join(ROOT, relPath), "utf8");
}

const mapSource = read("components/smart-route/map/smart-route-map.tsx");
const mapHelpersSource = read("components/smart-route/map/smart-route-map-helpers.ts");

// 1. The visual renderer is wired in and reads the visual artifact. The map
// owns the runtime call; the helper owns the content-hashed artifact URL.
for (const needle of ["loadVisualSubwayNetworkOrNull", "buildSubwayLaneFeaturesFromVisual"]) {
  assert.ok(
    mapSource.includes(needle),
    `smart-route-map.tsx should use the visual renderer (${needle})`,
  );
}
assert.ok(
  mapHelpersSource.includes('artifactUrl("subway-network.visual.geojson")'),
  "smart-route-map-helpers.ts should load the visual subway artifact",
);

// 2. Live train vehicle markers are not part of the current map direction.
for (const pattern of [
  /ensureLiveTrainLayers/,
  /LIVE_TRAIN/,
  /VehicleMarker/,
  /SmartTrainMarker/,
  /sr-train-marker/,
  /loadCanonicalSubwayNetwork/,
  /buildSubwayNetworkIndex/,
]) {
  assert.doesNotMatch(
    mapSource + mapHelpersSource,
    pattern,
    `SmartRoute map must not reintroduce live train marker plumbing (${pattern})`,
  );
}

// 3. No renderer flag / branch may come back.
const forbiddenInMap = [
  /subwayVisualEnabled/,
  /subwayReconEnabled/,
  /USE_SUBWAY_VISUAL_ENV/,
  /loadReconSubwayNetworkOrNull/,
  /subway-network\.recon\.geojson/,
  // legacy bbox builder call (anything but the visual builder)
  /buildSubwayLaneFeatures(?!FromVisual)/,
];
for (const pattern of forbiddenInMap) {
  assert.doesNotMatch(
    mapSource,
    pattern,
    `smart-route-map.tsx must not reintroduce ${pattern}`,
  );
}

// 4. The legacy bbox builder + its hardcoded corridor table are gone from the
//    renderer module and cannot come back.
const networkSource = read("components/map/subway-network.ts");
for (const pattern of [
  /SHARED_CORRIDORS/,
  /pickLongestShapePerRoute/,
  /buildSubwayLaneFeatures(?!FromVisual)/,
]) {
  assert.doesNotMatch(
    networkSource,
    pattern,
    `subway-network.ts must not reintroduce the legacy renderer (${pattern})`,
  );
}

// 5. Visual geometry is already lane-offset-baked. Runtime may add a
//    screen-space top-up at borough/neighborhood zooms because baked meter
//    offsets collapse to sub-pixel distances when the user is zoomed out.
//    The top-up must be large enough to keep same-bundle colors visibly
//    distinct, but still capped so independently emitted LineString pieces do
//    not pull apart at seams.
const laneOffsetCalls = [
  ...networkSource.matchAll(/laneOffsetAt\(([-\d.]+),\s*([-\d.]+)\)/g),
].map((match) => ({
  fullPerSlotPx: Number(match[1]),
  bakedTopUpPx: Number(match[2]),
}));
assert.ok(laneOffsetCalls.length > 0, "LANE_OFFSET_EXPR should use laneOffsetAt");
const maxBakedTopUpPx = Math.max(
  ...laneOffsetCalls.map((call) => call.bakedTopUpPx),
);
assert.ok(
  maxBakedTopUpPx >= 2.25,
  `baked visual lanes need >=2.25px low/mid zoom top-up so bundled colors remain distinct; got ${maxBakedTopUpPx}px`,
);
assert.ok(
  maxBakedTopUpPx <= 2.6,
  `baked visual lanes should not receive >2.6px runtime top-up; got ${maxBakedTopUpPx}px`,
);

function extractLayerSection(source, startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  const end = source.indexOf(endNeedle, start);
  assert.ok(start >= 0, `missing layer section start ${startNeedle}`);
  assert.ok(end > start, `missing layer section end ${endNeedle}`);
  return source.slice(start, end);
}

function extractPaintStops(section, propertyName) {
  const propStart = section.indexOf(`"${propertyName}": [`);
  assert.ok(propStart >= 0, `missing paint property ${propertyName}`);
  const nextProp = section.indexOf(`"line-offset"`, propStart);
  assert.ok(nextProp > propStart, `could not isolate ${propertyName} block`);
  const block = section.slice(propStart, nextProp);
  return new Map(
    [...block.matchAll(/\n\s*(\d+(?:\.\d+)?),\s*\n\s*(\d+(?:\.\d+)?),/g)].map(
      (match) => [Number(match[1]), Number(match[2])],
    ),
  );
}

const fillLayerSection = extractLayerSection(
  networkSource,
  "id: SUBWAY_FILL_LAYER_ID",
  "id: SUBWAY_HIGHLIGHT_LAYER_ID",
);
const fillLayoutStart = fillLayerSection.indexOf("layout: {");
const fillPaintStart = fillLayerSection.indexOf("paint: {");
assert.ok(fillLayoutStart >= 0, "fill layer should have a layout block");
assert.ok(fillPaintStart > fillLayoutStart, "fill layer paint block should follow layout");
const fillLayoutSection = fillLayerSection.slice(fillLayoutStart, fillPaintStart);
assert.ok(
  fillLayoutSection.includes("lane_slot_semantic"),
  "subway fill paint order should follow semantic lane position, not hard-coded route color priority",
);
assert.doesNotMatch(
  fillLayoutSection,
  /"#00933C"\s*,\s*11/,
  "4/5/6 green must not be hard-coded as the top-painted contested bundle color",
);
assert.doesNotMatch(
  fillLayoutSection,
  /top of the contested bundle/i,
  "subway fill sort comments should not preserve the old green-overpaint rule",
);
assert.ok(
  networkSource.includes('"#00933C", "#24A85B"'),
  "4/5/6 map-fill green should be display-balanced (#24A85B), not the overly dominant previous green",
);
const fillWidthStops = extractPaintStops(fillLayerSection, "line-width");
assert.ok(
  (fillWidthStops.get(11) ?? Infinity) <= 1.25,
  `z11 subway fill should stay thin enough for bundled colors to separate; got ${fillWidthStops.get(11)}`,
);
assert.ok(
  (fillWidthStops.get(13) ?? Infinity) <= 2.15,
  `z13 subway fill should not close the inter-lane gutter; got ${fillWidthStops.get(13)}`,
);
assert.ok(
  (fillWidthStops.get(14) ?? Infinity) <= 2.55,
  `z14 subway fill should preserve a visible gutter between adjacent color lanes; got ${fillWidthStops.get(14)}`,
);

console.log(
  JSON.stringify(
    {
      renderer: "subway-network.visual.geojson",
      legacyBranch: "removed",
      maxBakedTopUpPx,
      fillWidth: {
        z11: fillWidthStops.get(11),
        z13: fillWidthStops.get(13),
        z14: fillWidthStops.get(14),
      },
      fillSort: "semantic-lane-slot",
      displayGreen: "#24A85B",
    },
    null,
    2,
  ),
);
