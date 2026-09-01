import assert from "node:assert/strict";
import test from "node:test";

import {
  compactAlertTitle,
  compactFeedTitle,
  leadSentences,
  splitSentences,
} from "./alert-feed-copy.ts";

test("splitSentences keeps punctuation without a following space inside one sentence", () => {
  assert.deepEqual(splitSentences("See mta.info. Service restored!"), [
    "See mta.info.",
    "Service restored!",
  ]);
  assert.deepEqual(splitSentences("Use 5 Av. station. Then transfer."), [
    "Use 5 Av. station.",
    "Then transfer.",
  ]);
});

test("leadSentences returns undefined when no sentence is selected", () => {
  assert.equal(leadSentences("One sentence.", 0), undefined);
  assert.equal(leadSentences("   "), undefined);
});

test("compactAlertTitle rejects short token leads and escapes route ids", () => {
  assert.equal(compactAlertTitle("[Q] runs", ["Q"]), "[Q] runs");
  assert.equal(
    compactAlertTitle("A+ trains", ["A+"]),
    "A+ trains",
  );
  assert.equal(compactAlertTitle("[Q] service is delayed"), "Service is delayed");
});

test("compactFeedTitle handles empty and place-free fallback titles", () => {
  assert.equal(compactFeedTitle("", []), "");
  assert.equal(compactFeedTitle("Signal problem", []), "Signal problem");
  assert.equal(compactFeedTitle("Signal problem - ", []), "Signal problem -");
});
