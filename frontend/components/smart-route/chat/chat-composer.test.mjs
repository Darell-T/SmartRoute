import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const COMPOSER_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-composer.tsx", import.meta.url)),
  "utf8",
);
const PANEL_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-panel.tsx", import.meta.url)),
  "utf8",
);

test("composer exposes compact Auto and Quick presentation choices accessibly", () => {
  assert.match(COMPOSER_SOURCE, /aria-label="Response style"/);
  assert.match(COMPOSER_SOURCE, /<option value="auto">Auto<\/option>/);
  assert.match(COMPOSER_SOURCE, /<option value="quick">Quick<\/option>/);
});

test("changing presentation does not regenerate a completed response", () => {
  assert.match(PANEL_SOURCE, /onPresentationModeChange=\{setPresentationMode\}/);
  assert.match(PANEL_SOURCE, /onSend=\{\(text\) => chat\.send\(text, presentationMode\)\}/);
  assert.doesNotMatch(PANEL_SOURCE, /useEffect\(\(\) => \{\s*chat\.send/);
});
