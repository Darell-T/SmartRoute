import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-sidebar.tsx", import.meta.url)),
  "utf8",
);

test("sidebar icons share one 20px animated outline-to-fill system", () => {
  assert.match(SOURCE, /data-state=\{active \? "active" : engaged \? "engaged" : "rest"\}/);
  assert.match(SOURCE, /animated-icon-layer--outline/);
  assert.match(SOURCE, /animated-icon-layer--filled/);
  assert.match(SOURCE, /<OutlineIcon width=\{20\} height=\{20\}/);
  assert.match(SOURCE, /width=\{20\}[\s\S]*height=\{20\}[\s\S]*fill="currentColor"/);
});

test("pointer and keyboard engagement use the same state and reduced motion is honored", () => {
  assert.match(SOURCE, /const engaged = !disabled && \(hovered \|\| focused\)/);
  assert.match(SOURCE, /onPointerEnter=\{\(\) => setHovered\(true\)\}/);
  assert.match(SOURCE, /onFocus=\{\(\) => setFocused\(true\)\}/);
  assert.match(SOURCE, /duration: reduceMotion \? 0 : 0\.19/);
  assert.doesNotMatch(SOURCE, /scale: 1\.24/);
});

test("sidebar retains active-page and tooltip semantics", () => {
  assert.match(SOURCE, /aria-current=\{active \? "page" : undefined\}/);
  assert.match(SOURCE, /aria-label=\{tooltipLabel\}/);
  assert.match(SOURCE, /<TooltipContent side="right"/);
  assert.match(SOURCE, /aria-expanded=\{open && !collapsed\}/);
});
