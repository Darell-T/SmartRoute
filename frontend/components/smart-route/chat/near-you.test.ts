import assert from "node:assert/strict";
import test from "node:test";

import {
  buildArrivalsPayloadForRoute,
  buildHomeNearbyModel,
} from "./near-you";
import { DEMO_RAIL_DATA } from "@/components/smart-route/left-rail/demo-data";
import type {
  Arrival,
  NearbyTransitGroup,
  ServiceAlert,
} from "@/components/smart-route/left-rail/types";

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

function nearbyGroup(
  id: string,
  name: string,
  routeId: string,
  destination: string,
  minutes: number,
): NearbyTransitGroup {
  return {
    id,
    name,
    mode: "subway",
    routeIds: [routeId],
    arrivals: [
      {
        id: `${id}-${routeId}`,
        mode: "subway",
        routeIds: [routeId],
        destination,
        arrivalMinutes: [minutes],
        direction: "downtown",
      },
    ],
  };
}

function serviceAlert(routeId: string): ServiceAlert {
  return {
    sev: "minor",
    kind: "train",
    lines: [routeId],
    title: `${routeId} trains run local after 10 PM`,
    sub: "Active service notice",
    startedAgo: "now",
    lastUpdate: "now",
  };
}

test("home nearby model keeps canonical arrivals and relevant alerts together", () => {
  const result = buildHomeNearbyModel({
    data: {
      ...DEMO_RAIL_DATA,
      nearbyTransitGroups: [
        nearbyGroup("union-d", "Union Sq", "D", "Coney Island", 3),
        nearbyGroup("union-n", "Union Sq", "N", "Astoria-Ditmars", 6),
        nearbyGroup("union-r", "Union Sq", "R", "Bay Ridge", 8),
      ],
      alerts: [serviceAlert("R"), serviceAlert("A")],
    },
    nearestStopName: "14 St-Union Sq",
    nearestRouteIds: ["D", "N", "R"],
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
  });

  assert.equal(result.stationName, "Union Sq");
  assert.equal(result.arrivalsState, "ready");
  assert.deepEqual(
    result.arrivals.map(({ routeId, destination, minutes }) => ({
      routeId,
      destination,
      minutes,
    })),
    [
      { routeId: "D", destination: "Coney Island", minutes: [3] },
      { routeId: "N", destination: "Astoria-Ditmars", minutes: [6] },
      { routeId: "R", destination: "Bay Ridge", minutes: [8] },
    ],
  );
  assert.deepEqual(result.condition, {
    state: "alert",
    label: "R trains run local after 10 PM",
  });
});

test("home nearby model never invents arrivals when the live feed is empty", () => {
  const result = buildHomeNearbyModel({
    data: {
      ...DEMO_RAIL_DATA,
      nearbyTransitGroups: [],
      arrivals: [],
      nearbyBusArrivals: [],
      alerts: [],
    },
    nearestStopName: "14 St-Union Sq",
    nearestRouteIds: ["D", "N", "R"],
    arrivalsLoading: false,
    arrivalsUnavailable: true,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: true,
  });

  assert.equal(result.arrivalsState, "unavailable");
  assert.deepEqual(result.arrivals, []);
  assert.deepEqual(result.condition, {
    state: "unavailable",
    label: "Service status unavailable",
  });
});

test("home nearby model stays in loading until arrivals explicitly fail", () => {
  const result = buildHomeNearbyModel({
    data: {
      ...DEMO_RAIL_DATA,
      nearbyTransitGroups: [],
      arrivals: [],
      nearbyBusArrivals: [],
      alerts: [],
    },
    nearestRouteIds: ["A"],
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: true,
    serviceAlertsUnavailable: false,
  });

  assert.equal(result.arrivalsState, "loading");
  assert.deepEqual(result.condition, {
    state: "loading",
    label: "Checking nearby service status",
  });
});

test("home nearby labels the fallback and keeps an outside location out of the live module", () => {
  const fallback = buildHomeNearbyModel({
    data: DEMO_RAIL_DATA,
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
    locationState: "fallback_nyc",
  });
  assert.equal(fallback.locationLabel, "Starting area");
  assert.equal(fallback.stationName, "34 St–Herald Sq");

  const outside = buildHomeNearbyModel({
    data: DEMO_RAIL_DATA,
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
    locationState: "outside_service_area",
  });
  assert.equal(outside.arrivalsState, "outside_service_area");
  assert.deepEqual(outside.arrivals, []);
  assert.match(outside.locationNotice ?? "", /NYC transit/);
});

test("home nearby model bounds provider alert copy to one concise summary", () => {
  const result = buildHomeNearbyModel({
    data: {
      ...DEMO_RAIL_DATA,
      nearbyTransitGroups: [
        nearbyGroup("union-r", "Union Sq", "R", "Bay Ridge", 8),
      ],
      alerts: [{
        ...serviceAlert("R"),
        title:
          "R trains are running with extensive delays in both directions because of an earlier signal problem near Times Square. Customers should allow additional travel time.",
      }],
    },
    nearestRouteIds: ["R"],
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
  });

  assert.equal(result.condition.state, "alert");
  assert.equal(result.condition.label, "R trains running with delays");
  assert.doesNotMatch(result.condition.label, /signal problem|additional travel/i);
});

test("home nearby model normalizes long directional alerts without raw provider copy", () => {
  const result = buildHomeNearbyModel({
    data: {
      ...DEMO_RAIL_DATA,
      nearbyTransitGroups: [
        nearbyGroup("herald-f", "34 St-Herald Sq", "F", "Coney Island", 0),
      ],
      alerts: [{
        ...serviceAlert("B"),
        lines: ["B", "D"],
        title:
          "Downtown [B][D] trains are running with delays after we moved a train that had its brakes activated at 161 St-Yankee Stadium.",
      }],
    },
    nearestRouteIds: ["B", "D", "F"],
    arrivalsLoading: false,
    arrivalsUnavailable: false,
    serviceAlertsLoading: false,
    serviceAlertsUnavailable: false,
  });

  assert.deepEqual(result.condition, {
    state: "alert",
    label: "Downtown B and D trains running with delays",
  });
});
