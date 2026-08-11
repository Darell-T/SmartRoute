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
  assert.match(LEG_SOURCE, /onClick=\{onToggle\}/);
  assert.match(CARD_SOURCE, /<motion\.article[\s\S]*?\blayout\b/);
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
