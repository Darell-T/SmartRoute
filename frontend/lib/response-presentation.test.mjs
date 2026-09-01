import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_RESPONSE_PRESENTATION_MODE,
  RESPONSE_PRESENTATION_STORAGE_KEY,
  createResponsePresentationModeStore,
  normalizeResponsePresentationMode,
  persistResponsePresentationMode,
  readResponsePresentationMode,
  browserSessionStorage,
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

test("the server snapshot stays Auto while a client can promote stored Quick", () => {
  const storage = memoryStorage({
    [RESPONSE_PRESENTATION_STORAGE_KEY]: "quick",
  });
  const store = createResponsePresentationModeStore(() => storage);

  assert.equal(store.getServerSnapshot(), "auto");
  assert.equal(store.getClientSnapshot(), "quick");
});

test("same-tab mode selection updates subscribers and persists the mode", () => {
  const storage = memoryStorage();
  const store = createResponsePresentationModeStore(() => storage);
  let updates = 0;
  const unsubscribe = store.subscribe(() => {
    updates += 1;
  });

  store.setMode("quick");
  unsubscribe();

  assert.equal(updates, 1);
  assert.equal(store.getClientSnapshot(), "quick");
  assert.equal(storage.getItem(RESPONSE_PRESENTATION_STORAGE_KEY), "quick");
});

test("browser session storage is absent during SSR and when the store throws", () => {
  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  delete globalThis.window;
  try {
    assert.equal(browserSessionStorage(), undefined);
  } finally {
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
  }

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {},
  });
  Object.defineProperty(globalThis.window, "sessionStorage", {
    configurable: true,
    get() {
      throw new Error("blocked");
    },
  });
  try {
    assert.equal(browserSessionStorage(), undefined);
  } finally {
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    else delete globalThis.window;
  }
});

test("persist is a no-op when session storage is unavailable", () => {
  assert.doesNotThrow(() => persistResponsePresentationMode(undefined, "quick"));
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

  const store = createResponsePresentationModeStore(() => blocked);
  assert.equal(store.getServerSnapshot(), "auto");
  assert.equal(store.getClientSnapshot(), "auto");
  assert.doesNotThrow(() => store.setMode("quick"));
  assert.equal(store.getClientSnapshot(), "quick");
});
