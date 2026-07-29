import assert from "node:assert/strict";
import test from "node:test";

import { buildArrivalsPayloadForRoute } from "./near-you";
import type { Arrival } from "@/components/smart-route/left-rail/types";

function arrival(direction: "uptown" | "downtown", minutes: number[]): Arrival {
  return {
    id: `A-${direction}`,
    mode: "subway",
    routeIds: ["A"],
    line: "A",
    destination: direction === "uptown" ? "Inwood–207 St" : "Far Rockaway",
    arrivalMinutes: minutes,
    direction,
    way: direction,
    dest: direction === "uptown" ? "Inwood–207 St" : "Far Rockaway",
    label: `${minutes[0]} min`,
    mins: minutes[0] ?? 0,
    status: "On Time",
    stale: false,
  };
}

test("nearby-line payload carries sorted arrivals and station directions metadata", () => {
  const result = buildArrivalsPayloadForRoute(
    "a",
    [arrival("uptown", [8, 2, 8]), arrival("downtown", [11, 4])],
    "34 St–Penn Station",
    {
      walkMinutes: 4,
      distanceMiles: 0.2,
      coordinates: { lat: 40.7506, lng: -73.9935 },
    },
  );

  assert.equal(result.routeId, "A");
  assert.equal(result.stationGuidance, "4 min walk · 0.2 mi away");
  assert.deepEqual(result.stationCoordinates, { lat: 40.7506, lng: -73.9935 });
  assert.deepEqual(result.groups, [
    { direction: "uptown", label: "Uptown", minutes: [2, 8] },
    { direction: "downtown", label: "Downtown", minutes: [4, 11] },
  ]);
});

test("nearby-line payload omits arrivals that are already due", () => {
  const result = buildArrivalsPayloadForRoute(
    "A",
    [arrival("uptown", [0, 8, 14])],
    "34 Stâ€“Penn Station",
  );

  assert.deepEqual(result.groups, [
    { direction: "uptown", label: "Uptown", minutes: [8, 14] },
  ]);
});
