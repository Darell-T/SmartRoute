import assert from "node:assert/strict";
import test from "node:test";

import { recommendedCardsForChat } from "./recommended-card-selection.ts";

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
