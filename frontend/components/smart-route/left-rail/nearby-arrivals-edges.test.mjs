import assert from "node:assert/strict";
import test from "node:test";

import { buildLeftRailData } from "./live-data.ts";
import { canonicalDurationMinutes } from "./live-data/route-candidates.ts";
import { detailStepsFromCanonicalItinerary } from "./live-data/route-steps.ts";
import { sevColor } from "./types.ts";

const nowMs = 1_700_000_000_000;

test("severity colors and candidate eta minutes stay typed", () => {
  assert.equal(sevColor("major"), "var(--sr-coral)");
  assert.equal(sevColor("minor"), "var(--sr-amber)");
  assert.equal(sevColor("planned"), "var(--sr-cyan)");
  assert.equal(sevColor("watch"), "var(--sr-muted)");
  assert.equal(canonicalDurationMinutes({ itinerary: { total_duration_seconds: 180 } }), 3);
  assert.equal(canonicalDurationMinutes({ total_minutes: 11 }), 11);
  assert.equal(canonicalDurationMinutes({}), null);
});

test("nearby arrivals classify delay, schedule, compass, and direction-only headsigns", () => {
  const data = buildLeftRailData({
    nowMs,
    liveFeed: {
      nearest_stop: { stop_id: "Q", stop_name: "Canal St", distance_m: 90, route_ids: ["Q"] },
      stops: [
        { stop_id: "Q", stop_name: "Canal St", distance_m: 90, route_ids: ["Q"] },
        { stop_id: "N", stop_name: "Union Sq", distance_m: 90, route_ids: ["N"] },
      ],
      arrivals: [
        {
          route_id: "Q",
          stop_id: "Q01N",
          parent_stop_id: "Q",
          station_name: "Canal St",
          distance_m: 90,
          arrival_time: nowMs / 1000 + 180,
          terminal_stop_name: "Uptown",
          direction: "UPTOWN",
          delay: 650,
        },
        {
          route_id: "N",
          stop_id: "N01S",
          parent_stop_id: "N",
          station_name: "Union Sq",
          distance_m: 90,
          arrival_time: nowMs / 1000 + 180,
          terminal_stop_name: "Coney Island",
          direction: "DOWNTOWN",
          delay: 400,
          prediction_type: "schedule",
        },
        {
          route_id: "B41",
          stop_id: "308214",
          station_name: "Atlantic Av",
          distance_m: 200,
          arrival_time: nowMs / 1000 + 90,
          terminal_stop_name: "Limited to Downtown Brooklyn",
          direction: "SOUTHBOUND",
          mode: "bus",
          stop_compass: "NE",
        },
        {
          route_id: "",
          arrival_time: nowMs / 1000 + 30,
        },
        {
          route_id: "2",
          arrival_time: "bad",
        },
      ],
      alerts: [],
      updated_at: nowMs / 1000,
    },
  });
  assert.ok(data.nearbyTransitGroups.length >= 1);
  assert.ok(data.nearbyBusArrivals.some((row) => row.line === "B41"));
  assert.ok(data.arrivals.some((row) => row.status === "Delayed"));
});

test("canonical itinerary details include dwell and walk fallbacks", () => {
  const details = detailStepsFromCanonicalItinerary(
    [
      { type: "WALK", arrival_stop: "Jay St", segment_index: 0 },
      { type: "SUBWAY", route_id: "A", train_line: "A", departure_stop: "Jay St", arrival_stop: "Fulton", segment_index: 0 },
      { type: "WALK", arrival_stop: "Wall St", segment_index: 1 },
    ],
    {
      itinerary_id: "it",
      total_duration_seconds: 900,
      transfer_count: 0,
      legs: [],
      segments: [
        {
          segment_index: 0,
          destination: { label: "Fulton" },
          legs: [
            { mode: "WALK", walk_seconds: 180 },
            { mode: "SUBWAY", ride_seconds: 420, service_id: "A" },
          ],
        },
        {
          segment_index: 1,
          destination: { label: "Wall St" },
          legs: [{ mode: "WALK", walk_seconds: 120 }],
        },
      ],
      dwell_events: [
        {
          event_type: "dwell",
          after_segment_index: 0,
          waypoint: { label: "Fulton" },
          duration_seconds: 300,
          source: "default",
        },
      ],
    },
  );
  assert.ok(details.some((step) => step.kind === "segment"));
  assert.ok(details.some((step) => step.kind === "dwell"));
});
