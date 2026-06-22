import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(here, "../../..");

const popupPath = resolve(here, "incident-popup.ts");
const layerPath = resolve(here, "incident-maplibre-layer.ts");
const jarvisMapPath = resolve(projectRoot, "components/jarvis-map.tsx");
const cssPath = resolve(projectRoot, "app/styles/smart-route-live-feed.css");

assert.ok(existsSync(popupPath), "incident popup helper should exist");

const popup = readFileSync(popupPath, "utf8");
const layer = readFileSync(layerPath, "utf8");
const jarvisMap = readFileSync(jarvisMapPath, "utf8");
const css = readFileSync(cssPath, "utf8");

assert.match(
  popup,
  /export function incidentFeatureToPopupViewModel/,
  "popup helper should convert MapLibre feature properties into a view model",
);
assert.match(
  popup,
  /export function renderIncidentPopupHtml/,
  "popup helper should render popup HTML",
);
assert.match(
  popup,
  /escapeHtml/,
  "popup renderer should escape backend/user strings before HTML injection",
);
assert.match(
  popup,
  /formatIncidentElapsed/,
  "popup helper should format T+ elapsed labels",
);
assert.match(
  popup,
  /--sr-incident-popup-accent/,
  "popup HTML should set the accent CSS variable from the incident color",
);
assert.match(
  layer,
  /category:/,
  "incident feature properties should include category for popup display",
);
assert.match(
  layer,
  /source:/,
  "incident feature properties should include source text for popup display",
);
assert.match(
  jarvisMap,
  /new maplibregl\.Popup/,
  "JarvisMap should create a MapLibre Popup for incident marker clicks",
);
assert.match(
  jarvisMap,
  /incidentPopupRef/,
  "JarvisMap should keep a reusable incident popup ref",
);
assert.match(
  jarvisMap,
  /renderIncidentPopupHtml/,
  "JarvisMap should render the typed incident popup HTML",
);
assert.match(
  jarvisMap,
  /incidentPopupRef\.current\?\.remove/,
  "JarvisMap should remove the popup on background click or cleanup",
);
for (const className of [
  ".sr-incident-popup",
  ".sr-incident-popup__beam",
  ".sr-incident-popup__rail",
  ".sr-incident-popup__content",
]) {
  assert.match(css, new RegExp(className.replace(".", "\\.")), `CSS should define ${className}`);
}
assert.match(
  css,
  /--sr-incident-popup-accent/,
  "popup CSS should use the incident accent CSS variable",
);
assert.match(
  css,
  /prefers-reduced-motion: reduce[\s\S]*sr-incident-popup__beam/,
  "popup CSS should disable continuous beam/rail animations for reduced motion",
);

console.log("incident popup checks passed");
