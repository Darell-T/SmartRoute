import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);

function loadTsModule(relativePath) {
  const helperPath = new URL(relativePath, import.meta.url);
  const helperSource = readFileSync(helperPath, "utf8");
  const helperModule = { exports: {} };
  const transpiled = ts.transpileModule(helperSource, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
      resolveJsonModule: true,
    },
  });

  vm.runInNewContext(
    transpiled.outputText,
    {
      Image: class {},
      console,
      exports: helperModule.exports,
      module: helperModule,
      require: (specifier) => {
        if (specifier === "./subway-lane-separation") {
          return loadTsModule("./subway-lane-separation.ts");
        }
        return require(specifier);
      },
    },
    { filename: helperPath.pathname },
  );

  return helperModule.exports;
}

const { validateStyleMin } = require("@maplibre/maplibre-gl-style-spec");
const helperModuleExports = loadTsModule("./subway-network.ts");
const jarvisMapSource = readFileSync(
  new URL("../jarvis-map.tsx", import.meta.url),
  "utf8",
);
const {
  SUBWAY_NETWORK_LINE_LAYER_ID,
  normalizeSubwayNetworkFocusState,
  normalizeSubwayFocusRouteIds,
  subwayRouteFocusFilter,
  subwayFocusedLineOpacityExpression,
  subwayFocusedLineWidthExpression,
  subwayBulletOpacityExpression,
} = helperModuleExports;

assert.equal(
  typeof normalizeSubwayFocusRouteIds,
  "function",
  "focus route normalization should be exported",
);
assert.equal(
  SUBWAY_NETWORK_LINE_LAYER_ID,
  "sr-subway-network-lines",
  "line layer id should be exported for map route click focus",
);
assert.equal(
  typeof normalizeSubwayNetworkFocusState,
  "function",
  "structured focus state normalization should be exported",
);
assert.equal(
  typeof subwayRouteFocusFilter,
  "function",
  "focus route filter should be exported",
);

const normalized = normalizeSubwayFocusRouteIds([
  " q ",
  "6d",
  "7x",
  "sir",
  "q",
  "",
  null,
]);

assert.deepEqual(
  JSON.parse(JSON.stringify(normalized)),
  ["Q", "6X", "6D", "7X", "7D", "SI", "SIR"],
  "route focus ids should normalize, expand express aliases, dedupe, and preserve order",
);

const focusState = normalizeSubwayNetworkFocusState({
  selectedRouteIds: [" q "],
  incidentRouteIds: ["4"],
  nearbyRouteIds: ["g", "Q"],
});

assert.deepEqual(
  JSON.parse(JSON.stringify(focusState.selectedRouteIds)),
  ["Q"],
  "selected routes should normalize independently",
);
assert.deepEqual(
  JSON.parse(JSON.stringify(focusState.incidentRouteIds)),
  ["4"],
  "incident-affected routes should normalize independently",
);
assert.deepEqual(
  JSON.parse(JSON.stringify(focusState.nearbyRouteIds)),
  ["G", "Q"],
  "nearby routes should normalize independently without losing selected overlap",
);
assert.deepEqual(
  JSON.parse(JSON.stringify(focusState.sameFamilySiblingRouteIds)),
  ["N", "R", "W"],
  "same-family siblings should be derived from selected routes",
);
assert.deepEqual(
  JSON.parse(JSON.stringify(focusState.allEmphasisRouteIds)),
  ["Q", "4", "G"],
  "all-emphasis routes should dedupe selected, incident, and nearby buckets",
);

assert.deepEqual(
  JSON.parse(JSON.stringify(subwayRouteFocusFilter([]))),
  ["==", ["get", "route_id"], "__sr-no-focused-route__"],
  "empty focus filters should match no routes safely",
);

const focusedFilter = subwayRouteFocusFilter(normalized);

const style = {
  version: 8,
  sources: {
    subway: {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    },
  },
  layers: [
    {
      id: "focused-line-test",
      type: "line",
      source: "subway",
      paint: {
        "line-opacity": subwayFocusedLineOpacityExpression(
          focusState,
          "line",
        ),
        "line-width": subwayFocusedLineWidthExpression(focusState, "line"),
      },
    },
    {
      id: "focused-bullet-test",
      type: "symbol",
      source: "subway",
      filter: focusedFilter,
      layout: {
        "symbol-placement": "line",
        "icon-image": "mta-q",
        "icon-size": 0.5,
      },
      paint: {
        "icon-opacity": subwayBulletOpacityExpression(focusState),
      },
    },
  ],
};

const lineOpacityExpression = JSON.stringify(
  subwayFocusedLineOpacityExpression(focusState, "line"),
);
// Updated for the MTA-poster-bold opacity rebalance: line layer states are now
// idle 0.92, selected 1, incident 0.98, nearby 0.94, sibling 0.86, background 0.44.
// (was: 0.52 / 0.96 / 0.9 / 0.78 / 0.52 / 0.24 in the dusk-mode tuning.)
assert.match(
  lineOpacityExpression,
  /(?:^|\D)1(?:\D|$)/,
  "selected route opacity should be the strongest route state (now 1.0)",
);
assert.match(
  lineOpacityExpression,
  /0\.98/,
  "incident-affected routes should be promoted without recoloring",
);
assert.match(
  lineOpacityExpression,
  /0\.94/,
  "nearby routes should sit above the background network",
);
assert.match(
  lineOpacityExpression,
  /0\.86/,
  "same-family siblings should stay visible but subdued",
);
assert.match(
  lineOpacityExpression,
  /0\.44/,
  "unrelated routes should dim to a quiet background level",
);
assert.match(
  jarvisMapSource,
  /SUBWAY_NETWORK_LINE_LAYER_ID/,
  "JarvisMap should use the exported subway line layer id for route click focus",
);
assert.match(
  jarvisMapSource,
  /INCIDENT_MAPLIBRE_LAYER_ID/,
  "JarvisMap should use the incident marker layer id for incident-affected route focus",
);
assert.match(
  jarvisMapSource,
  /setSelectedMapRouteIds/,
  "JarvisMap should maintain map-click selected route focus",
);

assert.deepEqual(
  validateStyleMin(style).map((error) => error.message),
  [],
  "focused subway paint/filter expressions should be valid MapLibre syntax",
);

console.log("subway network focus checks passed");
