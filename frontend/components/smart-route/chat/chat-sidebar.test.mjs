import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ChatSidebar } from "./chat-sidebar.tsx";

const CSS_SOURCE = fs.readFileSync(
  fileURLToPath(
    new URL("../../../app/styles/smart-route-sidebar.css", import.meta.url),
  ),
  "utf8",
);
const SHELL_CSS_SOURCE = fs.readFileSync(
  fileURLToPath(
    new URL("../../../app/styles/smart-route-tab-shell.css", import.meta.url),
  ),
  "utf8",
);

function renderSidebar(overrides = {}) {
  return renderToStaticMarkup(
    createElement(ChatSidebar, {
      activeTab: "chat",
      collapsed: false,
      theme: "dark",
      onOpenChat: () => {},
      onOpenLiveMap: () => {},
      onNewTrip: () => {},
      onToggleCollapsed: () => {},
      onToggleTheme: () => {},
      ...overrides,
    }),
  );
}

test("sidebar icons share one restrained 20px outline system", () => {
  const html = renderSidebar();
  assert.match(html, /data-state="active"/);
  assert.match(html, /width="20"/);
  assert.match(html, /height="20"/);
  assert.match(html, /stroke-width="1.85"|strokeWidth="1.85"/);
  assert.doesNotMatch(html, /animated-icon-layer--filled/);
  assert.doesNotMatch(html, /fill="currentColor"/);
});

test("pointer and keyboard engagement keep rest motion on the server tree", () => {
  const html = renderSidebar();
  assert.match(html, /data-state="rest"/);
  assert.match(html, /data-reduced-motion="false"/);
  assert.doesNotMatch(html, /scale: 1\.24/);
});

test("sidebar defers client reduced-motion until hydration", () => {
  const html = renderSidebar();
  assert.match(html, /data-reduced-motion="false"/);
});

test("sidebar retains active-page and tooltip semantics without future destinations", () => {
  const html = renderSidebar({ activeTab: "livemap" });
  assert.match(html, /aria-current="page"/);
  assert.match(html, /aria-label="Transit Map"/);
  assert.match(html, /aria-label="Chat"/);
  assert.match(html, /aria-label="New Trip"/);
  assert.doesNotMatch(html, /Coming soon|Favorites|Feedback|Help|Settings/);
  assert.doesNotMatch(html, /aria-disabled/);
  assert.doesNotMatch(html, /data-disabled/);
});

test("sidebar uses a neutral Grok-like rail without Nearby Lines or green active styling", () => {
  const html = renderSidebar();
  assert.match(html, /New Trip/);
  assert.match(html, /Transit Map/);
  assert.doesNotMatch(html, /Nearby Lines/);
  assert.doesNotMatch(html, /nearbyRouteIds/);
  assert.doesNotMatch(CSS_SOURCE, /--sr-sidebar-accent/);
  assert.doesNotMatch(CSS_SOURCE, /#22c55e|#2ee85f|rgba\(46,\s*232,\s*95/i);
  assert.match(CSS_SOURCE, /\.sr-app-sidebar\[data-collapsed="true"\][\s\S]*inset: 4px/);
});

test("light sidebar uses one hairline separator without a dark shadow", () => {
  const html = renderSidebar({ theme: "light" });
  assert.match(html, /data-theme="light"/);
  assert.match(
    CSS_SOURCE,
    /\.sr-app-sidebar\[data-theme="light"\]\s*\{[\s\S]*?border-right-color:\s*var\(--sr-sidebar-line\);/,
  );
  assert.match(
    CSS_SOURCE,
    /\.sr-app-sidebar\[data-theme="light"\]\s*\{[\s\S]*?box-shadow:\s*none;/,
  );
  assert.match(CSS_SOURCE, /border-right:\s*1px solid var\(--sr-sidebar-line\);/);
});

test("collapsed sidebar and shell reserve the same width", () => {
  const html = renderSidebar({ collapsed: true });
  assert.match(html, /data-collapsed="true"/);
  assert.match(html, /aria-label="Expand sidebar"/);
  assert.match(
    CSS_SOURCE,
    /\.sr-app-sidebar\[data-collapsed="true"\]\s*\{[\s\S]*?--sr-sidebar-width:\s*58px;/,
  );
  assert.match(
    SHELL_CSS_SOURCE,
    /\.sr-tab-shell\[data-sidebar-collapsed="true"\]\s*\{[\s\S]*?--sr-shell-sidebar-width:\s*58px;/,
  );
});
