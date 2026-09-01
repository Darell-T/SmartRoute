import assert from "node:assert/strict";
import test from "node:test";

import { buildHomeNearbyModel } from "./near-you.ts";

function liveData(overrides = {}) {
  return {
    station: { name: "Union Sq", walk: "2 min walk", dist: "80 m", updatedSec: 1 },
    health: {
      status: "clear",
      alerts: 0,
      lines: 0,
      major: 0,
      stale: 0,
      summary: "",
      affected: [],
    },
    arrivals: [],
    nearbyTransitGroups: [],
    nearbyBusArrivals: [],
    plan: {
      headline: "",
      rationale: "",
      eta: "",
      totalTime: "",
      pickedLine: "",
      steps: [],
      alternatives: [],
      notes: [],
    },
    feed: [],
    lineState: {},
    alerts: [],
    ...overrides,
  };
}

function group(id, routeId, destination, minutes, extra = {}) {
  return {
    id,
    name: "Union Sq",
    mode: "subway",
    routeIds: routeId ? [routeId] : [],
    arrivals: [
      {
        id: `${id}-row`,
        mode: "subway",
        routeIds: routeId ? [routeId] : [],
        destination,
        arrivalMinutes: minutes,
        direction: extra.direction ?? "downtown",
        ...extra,
      },
    ],
  };
}

function alert(overrides = {}) {
  return {
    sev: "minor",
    kind: "train",
    lines: ["R"],
    title: "R trains run local after 10 PM",
    sub: "Active service notice",
    startedAgo: "now",
    lastUpdate: "now",
    ...overrides,
  };
}

const longDelay =
  "Trains are running with extensive delays in both directions after an earlier incident and customers should allow extra time.";
const longSuspend =
  "There are no trains and service is suspended through this area while crews work on the tracks nearby for the rest of the evening.";
const longSkip =
  "Trains are skipping and bypassing several nearby stops after a medical emergency at an earlier station along the line.";
const longReroute =
  "Trains are rerouted around the disruption and customers should expect a longer trip through this part of the line tonight.";
const longPlanned =
  "Weekend construction will change regular service through this corridor and nearby stations will use a different stopping pattern.";
const longGeneric =
  "Customers should allow additional travel time because of a service change affecting this corridor for the rest of the night.";

test("home nearby skips empty routes, empty minutes, and duplicate arrival keys", () => {
  const result = buildHomeNearbyModel({
    data: liveData({
      nearbyTransitGroups: [
        group("blank", "", "Coney Island", [3]),
        group("neg", "D", "Coney Island", [-2]),
        group("empty-mins", "N", "Astoria", []),
        group("first", "R", "Bay Ridge", [4]),
        group("dup", "R", "Bay Ridge", [9]),
        group("next", "Q", "96 St", [6]),
      ],
    }),
    nearestRouteIds: ["R", "Q"],
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
  });
  assert.deepEqual(
    result.arrivals.map(({ routeId, destination, minutes }) => ({ routeId, destination, minutes })),
    [
      { routeId: "R", destination: "Bay Ridge", minutes: [4] },
      { routeId: "Q", destination: "96 St", minutes: [6] },
    ],
  );
});

test("long alerts list three lines and fall back to subtitle when the title is empty", () => {
  const three = buildHomeNearbyModel({
    data: liveData({
      nearbyTransitGroups: [group("r", "R", "Bay Ridge", [8])],
      alerts: [alert({ lines: ["B", "D", "F"], title: longDelay })],
    }),
    nearestRouteIds: ["B", "D", "F"],
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
  });
  assert.equal(three.condition.label, "B, D, and F trains running with delays");

  const fromSub = buildHomeNearbyModel({
    data: liveData({
      nearbyTransitGroups: [group("r", "R", "Bay Ridge", [8])],
      alerts: [alert({ title: "", sub: longDelay })],
    }),
    nearestRouteIds: ["R"],
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
  });
  assert.equal(fromSub.condition.label, "R trains running with delays");
});

test("long alerts without a direction or line stay unprefixed", () => {
  const result = buildHomeNearbyModel({
    data: liveData({
      nearbyTransitGroups: [group("r", "R", "Bay Ridge", [8])],
      alerts: [alert({ lines: [""], title: longDelay })],
    }),
    nearestRouteIds: ["", "R"],
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
  });
  assert.equal(result.condition.label, "trains running with delays");
});

test("long alerts compress suspend, skip, reroute, planned, and generic copy", () => {
  const model = (alertOverrides) =>
    buildHomeNearbyModel({
      data: liveData({
        nearbyTransitGroups: [group("r", "R", "Bay Ridge", [8])],
        alerts: [alert(alertOverrides)],
      }),
      nearestRouteIds: ["R"],
      arrivalsLoading: false,
      arrivalsUnavailable: false,
      serviceAlertsLoading: false,
      serviceAlertsUnavailable: false,
    });

  assert.equal(model({ title: longSuspend }).condition.label, "R service suspended nearby");
  assert.equal(model({ title: longSkip }).condition.label, "R trains skipping nearby stops");
  assert.equal(model({ title: longReroute }).condition.label, "R trains rerouted nearby");
  assert.equal(
    model({ title: longPlanned, sev: "planned" }).condition.label,
    "R planned service change nearby",
  );
  assert.equal(model({ title: longGeneric }).condition.label, "R service change nearby");
});

test("pending location copy and a ready station stay distinct from loading arrivals", () => {
  const pending = buildHomeNearbyModel({
    data: liveData(),
    nearestStopName: "  Jay St  ",
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
    locationState: "pending",
  });
  assert.equal(pending.stationName, "Locating you…");
  assert.equal(pending.locationLabel, "Near you");

  const named = buildHomeNearbyModel({
    data: liveData(),
    nearestStopName: "  Jay St  ",
    arrivalsLoading: true,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
  });
  assert.equal(named.stationName, "Jay St");
  assert.equal(named.arrivalsState, "loading");
});
