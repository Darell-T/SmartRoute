import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  DestinationSuggestions,
  destinationSuggestionOptionId,
} from "./destination-suggestions.tsx";
import {
  DestinationInput,
  cleanDestinationSubmit,
  destinationComboboxCommand,
} from "./route-view/actions.tsx";

const jay = {
  id: "jay",
  label: "Jay St-MetroTech, Brooklyn, NY",
  address: "Jay St-MetroTech, Brooklyn, NY",
  coordinates: { lat: 40.692, lng: -73.987 },
};

test("destination suggestion option ids stay stable for aria-activedescendant", () => {
  assert.equal(destinationSuggestionOptionId("sr-dest", 2), "sr-dest-option-2");
});

test("open destination listbox renders primary and secondary labels", () => {
  const html = renderToStaticMarkup(
    createElement(DestinationSuggestions, {
      id: "sr-dest",
      open: true,
      suggestions: [jay],
      highlightedIndex: 0,
      onHighlight() {},
      onSelect() {},
    }),
  );
  assert.match(html, /role="listbox"/);
  assert.match(html, /aria-label="Suggested destinations"/);
  assert.match(html, /Jay St-MetroTech/);
  assert.match(html, /Brooklyn, NY/);
  assert.match(html, /aria-selected="true"/);
  const closed = renderToStaticMarkup(
    createElement(DestinationSuggestions, {
      id: "sr-dest",
      open: false,
      suggestions: [jay],
      highlightedIndex: 0,
      onHighlight() {},
      onSelect() {},
    }),
  );
  assert.doesNotMatch(closed, /role="listbox"/);
});

test("destination combobox commands wrap and escape", () => {
  assert.deepEqual(destinationComboboxCommand("ArrowDown", 0, 0), { type: "none" });
  assert.deepEqual(destinationComboboxCommand("ArrowDown", 3, 2), { type: "highlight", index: 0 });
  assert.deepEqual(destinationComboboxCommand("ArrowUp", 3, 0), { type: "highlight", index: 2 });
  assert.deepEqual(destinationComboboxCommand("Enter", 3, 1), { type: "choose", index: 1 });
  assert.deepEqual(destinationComboboxCommand("Escape", 3, 1), { type: "escape" });
  assert.deepEqual(destinationComboboxCommand("Tab", 3, 1), { type: "none" });
  assert.equal(cleanDestinationSubmit("  JFK  "), "JFK");
});

test("destination input covers empty, submit, stop, and clear action states", () => {
  const search = {
    inputValue: "",
    isLoading: false,
    planningPhase: "idle",
    hasActiveRoute: false,
    onInputChange() {},
    onSubmit() {},
    onCancelPlanning() {},
    onClear() {},
  };
  const empty = renderToStaticMarkup(
    createElement(DestinationInput, { search, onDemoSubmit() {} }),
  );
  assert.match(empty, /aria-label="Search destination or address"/);
  assert.match(empty, /data-action-state="empty"/);
  const filled = renderToStaticMarkup(
    createElement(DestinationInput, {
      search: { ...search, inputValue: "JFK" },
      onDemoSubmit() {},
    }),
  );
  assert.match(filled, /data-action-state="submit"/);
  assert.match(filled, /aria-label="Search route"/);
  const stopping = renderToStaticMarkup(
    createElement(DestinationInput, {
      search: { ...search, inputValue: "JFK", planningPhase: "cancellable" },
      onDemoSubmit() {},
    }),
  );
  assert.match(stopping, /data-action-state="stop"/);
  const clearing = renderToStaticMarkup(
    createElement(DestinationInput, {
      search: { ...search, hasActiveRoute: true },
      onDemoSubmit() {},
    }),
  );
  assert.match(clearing, /data-action-state="clear"/);
  const demo = renderToStaticMarkup(
    createElement(DestinationInput, { onDemoSubmit() {} }),
  );
  assert.match(demo, /Search destination or address/);
});
