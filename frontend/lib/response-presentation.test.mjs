import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_RESPONSE_PRESENTATION_MODE,
  RESPONSE_PRESENTATION_STORAGE_KEY,
  normalizeResponsePresentationMode,
  persistResponsePresentationMode,
  readResponsePresentationMode,
} from "./response-presentation.ts";

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      values.set(key, value);
    },
  };
}

test("response presentation defaults to Auto and rejects unknown values", () => {
  assert.equal(DEFAULT_RESPONSE_PRESENTATION_MODE, "auto");
  assert.equal(normalizeResponsePresentationMode(undefined), "auto");
  assert.equal(normalizeResponsePresentationMode("verbose"), "auto");
  assert.equal(normalizeResponsePresentationMode("quick"), "quick");
});

test("Quick persists for the current browser session", () => {
  const storage = memoryStorage();
  persistResponsePresentationMode(storage, "quick");

  assert.equal(
    storage.getItem(RESPONSE_PRESENTATION_STORAGE_KEY),
    "quick",
  );
  assert.equal(readResponsePresentationMode(storage), "quick");
});

test("blocked session storage falls back safely to Auto", () => {
  const blocked = {
    getItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
  };

  assert.equal(readResponsePresentationMode(blocked), "auto");
  assert.doesNotThrow(() =>
    persistResponsePresentationMode(blocked, "quick"),
  );
});
