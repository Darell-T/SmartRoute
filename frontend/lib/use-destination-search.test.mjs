import assert from "node:assert/strict";
import test from "node:test";

import {
  DestinationRequestGate,
  publishDestinationSearch,
  visibleDestinationSuggestions,
} from "./use-destination-search.ts";

test("a late destination request cannot publish over a newer query", () => {
  const gate = new DestinationRequestGate();
  const first = gate.begin();
  const second = gate.begin();
  const late = publishDestinationSearch({ query: "", suggestions: [] }, gate, first, "old place", [{ id: "old" }]);
  const current = publishDestinationSearch(late, gate, second, "new place", [{ id: "new" }]);
  assert.deepEqual(late.suggestions, []);
  assert.deepEqual(visibleDestinationSuggestions(current, "new place", true), [{ id: "new" }]);
});

test("disabling destination search invalidates pending results and hides prior suggestions", () => {
  const gate = new DestinationRequestGate();
  const active = gate.begin();
  gate.begin();
  const late = publishDestinationSearch({ query: "", suggestions: [] }, gate, active, "museum", [{ id: "museum" }]);
  assert.deepEqual(late.suggestions, []);
  assert.deepEqual(visibleDestinationSuggestions(late, "museum", false), []);
});
