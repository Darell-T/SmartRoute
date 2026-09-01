import assert from "node:assert/strict";
import test from "node:test";

import { destinationComboboxCommand } from "./route-view-actions.tsx";

test("destination combobox arrows wrap and Enter chooses the highlighted suggestion", () => {
  assert.deepEqual(destinationComboboxCommand("ArrowDown", 3, 2), {
    type: "highlight",
    index: 0,
  });
  assert.deepEqual(destinationComboboxCommand("ArrowUp", 3, 0), {
    type: "highlight",
    index: 2,
  });
  assert.deepEqual(destinationComboboxCommand("Enter", 3, 1), {
    type: "choose",
    index: 1,
  });
  assert.deepEqual(destinationComboboxCommand("Escape", 3, 1), { type: "escape" });
  assert.deepEqual(destinationComboboxCommand("ArrowDown", 0, 0), { type: "none" });
  assert.deepEqual(destinationComboboxCommand("Tab", 3, 0), { type: "none" });
});
