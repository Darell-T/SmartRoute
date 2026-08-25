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
const mobileShellCss = readFileSync(
  new URL("../../../app/styles/smart-route-mobile-shell.css", import.meta.url),
  "utf8",
);
const viewportSource = readFileSync(
  new URL("../../../lib/use-mobile-visible-viewport.ts", import.meta.url),
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

test("mobile chat has one visible-height owner and an in-flow three-track shell", () => {
  assert.match(
    mobileShellCss,
    /height:\s*var\(--visible-viewport-height,\s*100dvh\)/,
  );
  assert.match(
    mobileShellCss,
    /grid-template-rows:\s*auto minmax\(0,\s*1fr\)/,
  );
  assert.match(
    css,
    /\.sr-chat-tab-inner\s*\{[\s\S]*?grid-template-rows:\s*minmax\(0,\s*1fr\) auto/,
  );
  assert.match(
    css,
    /\.sr-chat-tab-inner\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\)/,
  );
  assert.match(
    css,
    /\.sr-chat-thread,[\s\S]*?overflow-y:\s*auto;[\s\S]*?overscroll-behavior:\s*contain/,
  );
  assert.match(
    css,
    /\.sr-chat-interaction-dock\s*\{[\s\S]*?width:\s*100%;[\s\S]*?min-width:\s*0/,
  );
  assert.match(
    mobileShellCss,
    /inset:\s*var\(--mobile-viewport-offset-top,\s*0px\) 0 auto/,
  );
  assert.match(viewportSource, /--visible-viewport-height/);
  assert.match(viewportSource, /--mobile-viewport-offset-top/);
});

test("mobile suggestion affordance gains a left fade only after horizontal scrolling", () => {
  assert.match(welcomeSource, /data-scrolled=\{scrolledFromStart \? "true" : "false"\}/);
  assert.match(welcomeSource, /scrollLeft > 4/);
  assert.match(welcomeSource, /tabIndex=\{-1\}/);
  assert.match(css, /\.sr-chat-empty__suggestions\[data-scrolled="true"\]/);
  assert.match(css, /scroll-snap-align:\s*start/);
});

test("Near You has no accordion chevrons and suggestions stay text-only", () => {
  assert.doesNotMatch(nearbySource, /Chevron|chevron/);
  assert.doesNotMatch(welcomeSource, /AirplaneIcon|UsersRoundIcon|SoupIcon/);
  assert.doesNotMatch(welcomeSource, /sr-chat-suggestion-icon/);
  assert.match(
    css,
    /\.sr-chat-suggestion-pill\s*\{[\s\S]*?border:\s*0;[\s\S]*?background:\s*transparent;/,
  );
  assert.doesNotMatch(css, /sr-chat-suggestion-(?:separator|dot)/);
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

test("Near You is borderless and spacing-led with no container or divider borders", () => {
  const nearbyBlocks = [...css.matchAll(/\.sr-home-nearby\s*\{([^}]*)\}/g)];
  assert.ok(nearbyBlocks.length >= 2);
  for (const [, body] of nearbyBlocks) {
    assert.match(body, /(?:gap|row-gap):/);
    assert.doesNotMatch(
      body,
      /(?:overflow|min-height|grid-template-rows):/,
    );
  }
  assert.match(
    nearbyBlocks[0][1],
    /width:\s*min\(calc\(100% - 32px\),\s*var\(--sr-home-content-width\)\)/,
  );
  assert.match(
    css,
    /@media \(max-width:\s*720px\)[\s\S]*?\.sr-home-nearby\s*\{[^}]*width:\s*100%;/,
  );
  assert.match(nearbyBlocks[0][1], /border:\s*0;/);
  assert.match(nearbyBlocks[0][1], /border-radius:\s*0;/);
  assert.match(nearbyBlocks[0][1], /background:\s*transparent;/);
  assert.match(css, /\.sr-home-nearby__arrivals\s*\{[^}]*column-gap:/);
  assert.match(css, /\.sr-home-nearby__skeletons\s*\{[^}]*column-gap:/);
  assert.doesNotMatch(
    css,
    /\.sr-home-nearby__(?:arrivals|condition|issue|service-area)\s*\{[^}]*border-top:/,
  );
  assert.doesNotMatch(
    css,
    /\.sr-home-nearby__(?:arrival|skeleton-arrival)\s*\+\s*\.sr-home-nearby__(?:arrival|skeleton-arrival)\s*\{[^}]*border-left:/,
  );
  assert.match(
    css,
    /\.sr-home-nearby__arrival:hover,\s*\.sr-home-nearby__arrival:focus-visible\s*\{[^}]*transform:\s*translateY\(-2px\);/,
  );
  assert.doesNotMatch(
    css,
    /\.sr-home-nearby__arrival:hover,\s*\.sr-home-nearby__arrival:focus-visible\s*\{[^}]*background:/,
  );
  assert.match(
    css,
    /\.sr-home-nearby__arrival:focus-visible\s*\{[^}]*outline:/,
  );
});

test("submitted messages use the accessible blue bubble token", () => {
  const bubble = css.match(/\.sr-chat-bubble\s*\{([^}]*)\}/)?.[1];
  assert.ok(bubble);
  assert.match(css, /--sr-chat-user-bubble:\s*#0071e3;/);
  assert.match(bubble, /background:\s*var\(--sr-chat-user-bubble\);/);
  assert.match(bubble, /color:\s*#fff;/);
  assert.match(bubble, /border-radius:\s*18px 18px 6px 18px;/);
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
