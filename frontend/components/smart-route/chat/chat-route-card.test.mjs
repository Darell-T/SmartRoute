import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { recommendedCardsForChat } from "./recommended-card-selection.ts";

const CARD_SOURCE = fs.readFileSync(
  fileURLToPath(new URL("./recommended-itinerary-card.tsx", import.meta.url)),
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
  assert.match(CARD_SOURCE, /aria-expanded=\{expanded\}/);
  assert.match(CARD_SOURCE, /onClick=\{onToggle\}/);
  assert.match(CARD_SOURCE, /<motion\.article[\s\S]*?\blayout\b/);
});

test("recommendation card preserves total duration and route-colored chains", () => {
  assert.match(CARD_SOURCE, /model\.durationLabel/);
  assert.match(CARD_SOURCE, /model\.metaParts\.map/);
  assert.match(CARD_SOURCE, /getRouteColor\(event\.routeIds\[0\]/);
  assert.match(CARD_SOURCE, /duration: 0\.3, ease: LAYOUT_EASE/);
});

test("Open on map remains a direct keyboard-accessible action", () => {
  assert.match(CARD_SOURCE, /<motion\.button[\s\S]*?type="button"/);
  assert.match(CARD_SOURCE, /aria-label=\{model\.primaryActionLabel\}/);
  assert.match(CARD_SOURCE, /disabled=\{!onPrimaryAction\}/);
  assert.match(CARD_SOURCE, /onClick=\{onPrimaryAction\}/);
  assert.doesNotMatch(CARD_SOURCE, /onClick=\{\(\) => onPrimaryAction/);
});
