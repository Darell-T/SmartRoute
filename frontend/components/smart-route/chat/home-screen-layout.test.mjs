import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(
  new URL("../../../app/styles/smart-route-chat.css", import.meta.url),
  "utf8",
);
const nearbySource = readFileSync(
  new URL("./home-near-you.tsx", import.meta.url),
  "utf8",
);
const welcomeSource = readFileSync(
  new URL("./chat-welcome.tsx", import.meta.url),
  "utf8",
);
const panelSource = readFileSync(
  new URL("./chat-panel.tsx", import.meta.url),
  "utf8",
);

test("desktop home surfaces share one responsive content width", () => {
  assert.match(
    css,
    /--sr-home-content-width:\s*clamp\(780px,\s*calc\(30vw \+ 388px\),\s*960px\)/,
  );
  assert.match(
    css,
    /\.sr-chat-composer-beam\s*\{[\s\S]*?width:\s*min\(calc\(100% - 32px\),\s*var\(--sr-home-content-width\)\)/,
  );
});

test("mobile interaction dock is safe-area aware and the rail collapses from composer focus state", () => {
  assert.match(css, /scroll-snap-type:\s*x mandatory/);
  assert.match(css, /overflow-x:\s*auto/);
  assert.match(css, /\.sr-chat-empty__suggestions\[data-hidden="true"\]/);
  assert.match(css, /:has\(\.sr-chat-composer:focus-within\)/);
  assert.match(css, /env\(safe-area-inset-bottom\)/);
  assert.match(css, /\.sr-chat-interaction-dock\s*\{[\s\S]*?flex-direction:\s*column/);
  assert.match(panelSource, /hidden=\{composerFocused\}/);
  assert.match(panelSource, /addEventListener\("focusin", handleFocusIn\)/);
  assert.match(panelSource, /addEventListener\("focusout", handleFocusOut\)/);
  assert.match(panelSource, /chat\.messages\.length === 0/);
});

test("mobile suggestion affordance gains a left fade only after horizontal scrolling", () => {
  assert.match(welcomeSource, /data-scrolled=\{scrolledFromStart \? "true" : "false"\}/);
  assert.match(welcomeSource, /scrollLeft > 4/);
  assert.match(welcomeSource, /tabIndex=\{-1\}/);
  assert.match(css, /\.sr-chat-empty__suggestions\[data-scrolled="true"\]/);
  assert.match(css, /scroll-snap-align:\s*start/);
});

test("Near You has no accordion chevrons and suggestion glyphs are animated", () => {
  assert.doesNotMatch(nearbySource, /Chevron|chevron/);
  assert.match(welcomeSource, /startAnimation\(\)/);
  assert.match(welcomeSource, /onHoverStart/);
});

test("Near You distinguishes active refresh from an unavailable live feed", () => {
  assert.match(nearbySource, /LoaderCircle/);
  assert.match(nearbySource, /sr-home-nearby__loading-spinner/);
  assert.match(nearbySource, /RadioTower/);
  assert.match(nearbySource, /title=\{arrival\.destination\}/);
  assert.match(css, /@keyframes srHomeNearbySpin/);
  assert.match(
    css,
    /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.sr-home-nearby__loading-spinner\s*\{[\s\S]*?animation:\s*none;/,
  );
});

test("desktop shortcuts use a restrained responsive gap while mobile remains compact", () => {
  assert.match(
    css,
    /\.sr-chat-empty__suggestions\s*\{[\s\S]*?gap:\s*clamp\(16px,\s*1\.15vw,\s*22px\)/,
  );
  assert.match(
    css,
    /@media \(max-width:\s*720px\)[\s\S]*?\.sr-chat-empty__suggestions\s*\{[\s\S]*?gap:\s*8px/,
  );
});
