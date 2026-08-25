import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { recommendedCardsForChat } from "./recommended-card-selection.ts";

const CARD_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./recommended-itinerary-card.tsx", import.meta.url)),
  "utf8",
);
const LEG_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./itinerary-card-legs.tsx", import.meta.url)),
  "utf8",
);
const CARD_RENDER_SOURCE = `${CARD_SOURCE}\n${LEG_SOURCE}`;
const CHAT_CSS_SOURCE = fs.readFileSync(
  fileURLToPath(
    new URL("../../../app/styles/smart-route-chat.css", import.meta.url),
  ),
  "utf8",
);
const CHAT_MESSAGE_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-message.tsx", import.meta.url)),
  "utf8",
);
const CHAT_PANEL_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./chat-panel.tsx", import.meta.url)),
  "utf8",
);

const cards = [
  { card_id: "recommended", role: "recommended" },
  { card_id: "alternative-1", role: "alternative" },
  { card_id: "alternative-2", role: "alternative" },
];

test("chat renders only the recommended route without mutating map alternatives", () => {
  const visible = recommendedCardsForChat(cards);

  assert.deepEqual(visible.map((card) => card.card_id), ["recommended"]);
  assert.deepEqual(cards.map((card) => card.card_id), [
    "recommended",
    "alternative-1",
    "alternative-2",
  ]);
});

test("chat does not promote an alternative when no recommendation exists", () => {
  assert.deepEqual(recommendedCardsForChat(cards.slice(1)), []);
});

test("recommendation card keeps transit details collapsed by default", () => {
  assert.match(
    CARD_SOURCE,
    /useState<Set<string>>\(\(\) => new Set\(\)\)/,
  );
  assert.match(LEG_SOURCE, /aria-expanded=\{expanded\}/);
  assert.match(LEG_SOURCE, /aria-controls=\{`\$\{event\.id\}-stops`\}/);
  assert.match(LEG_SOURCE, /onClick=\{onToggle\}/);
  assert.match(LEG_SOURCE, /\{expanded && stops\.length > 0 \?/);
  assert.match(
    CARD_SOURCE,
    /if \(next\.has\(eventId\)\) next\.delete\(eventId\);[\s\S]*else next\.add\(eventId\);/,
  );
  assert.match(CARD_SOURCE, /<motion\.article[\s\S]*?\blayout\b/);
});

test("recommendation card uses the same quiet shell as arrivals", () => {
  const routeShell = CHAT_CSS_SOURCE.match(
    /\.sr-chat-tab \.sr-itinerary-card\s*\{([^}]*)\}/,
  )?.[1];
  const arrivalShell = CHAT_CSS_SOURCE.match(
    /\.sr-chat-arrivals-card\s*\{([^}]*)\}/,
  )?.[1];

  assert.ok(routeShell);
  assert.ok(arrivalShell);
  assert.match(routeShell, /gap:\s*10px/);
  assert.match(routeShell, /border-radius:\s*16px/);
  assert.match(routeShell, /border:\s*1px solid var\(--sr-chat-hairline\)/);
  assert.match(routeShell, /background:\s*var\(--sr-chat-surface\)/);
  assert.match(routeShell, /box-shadow:\s*var\(--sr-chat-raised-shadow\)/);
  assert.match(arrivalShell, /gap:\s*10px/);
  assert.match(arrivalShell, /border-radius:\s*16px/);
  assert.match(arrivalShell, /border:\s*1px solid var\(--sr-chat-hairline\)/);
  assert.match(arrivalShell, /background:\s*var\(--sr-chat-surface\)/);
  assert.match(arrivalShell, /box-shadow:\s*var\(--sr-chat-raised-shadow\)/);
  assert.doesNotMatch(CARD_SOURCE, /BorderBeam|border-beam/);
  assert.doesNotMatch(routeShell, /18px 44px|inset/);
});

test("recommendation card keeps the route hierarchy compact", () => {
  assert.match(CARD_SOURCE, /sr-itinerary-card__summary/);
  assert.match(CARD_SOURCE, /model\.arrivalLabel && model\.firstLegArrivalLabel/);
  assert.doesNotMatch(CARD_SOURCE, /faArrowRightArrowLeft/);
  assert.match(LEG_SOURCE, /TrainBullet line=\{normalized\} size=\{24\}/);
  assert.match(LEG_SOURCE, /sr-itinerary-card__walk-duration/);
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-chat-tab \.sr-itinerary-card__duration-value\s*\{[\s\S]*?font-size:\s*20px;[\s\S]*?font-weight:\s*600;[\s\S]*?line-height:\s*24px;/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__leg,[\s\S]*?grid-template-columns:\s*24px minmax\(0, 1fr\) auto;[\s\S]*?border-top:\s*0;/,
  );
});

test("recommendation card uses restrained three-level typography", () => {
  assert.match(
    CHAT_CSS_SOURCE,
    /--sr-itinerary-primary:[\s\S]*?--sr-itinerary-secondary:[\s\S]*?--sr-itinerary-tertiary:/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-chat-tab \.sr-itinerary-card__arrive\s*\{[\s\S]*?font-size:\s*12px;[\s\S]*?font-weight:\s*400;[\s\S]*?line-height:\s*16px;/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__leg-heading,[\s\S]*?font-size:\s*14px;[\s\S]*?font-weight:\s*550;[\s\S]*?line-height:\s*19px;/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__station\s*\{[\s\S]*?font-size:\s*13px;[\s\S]*?font-weight:\s*500;[\s\S]*?line-height:\s*18px;/,
  );
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__disclosure,[\s\S]*?font-size:\s*12px;[\s\S]*?font-weight:\s*400;[\s\S]*?line-height:\s*17px;/,
  );
});

test("recommendation card preserves total duration and route-colored chains", () => {
  assert.match(CARD_SOURCE, /model\.durationLabel/);
  assert.match(CARD_SOURCE, /model\.metaParts\.map/);
  assert.match(LEG_SOURCE, /getRouteColor\(event\.routeIds\[0\]/);
  assert.match(LEG_SOURCE, /duration: 0\.3, ease: LAYOUT_EASE/);
  assert.match(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__chain-marker--start,[\s\S]*?background: var\(--sr-route-color\)/,
  );
  assert.doesNotMatch(
    CHAT_CSS_SOURCE,
    /\.sr-itinerary-card__chain-marker--(?:start|end)::after/,
  );
});

test("chat card omits the redundant recommendation badge without changing recommendation data", () => {
  assert.doesNotMatch(CARD_SOURCE, /sr-itinerary-card__badge/);
  assert.match(CARD_SOURCE, /data-selected=\{isSelected/);
});

test("bus legs use a compact bus glyph, plain route text, and the shared chain", () => {
  assert.match(CARD_RENDER_SOURCE, /className="sr-itinerary-card__bus-glyph"/);
  assert.match(CARD_RENDER_SOURCE, /className="sr-itinerary-card__bus-route"/);
  assert.match(CARD_RENDER_SOURCE, /event\.kind === "bus"/);
  assert.match(CARD_RENDER_SOURCE, /className="sr-itinerary-card__chain-track"/);
  assert.doesNotMatch(CARD_RENDER_SOURCE, /<TrainBullet line=\{normalized\} size=\{34\}/);
});

test("Open on map remains a direct keyboard-accessible action", () => {
  assert.match(CARD_SOURCE, /<motion\.button[\s\S]*?type="button"/);
  assert.match(CARD_SOURCE, /aria-label=\{model\.primaryActionLabel\}/);
  assert.match(CARD_SOURCE, /disabled=\{!onPrimaryAction\}/);
  assert.match(CARD_SOURCE, /onClick=\{onPrimaryAction\}/);
  assert.doesNotMatch(CARD_SOURCE, /onClick=\{\(\) => onPrimaryAction/);
});

test("transit status exposes View alerts only from the typed action flag", () => {
  assert.match(CHAT_MESSAGE_SOURCE, /turn\.transitStatusAction === "view_alerts"/);
  assert.match(CHAT_MESSAGE_SOURCE, /className="sr-chat-transit-action"/);
  assert.match(CHAT_MESSAGE_SOURCE, /onClick=\{onViewAlerts\}/);
  assert.doesNotMatch(CHAT_MESSAGE_SOURCE, /turn\.text\.toLowerCase\(\)[\s\S]*alert|includes\(\s*["'][^)]*alert/i);
  assert.match(CHAT_PANEL_SOURCE, /onViewAlerts\?: \(\) => void/);
  assert.match(CHAT_PANEL_SOURCE, /onViewAlerts=\{onViewAlerts\}/);
  assert.match(CHAT_CSS_SOURCE, /\.sr-chat-transit-action\s*\{/);
});
