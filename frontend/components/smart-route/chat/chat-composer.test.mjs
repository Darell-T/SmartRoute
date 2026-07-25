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
const MODE_MENU_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./response-mode-menu.tsx", import.meta.url)),
  "utf8",
);

test("composer exposes Auto and Quick through an accessible custom menu", () => {
  assert.match(COMPOSER_SOURCE, /<ResponseModeMenu/);
  assert.doesNotMatch(COMPOSER_SOURCE, /<select/);
  assert.doesNotMatch(COMPOSER_SOURCE, /<option/);
  assert.match(MODE_MENU_SOURCE, /value: "auto"/);
  assert.match(MODE_MENU_SOURCE, /value: "quick"/);
  assert.match(MODE_MENU_SOURCE, /role="menuitemradio"/);
  assert.match(MODE_MENU_SOURCE, /aria-checked=\{selected\}/);
  assert.match(MODE_MENU_SOURCE, /aria-haspopup="menu"/);
  assert.match(MODE_MENU_SOURCE, /aria-expanded=\{open\}/);
  assert.match(MODE_MENU_SOURCE, /Quick changes response length, not route choice or travel time\./);
});

test("response menu opens upward and supports keyboard navigation", () => {
  assert.match(MODE_MENU_SOURCE, /bottom: window\.innerHeight - rect\.top \+ 8/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "ArrowDown"/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "ArrowUp"/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "Home"/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "End"/);
  assert.match(MODE_MENU_SOURCE, /event\.key === "Escape"/);
});

test("composer actions use the shared Prompt Kit action primitive", () => {
  assert.match(COMPOSER_SOURCE, /<PromptInputActions className="sr-chat-composer__actions">/);
  assert.match(COMPOSER_SOURCE, /<PromptInputAction[\s\S]*Use voice input/);
  assert.match(COMPOSER_SOURCE, /<PromptInputAction\s+tooltip=\{isStreaming \? "Stop response" : "Send message"\}/);
});

test("changing presentation does not regenerate a completed response", () => {
  assert.match(PANEL_SOURCE, /onPresentationModeChange=\{setPresentationMode\}/);
  assert.match(PANEL_SOURCE, /onSend=\{\(text\) => chat\.send\(text, presentationMode\)\}/);
  assert.doesNotMatch(PANEL_SOURCE, /useEffect\(\(\) => \{\s*chat\.send/);
});
