import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const NAVIGATION_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./mobile-navigation.tsx", import.meta.url)),
  "utf8",
);
const TOP_BAR_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./mobile-top-bar.tsx", import.meta.url)),
  "utf8",
);
const PAGE_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("../../../app/page.tsx", import.meta.url)),
  "utf8",
);
const STAGE_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./mobile-stage.tsx", import.meta.url)),
  "utf8",
);
const CSS_SOURCE = fs.readFileSync(
  fileURLToPath(
    new URL("../../../app/styles/smart-route-mobile-shell.css", import.meta.url),
  ),
  "utf8",
);

test("mobile navigation is a draggable full-canvas page with an interruptible spring", () => {
  assert.match(PAGE_SOURCE, /<MobileStage/);
  assert.match(STAGE_SOURCE, /DRAG_ACTIVATION_DISTANCE = 10/);
  assert.match(STAGE_SOURCE, /setPointerCapture/);
  assert.match(STAGE_SOURCE, /onPointerMove=\{handleDismissPointerMove\}/);
  assert.match(STAGE_SOURCE, /stageAnimationRef\.current\?\.stop\(\)/);
  assert.match(STAGE_SOURCE, /CLOSE_DISTANCE_RATIO = 0\.28/);
  assert.match(STAGE_SOURCE, /CLOSE_VELOCITY = -460/);
  assert.match(STAGE_SOURCE, /type: "spring"/);
  assert.match(
    STAGE_SOURCE,
    /aria-hidden=\{navigationOpen \? true : undefined\}/,
  );
  assert.match(NAVIGATION_SOURCE, /<AnimatePresence initial=\{false\}>/);
  assert.match(NAVIGATION_SOURCE, /role="dialog"/);
  assert.match(NAVIGATION_SOURCE, /aria-modal="true"/);
});

test("mobile navigation preserves the existing sidebar icon language", () => {
  assert.match(NAVIGATION_SOURCE, /icon=\{MessageCircle\}/);
  assert.match(NAVIGATION_SOURCE, /icon=\{MapIcon\}/);
  assert.match(NAVIGATION_SOURCE, /icon=\{Bookmark\}/);
  assert.match(NAVIGATION_SOURCE, /icon=\{MessageSquareText\}/);
  assert.match(NAVIGATION_SOURCE, /icon=\{CircleHelp\}/);
  assert.match(NAVIGATION_SOURCE, /icon=\{Settings\}/);
  assert.doesNotMatch(NAVIGATION_SOURCE, /BrainIcon|ZapIcon/);
});

test("mobile chrome stays neutral and exposes reachable primary controls", () => {
  assert.match(TOP_BAR_SOURCE, /aria-label="Open navigation menu"/);
  assert.doesNotMatch(TOP_BAR_SOURCE, /Open transit map|Open chat/);
  assert.doesNotMatch(TOP_BAR_SOURCE, /MapIcon|MessageCircle/);
  assert.match(STAGE_SOURCE, /aria-label="Close navigation"/);
  assert.doesNotMatch(NAVIGATION_SOURCE, /\bX\b|Close navigation menu/);
  assert.match(CSS_SOURCE, /touch-action:\s*none/);
  assert.match(CSS_SOURCE, /min-height:\s*52px/);
  assert.match(
    CSS_SOURCE,
    /\.sr-mobile-navigation__item\[data-active="true"\][\s\S]*background:\s*var\(--sr-mobile-nav-fill\)/,
  );
  assert.doesNotMatch(
    CSS_SOURCE,
    /#22c55e|#2ee85f|#3ed134|rgba\(46,\s*232,\s*95/i,
  );
});

test("mobile branding is removed while the live transit map is active", () => {
  assert.match(PAGE_SOURCE, /showBrand=\{!isLivemapTab\}/);
  assert.match(TOP_BAR_SOURCE, /\{showBrand \? \(/);
});
