import assert from "node:assert/strict";
import test from "node:test";

import type { NearbyTransitIssue } from "@/types/api";
import { selectHomeNearbyIssue } from "./nearby-issue";

const NOW = Date.parse("2026-07-30T16:00:00Z");

function issue(
  overrides: Partial<NearbyTransitIssue> = {},
): NearbyTransitIssue {
  return {
    id: "q-stall",
    route_ids: ["Q"],
    station_id: "D38",
    station_name: "Prospect Park",
    stops_away: 2,
    confidence: "confirmed",
    status: "stalled",
    summary: "Q train stalled near Prospect Park · 2 stops from Church Av",
    source_types: ["mta_service_alert"],
    observed_at: "2026-07-30T15:58:00Z",
    relevance: "nearby_line",
    ...overrides,
  };
}

function select(
  issues: NearbyTransitIssue[],
  overrides: {
    nearbyRouteIds?: string[];
    hasPlannedRoute?: boolean;
  } = {},
) {
  return selectHomeNearbyIssue({
    issues,
    nearbyRouteIds: overrides.nearbyRouteIds ?? ["Q"],
    hasPlannedRoute: overrides.hasPlannedRoute ?? false,
    nowMs: NOW,
  });
}

test("confirmed nearby stall keeps direct canonical wording", () => {
  assert.equal(
    select([issue()])?.label,
    "Q train stalled near Prospect Park · 2 stops from Church Av",
  );
});

test("strong inference is always qualified", () => {
  assert.equal(
    select([
      issue({
        confidence: "strong_inference",
        summary: "Q train stalled near Prospect Park",
        source_types: ["repeated_vehicle_telemetry"],
      }),
    ])?.label,
    "Possible q train stalled near prospect park",
  );
});

test("weak, stale, unrelated, and distant canonical issues are suppressed", () => {
  const weak = issue({
    confidence: "weak" as NearbyTransitIssue["confidence"],
  });
  assert.equal(select([weak]), null);
  assert.equal(
    select([issue({ observed_at: "2026-07-30T15:30:00Z" })]),
    null,
  );
  assert.equal(select([issue()], { nearbyRouteIds: ["A"] }), null);
});

test("planned-route issue is shown after planning even off the nearby line", () => {
  assert.ok(
    select(
      [issue({ route_ids: ["A"], relevance: "planned_route" })],
      { nearbyRouteIds: ["Q"], hasPlannedRoute: true },
    ),
  );
});

test("only the highest-priority eligible issue is returned", () => {
  const result = select([
    issue({
      id: "inferred",
      confidence: "strong_inference",
      summary: "Delay may be developing near 7 Av",
    }),
    issue({ id: "confirmed" }),
  ]);
  assert.equal(result?.id, "confirmed");
});
