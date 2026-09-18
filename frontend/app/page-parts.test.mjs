import assert from "node:assert/strict";
import test from "node:test";

import {
  destinationCoordinatesFromRoute,
  nearbyStationSelection,
  routeCardsForSelection,
  routeRailStatus,
  shellPanelProps,
  toggleElementFullscreen,
  transitRouteData,
} from "./page-parts.tsx";

test("route rail status follows loading, error, result, then standby", () => {
  assert.equal(routeRailStatus(true, "ignored", true), "thinking");
  assert.equal(routeRailStatus(false, "Failed to plan trip", false), "error");
  assert.equal(routeRailStatus(false, null, true), "result");
  assert.equal(routeRailStatus(false, null, false), "standby");
});

test("destination coordinates prefer the last walk end point over the selected pin", () => {
  const coords = destinationCoordinatesFromRoute(
    [
      { type: "SUBWAY", arrival_coords: { latitude: 40.7, longitude: -73.9 } },
      { type: "WALK", end_point: { latitude: 40.64, longitude: -73.78 } },
    ],
    { lat: 40.75, lng: -73.99 },
  );
  assert.deepEqual(coords, { lat: 40.64, lng: -73.78 });
});

test("destination coordinates fall back to the selected pin when steps have no geometry", () => {
  assert.deepEqual(
    destinationCoordinatesFromRoute([{ type: "WALK" }], { lat: 40.75, lng: -73.99 }),
    { lat: 40.75, lng: -73.99 },
  );
});

test("route card selection uses the producing assistant turn's full card set", () => {
  const first = {
    card_id: "rc_1",
    turn_id: "t1",
    role: "recommended",
    origin: { label: "A", lat: 40.7, lng: -73.9 },
    destination: { label: "B", lat: 40.6, lng: -73.8 },
    summary: { eta_minutes: 20, transfers: 0, lines: ["Q"], reason: "Direct" },
    route: [],
    alerts: [],
  };
  const second = { ...first, card_id: "rc_2", role: "alternative" };
  const cards = routeCardsForSelection(
    [
      { role: "user", text: "to B" },
      {
        role: "assistant",
        turnId: "t1",
        text: "here",
        reasoning: "",
        toolChips: [],
        routeCards: [first, second],
        isStreaming: false,
      },
    ],
    "rc_2",
    [second],
  );
  assert.deepEqual(
    cards.map((card) => card.card_id),
    ["rc_1", "rc_2"],
  );
});

test("nearby station selection requires coordinates before a map search is created", () => {
  assert.equal(
    nearbyStationSelection({
      routeId: "Q",
      stationName: "Newkirk Plaza",
      groups: [],
    }),
    null,
  );
  assert.deepEqual(
    nearbyStationSelection({
      routeId: "Q",
      stationName: "Newkirk Plaza",
      stationCoordinates: { lat: 40.635, lng: -73.962 },
      groups: [],
    }),
    {
      label: "Newkirk Plaza station",
      coordinates: { lat: 40.635, lng: -73.962 },
    },
  );
});

test("toggleElementFullscreen exits when a fullscreen element is already open", async () => {
  let exited = 0;
  await toggleElementFullscreen(
    { requestFullscreen: async () => { throw new Error("should not enter"); } },
    { element: {}, exit: async () => { exited += 1; } },
    () => { throw new Error("should not fall back"); },
  );
  assert.equal(exited, 1);
});

test("toggleElementFullscreen recents the map when the browser rejects fullscreen", async () => {
  let recentered = 0;
  await toggleElementFullscreen(
    { requestFullscreen: async () => { throw new Error("denied"); } },
    { element: null, exit: async () => undefined },
    () => { recentered += 1; },
  );
  assert.equal(recentered, 1);
});

test("toggleElementFullscreen is a no-op without a target and enters when none is open", async () => {
  await toggleElementFullscreen(null, { element: null, exit: async () => undefined }, () => {
    throw new Error("should not fall back");
  });
  let entered = 0;
  await toggleElementFullscreen(
    {
      requestFullscreen: async () => {
        entered += 1;
      },
    },
    { element: null, exit: async () => undefined },
    () => {
      throw new Error("should not fall back");
    },
  );
  assert.equal(entered, 1);
});

test("destination coordinates use subway arrival coords when the last step is not a walk", () => {
  assert.deepEqual(
    destinationCoordinatesFromRoute(
      [{ type: "SUBWAY", arrival_coords: { latitude: 40.64, longitude: -73.78 } }],
      null,
    ),
    { lat: 40.64, lng: -73.78 },
  );
});

test("route card selection falls back when no assistant turn owns the card", () => {
  const fallback = [{ card_id: "rc_fallback" }];
  assert.equal(
    routeCardsForSelection([{ role: "user", text: "to B" }], "rc_missing", fallback),
    fallback,
  );
});

test("shell panel props hide inactive tabs and expose active ones", () => {
  assert.deepEqual(shellPanelProps(true), { hiddenClass: "", inert: undefined });
  assert.deepEqual(shellPanelProps(false), {
    hiddenClass: " sr-tab-shell__panel--hidden",
    inert: true,
  });
});

test("transit route data is omitted until at least one step exists", () => {
  assert.equal(transitRouteData([], { itinerary_id: "it" }), null);
  assert.deepEqual(
    transitRouteData([{ type: "WALK" }], { itinerary_id: "it" }),
    { steps: [{ type: "WALK" }], itinerary: { itinerary_id: "it" } },
  );
});
