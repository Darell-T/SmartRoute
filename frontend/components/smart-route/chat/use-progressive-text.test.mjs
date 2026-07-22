import assert from "node:assert/strict";
import test from "node:test";

import { nextRevealLength } from "./use-progressive-text.ts";

test("progressive reveal advances one character near the end", () => {
  assert.equal(nextRevealLength(7, 10), 8);
});

test("progressive reveal catches up in larger chunks for bursty responses", () => {
  assert.equal(nextRevealLength(0, 500), 10);
  assert.equal(nextRevealLength(300, 500), 305);
});

test("progressive reveal never overshoots its target", () => {
  assert.equal(nextRevealLength(9, 10), 10);
  assert.equal(nextRevealLength(12, 10), 10);
});
