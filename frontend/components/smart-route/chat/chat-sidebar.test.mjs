import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-sidebar.tsx", import.meta.url)),
  "utf8",
);
const CSS_SOURCE = fs.readFileSync(
  fileURLToPath(
    new URL("../../../app/styles/smart-route-sidebar.css", import.meta.url),
  ),
  "utf8",
);
const TOOLTIP_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("../../ui/tooltip.tsx", import.meta.url)),
  "utf8",
);

test("sidebar icons share one restrained 20px outline system", () => {
  assert.match(SOURCE, /data-state=\{active \? "active" : engaged \? "engaged" : "rest"\}/);
  assert.match(SOURCE, /<Icon width=\{20\} height=\{20\} strokeWidth=\{1\.85\}/);
  assert.doesNotMatch(SOURCE, /animated-icon-layer--filled/);
  assert.doesNotMatch(SOURCE, /fill="currentColor"/);
});

test("pointer and keyboard engagement use the same state and reduced motion is honored", () => {
  assert.match(SOURCE, /const engaged = !disabled && \(hovered \|\| focused\)/);
  assert.match(SOURCE, /onPointerEnter=\{\(\) => setHovered\(true\)\}/);
  assert.match(SOURCE, /onFocus=\{\(\) => setFocused\(true\)\}/);
  assert.match(SOURCE, /duration: reduceMotion \? 0 : 0\.19/);
  assert.doesNotMatch(SOURCE, /scale: 1\.24/);
});

test("sidebar defers the client reduced-motion preference until hydration", () => {
  assert.match(SOURCE, /useSyncExternalStore/);
  assert.match(
    SOURCE,
    /useSyncExternalStore\(subscribeToHydration, \(\) => true, \(\) => false\)/,
  );
  assert.match(SOURCE, /const prefersReducedMotion = useReducedMotion\(\) \?\? false/);
  assert.match(SOURCE, /const reduceMotion = hydrated && prefersReducedMotion/);
});

test("sidebar retains active-page and tooltip semantics", () => {
  assert.match(SOURCE, /aria-current=\{active \? "page" : undefined\}/);
  assert.match(SOURCE, /aria-label=\{tooltipLabel\}/);
  assert.match(SOURCE, /<TooltipContent side="right"/);
  assert.match(SOURCE, /aria-disabled=\{disabled \|\| undefined\}/);
  assert.match(SOURCE, /<span>Coming soon<\/span>/);
  assert.match(SOURCE, /onClick=\{disabled \? undefined : onClick\}/);
  assert.doesNotMatch(SOURCE, /disabled=\{disabled\}/);
  assert.doesNotMatch(TOOLTIP_SOURCE, /TooltipPrimitive\.Arrow/);
});

test("sidebar uses a neutral Grok-like rail without Nearby Lines or green active styling", () => {
  assert.match(SOURCE, /SquarePen/);
  assert.match(SOURCE, /icon=\{MapIcon\}/);
  assert.doesNotMatch(SOURCE, /Nearby Lines/);
  assert.doesNotMatch(SOURCE, /nearbyRouteIds/);
  assert.doesNotMatch(CSS_SOURCE, /--sr-sidebar-accent/);
  assert.doesNotMatch(CSS_SOURCE, /#22c55e|#2ee85f|rgba\(46,\s*232,\s*95/i);
  assert.match(CSS_SOURCE, /\.sr-app-sidebar\[data-collapsed="true"\][\s\S]*inset: 4px/);
});

test("light sidebar uses a shadow separator without a dark border or layout change", () => {
  assert.match(
    CSS_SOURCE,
    /\.sr-app-sidebar\[data-theme="light"\]\s*\{[\s\S]*?border-right-color:\s*transparent;/,
  );
  assert.match(
    CSS_SOURCE,
    /\.sr-app-sidebar\[data-theme="light"\]\s*\{[\s\S]*?box-shadow:\s*8px 0 20px rgba\(15,\s*17,\s*19,\s*0\.055\);/,
  );
  assert.match(CSS_SOURCE, /border-right:\s*1px solid var\(--sr-sidebar-line\);/);
});
