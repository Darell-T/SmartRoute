import assert from "node:assert/strict";
import test from "node:test";

import { groupAlertThreads, sortAlertFeedItems } from "./alert-feed-threading.ts";

function item(overrides = {}) {
  return {
    id: "a",
    routeIds: ["Q"],
    serviceName: "Broadway",
    title: "Delays near Canal St",
    summary: "Trains delayed near Canal St",
    timestampLabel: "5m",
    severity: "minor",
    lifecycle: "active",
    statusLabel: "Delay",
    source: "mta",
    expandable: false,
    details: { updates: [{ time: "12m", title: "First reported", tone: "muted" }] },
    ...overrides,
  };
}

test("alert threads group the same place and rank live items first", () => {
  const grouped = groupAlertThreads([
    item({ id: "q1" }),
    item({ id: "q2", timestampLabel: "2m", title: "Delays near Canal St" }),
    item({ id: "lonely", routeIds: ["A"], title: "A trains skipping Spring", summary: "Skip-stop" }),
  ]);
  const threaded = grouped.find((row) => row.routeIds.includes("Q"));
  assert.ok(threaded);
  assert.equal(threaded.expandable, true);
  assert.ok((threaded.details?.updates?.length ?? 0) >= 2);

  const sorted = sortAlertFeedItems([
    item({ id: "resolved", lifecycle: "resolved", timestampLabel: "now", severity: "notice" }),
    item({ id: "live", timestampLabel: "live", severity: "incident", lifecycle: "active" }),
    item({ id: "hours", timestampLabel: "2h", severity: "major", lifecycle: "active" }),
    item({ id: "dup", timestampLabel: "5m", details: { updates: [{ time: "5m", title: "First reported", tone: "muted" }, { time: "5m", title: "First reported", tone: "muted" }] } }),
  ]);
  assert.equal(sorted[0].id, "live");
  const groupedDup = groupAlertThreads([
    item({ id: "one", timestampLabel: "5m" }),
    item({ id: "two", timestampLabel: "5m", title: "Delays near Canal St" }),
  ]);
  assert.equal(groupedDup.length, 1);
});
