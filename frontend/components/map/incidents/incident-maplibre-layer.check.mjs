import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(here, "../../..");

const helperPath = resolve(here, "incident-maplibre-layer.ts");
const typesPath = resolve(here, "incident-marker-types.ts");
const tokensPath = resolve(here, "incident-marker-tokens.ts");
const markerSvgPath = resolve(here, "incident-marker-svg.tsx");
const jarvisMapPath = resolve(projectRoot, "components/jarvis-map.tsx");
const pagePath = resolve(projectRoot, "app/page.tsx");

assert.ok(
  existsSync(helperPath),
  "incident MapLibre bridge helper should exist",
);

const helper = readFileSync(helperPath, "utf8");
const types = readFileSync(typesPath, "utf8");
const tokens = readFileSync(tokensPath, "utf8");
const markerSvg = readFileSync(markerSvgPath, "utf8");
const jarvisMap = readFileSync(jarvisMapPath, "utf8");
const page = readFileSync(pagePath, "utf8");

const incident002Types = [
  "shooting",
  "stabbing",
  "medical",
  "fire",
  "police",
  "disruptive",
  "suspicious",
  "general",
];

const incident002Colors = {
  shooting: "#ef4444",
  stabbing: "#f97316",
  medical: "#ec4899",
  fire: "#fb923c",
  police: "#3b82f6",
  disruptive: "#eab308",
  suspicious: "#14b8a6",
  general: "#8b5cf6",
};

assert.match(
  helper,
  /export function ensureIncidentMapLibreLayers/,
  "helper should expose an idempotent layer installer",
);
assert.match(
  helper,
  /export function setIncidentMapLibreData/,
  "helper should expose a data updater for MapIncident[]",
);
assert.match(
  helper,
  /sr-map-incidents/,
  "helper should use a stable incident source id",
);
for (const type of incident002Types) {
  assert.match(
    types,
    new RegExp(`"${type}"`),
    `IncidentType union should include 002 category ${type}`,
  );
  assert.match(
    tokens,
    new RegExp(`${type}:\\s*{[\\s\\S]*?color:\\s*"${incident002Colors[type]}"`),
    `token ${type} should use the 002 marker color ${incident002Colors[type]}`,
  );
}
for (const oldType of ["assault", "weapon", "passenger"]) {
  assert.doesNotMatch(
    types,
    new RegExp(`"${oldType}"`),
    `legacy visual type ${oldType} should not remain in the IncidentType union`,
  );
}
assert.match(
  tokens,
  /weapon(?:-incident)?[\s\S]*return "shooting"/,
  "weapon aliases should normalize to shooting",
);
assert.match(
  tokens,
  /passenger[\s\S]*return "disruptive"/,
  "passenger aliases should normalize to disruptive",
);
assert.match(
  tokens,
  /suspicious[\s\S]*return "suspicious"/,
  "suspicious/package aliases should normalize to suspicious",
);
for (const criticalType of ["shooting", "stabbing", "fire"]) {
  assert.match(
    tokens,
    new RegExp(`${criticalType}:\\s*{[\\s\\S]*?critical:\\s*true`),
    `${criticalType} should be marked as critical for pulse eligibility`,
  );
}
for (const staticType of ["medical", "police", "disruptive", "suspicious", "general"]) {
  assert.match(
    tokens,
    new RegExp(`${staticType}:\\s*{[\\s\\S]*?critical:\\s*false`),
    `${staticType} should not pulse by category by default`,
  );
}
assert.match(
  helper,
  /marker_image/,
  "incident GeoJSON should expose a marker_image property for MapLibre icon-image",
);
assert.match(
  helper,
  /critical/,
  "incident GeoJSON should expose critical metadata",
);
assert.match(
  helper,
  /pulse/,
  "incident GeoJSON should expose pulse metadata",
);
assert.match(
  helper,
  /"icon-image": \["get", "marker_image"\]/,
  "MapLibre symbol layer should read icon-image from marker_image",
);
assert.match(
  helper,
  /"icon-anchor": "bottom"/,
  "MapLibre incident markers should anchor at the pin tip",
);
assert.match(
  helper,
  /filter: \["==", \["get", "pulse"\], true\]/,
  "MapLibre halo layer should pulse only precomputed critical active incidents",
);
assert.match(
  markerSvg,
  /viewBox=\{"0 0 44 56"\}/,
  "React marker SVG should use the 002 44x56 baseline-anchored viewBox",
);
assert.match(
  markerSvg,
  /IncidentMarkerGlyph/,
  "React marker SVG should render the shared 002 glyph language",
);
assert.match(
  jarvisMap,
  /incidents\?: MapIncident\[\]/,
  "JarvisMap should accept MapIncident[] as an optional prop",
);
assert.match(
  jarvisMap,
  /ensureIncidentMapLibreLayers/,
  "JarvisMap should install incident layers when the map style loads",
);
assert.match(
  page,
  /visibleMapIncidents/,
  "page should pass real incidents plus the dev preview marker into the map",
);
assert.match(
  page,
  /NODE_ENV !== "production"/,
  "synthetic incident preview should be development-only",
);

console.log("incident MapLibre bridge checks passed");
