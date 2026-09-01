import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { MobileNavigation } from "./mobile-navigation.tsx";
import { MobileStage } from "./mobile-stage.tsx";
import { MobileTopBar } from "./mobile-top-bar.tsx";

const CSS_SOURCE = fs.readFileSync(
  new URL("../../../app/styles/smart-route-mobile-shell.css", import.meta.url),
  "utf8",
);

test("open mobile navigation is a dialog with only available destinations", () => {
  const html = renderToStaticMarkup(
    createElement(MobileNavigation, {
      open: true,
      activeTab: "chat",
      theme: "dark",
      onClose() {},
      onOpenChat() {},
      onOpenLiveMap() {},
      onNewTrip() {},
      onToggleTheme() {},
    }),
  );
  assert.match(html, /role="dialog"/);
  assert.match(html, /aria-modal="true"/);
  assert.match(html, /aria-label="SmartRoute navigation"/);
  assert.match(html, /New Trip/);
  assert.match(html, />Chat</);
  assert.match(html, /Transit Map/);
  assert.match(html, /aria-label="Close navigation"/);
  assert.match(html, /Switch to light mode/);
  assert.doesNotMatch(html, /Coming soon|Bookmark|Settings/);
  const closed = renderToStaticMarkup(
    createElement(MobileNavigation, {
      open: false,
      activeTab: "livemap",
      theme: "light",
      onClose() {},
      onOpenChat() {},
      onOpenLiveMap() {},
      onNewTrip() {},
      onToggleTheme() {},
    }),
  );
  assert.doesNotMatch(closed, /role="dialog"/);
});

test("mobile chrome exposes a reachable menu trigger and optional brand", () => {
  const withBrand = renderToStaticMarkup(
    createElement(MobileTopBar, {
      navigationOpen: false,
      showBrand: true,
      onOpenNavigation() {},
      onNewTrip() {},
    }),
  );
  assert.match(withBrand, /aria-label="Open navigation menu"/);
  assert.match(withBrand, /aria-expanded="false"/);
  assert.match(withBrand, /Start a new SmartRoute trip/);
  const liveMap = renderToStaticMarkup(
    createElement(MobileTopBar, {
      navigationOpen: true,
      showBrand: false,
      onOpenNavigation() {},
      onNewTrip() {},
    }),
  );
  assert.match(liveMap, /aria-expanded="true"/);
  assert.doesNotMatch(liveMap, /sr-mobile-top-bar__brand/);
});

test("mobile stage marks the page inert while navigation is open", () => {
  const html = renderToStaticMarkup(
    createElement(
      MobileStage,
      { navigationOpen: true, onDismissNavigation() {} },
      createElement("p", null, "Chat"),
    ),
  );
  assert.match(html, /data-navigation-open="true"/);
  assert.match(html, /aria-hidden="true"/);
  assert.match(html, /sr-mobile-stage-dismiss/);
  const closed = renderToStaticMarkup(
    createElement(
      MobileStage,
      { navigationOpen: false, onDismissNavigation() {} },
      createElement("p", null, "Chat"),
    ),
  );
  assert.match(closed, /data-navigation-open="false"/);
  assert.doesNotMatch(closed, /sr-mobile-stage-dismiss/);
});

test("mobile shell CSS keeps a 52px drag sliver without toy-green fills", () => {
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
