import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeAlertRoutes,
  serviceNameForRoutes,
} from "./line-identities.ts";

test("serviceNameForRoutes handles empty, shared, multiple, bus, and unknown routes", () => {
  assert.equal(serviceNameForRoutes([]), undefined);
  assert.equal(serviceNameForRoutes(["J", "Z"]), "Nassau St Line");
  assert.equal(serviceNameForRoutes(["A", "Q"]), "Multiple lines");
  assert.equal(serviceNameForRoutes(["M15"]), "M15 bus");
  assert.equal(serviceNameForRoutes(["ZZ"]), "ZZ service");
});

test("normalizeAlertRoutes removes empty aliases and sorts subway, unknown, and bus routes", () => {
  assert.deepEqual(
    normalizeAlertRoutes(["m15", "ZZ", "Q", null, " A ", "", "M15", undefined]),
    ["A", "Q", "ZZ", "M15"],
  );
  assert.deepEqual(normalizeAlertRoutes(["M2", "B44", "AA"]), ["AA", "B44", "M2"]);
  assert.deepEqual(normalizeAlertRoutes(["7X", "7", "6X", "6"]), ["6", "7", "6X", "7X"]);
});
