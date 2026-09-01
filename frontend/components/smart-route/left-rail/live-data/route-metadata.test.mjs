import assert from "node:assert/strict";
import test from "node:test";

import { compareRouteId, normalizeRouteId } from "./route-metadata.ts";

test("normalizeRouteId maps express, shuttle, and railway aliases", () => {
  assert.deepEqual(
    ["6X", "7X", "FX", "FS", "GS", "H", "SIR", "q"].map(normalizeRouteId),
    ["6", "7", "F", "S", "S", "S", "SI", "Q"],
  );
});

test("compareRouteId follows subway order before non-subway routes", () => {
  assert.ok(compareRouteId("Q", "N") > 0);
  assert.ok(compareRouteId("Q", "B63") < 0);
  assert.ok(compareRouteId("B63", "Q") > 0);
});

test("compareRouteId sorts unknown route ids with numeric collation", () => {
  assert.ok(compareRouteId("B2", "B10") < 0);
  assert.deepEqual(["B10", "B2"].sort(compareRouteId), ["B2", "B10"]);
});
