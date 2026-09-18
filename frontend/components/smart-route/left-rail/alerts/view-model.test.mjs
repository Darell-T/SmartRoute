import assert from "node:assert/strict";
import test from "node:test";

import {
  groupAlertItemsByLine,
  isSystemwideAlert,
  partitionAlertItems,
} from "./view-model.ts";

function item(id, routeIds, overrides = {}) {
  return {
    id,
    routeIds,
    serviceName: "Service",
    title: `${id} alert`,
    timestampLabel: "now",
    severity: "notice",
    lifecycle: "active",
    statusLabel: "",
    ...overrides,
  };
}

test("partitionAlertItems selects nearby alerts and leaves systemwide alerts in rest", () => {
  const nearby = item("nearby", ["A"]);
  const elsewhere = item("elsewhere", ["Q"]);
  const systemwide = item("systemwide", ["1", "2", "3", "4", "5", "6", "7", "A"]);
  const partition = partitionAlertItems(
    [elsewhere, systemwide, nearby],
    ["A"],
    1,
  );

  assert.deepEqual(partition.featured.map(({ id }) => id), ["nearby"]);
  assert.deepEqual(partition.rest.map(({ id }) => id), ["elsewhere", "systemwide"]);
  assert.equal(isSystemwideAlert(systemwide), true);
  assert.equal(isSystemwideAlert(nearby), false);
});

test("groupAlertItemsByLine groups families and merges route ids", () => {
  const groups = groupAlertItemsByLine([
    item("c-alert", ["C"]),
    item("a-alert", ["A"]),
    item("bus-alert", ["M15"]),
    item("bus-repeat", ["M15", "M15"]),
  ]);

  assert.equal(groups[0].id, "8-avenue");
  assert.deepEqual(groups[0].routeIds, ["A", "C", "E"]);
  assert.deepEqual(groups[0].items.map(({ id }) => id), ["c-alert", "a-alert"]);
  assert.equal(groups[1].id, "bus-M15");
  assert.equal(groups[1].items.length, 2);
});

test("groupAlertItemsByLine covers systemwide, mixed subway, and unknown routes", () => {
  const groups = groupAlertItemsByLine([
    item("system", []),
    item("mixed", ["Q", "M15"]),
    item("two-families", ["A", "Q"]),
    item("unknown", ["ZZ", "AA"]),
  ]);

  assert.equal(groups.find(({ id }) => id === "systemwide")?.name, "Systemwide");
  assert.deepEqual(
    groups.find(({ id }) => id === "multiple-lines")?.items.map(({ id }) => id),
    ["mixed", "two-families"],
  );
  assert.equal(groups.find(({ id }) => id === "bus-AA")?.name, "AA bus");
});

test("groupAlertItemsByLine sorts subway and unknown routes deterministically", () => {
  const [multiple] = groupAlertItemsByLine([
    item("routes", ["M15", "Q", "A", "ZZ", "A"]),
  ]);

  assert.equal(multiple.id, "multiple-lines");
  assert.deepEqual(multiple.routeIds, ["A", "Q", "M15", "ZZ"]);
});
