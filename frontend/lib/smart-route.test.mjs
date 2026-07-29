import assert from "node:assert/strict";
import test from "node:test";

import { formatCanonicalRouteSummary } from "./smart-route.ts";

test("formats only backend-owned duration and arrival fields", () => {
  const summary = formatCanonicalRouteSummary({
    itinerary: { itinerary_id: "it_1" },
    total_minutes: 34,
    arrival_at: "2026-07-16T15:45:00-04:00",
  });
  assert.equal(summary.totalLabel, "34 min");
  assert.equal(summary.arriveLabel, "3:45 PM");
});

test("missing canonical facts remain unavailable", () => {
  assert.equal(formatCanonicalRouteSummary(null), null);
  assert.equal(formatCanonicalRouteSummary({ itinerary: { itinerary_id: "it_1" } }), null);
});
