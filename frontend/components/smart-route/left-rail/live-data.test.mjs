import assert from "node:assert/strict";
import test from "node:test";

import {
  buildLeftRailData,
  HALF_MILE_METERS,
} from "./live-data.ts";

test("buildLeftRailData maps real half-mile arrivals into left rail rows", () => {
  const nowMs = 1_700_000_000_000;
  const liveFeed = {
    nearest_stop: {
      stop_id: "A",
      stop_name: "Near Station",
      distance_m: 120,
      route_ids: ["A"],
    },
    stops: [
      {
        stop_id: "A",
        stop_name: "Near Station",
        distance_m: 120,
        route_ids: ["A"],
      },
      {
        stop_id: "B",
        stop_name: "Second Station",
        distance_m: 780,
        route_ids: ["C"],
      },
      {
        stop_id: "C",
        stop_name: "Too Far Station",
        distance_m: HALF_MILE_METERS + 20,
        route_ids: ["D"],
      },
    ],
    arrivals: [
      {
        route_id: "A",
        stop_id: "A01N",
        parent_stop_id: "A",
        parent_stop_name: "Near Station",
        station_name: "Near Station",
        distance_m: 120,
        arrival_time: 1_700_000_120,
        terminal_stop_name: "Inwood-207 St",
        direction: "UPTOWN",
      },
      {
        route_id: "C",
        stop_id: "B01S",
        parent_stop_id: "B",
        parent_stop_name: "Second Station",
        station_name: "Second Station",
        distance_m: 780,
        arrival_time: 1_700_000_300,
        terminal_stop_name: "Euclid Av",
        direction: "DOWNTOWN",
      },
      {
        route_id: "B44",
        stop_id: "308214",
        parent_stop_name: "Nostrand Av/Eastern Pkwy",
        station_name: "Nostrand Av/Eastern Pkwy",
        distance_m: 240,
        arrival_time: 1_700_000_060,
        terminal_stop_name: "Sheepshead Bay",
        direction: "SOUTHBOUND",
        mode: "bus",
      },
      {
        route_id: "D",
        stop_id: "C01N",
        parent_stop_id: "C",
        parent_stop_name: "Too Far Station",
        station_name: "Too Far Station",
        distance_m: HALF_MILE_METERS + 20,
        arrival_time: 1_700_000_420,
        terminal_stop_name: "Norwood-205 St",
        direction: "UPTOWN",
      },
    ],
    alerts: [],
    updated_at: 1_700_000_000,
  };

  const data = buildLeftRailData({ liveFeed, nowMs });

  assert.equal(data.station.name, "Nearby transit");
  assert.equal(data.station.walk, "1 min walk");
  assert.equal(data.station.dist, "120 m");
  assert.deepEqual(
    data.arrivals.map((arrival) => arrival.line),
    ["B44", "A", "C"],
  );
  assert.equal(data.arrivals[0].mode, "bus");
  assert.equal(data.arrivals[0].stationName, "Nostrand Av/Eastern Pkwy");
  assert.equal(data.arrivals[1].stationName, "Near Station");
  assert.equal(data.arrivals[1].label, "2 min");
  assert.equal(data.arrivals[2].stationName, "Second Station");
  assert.equal(data.arrivals[2].way, "downtown");
});

test("buildLeftRailData uses alert stop names instead of stop ids", () => {
  const data = buildLeftRailData({
    nowMs: 1_700_000_000_000,
    liveFeed: {
      nearest_stop: null,
      stops: [],
      arrivals: [],
      alerts: [
        {
          header: "Delays",
          description: "Signal work",
          route_ids: ["A"],
          stop_ids: ["A32"],
          stop_names: ["Times Sq-42 St"],
          start: 1_699_999_700,
        },
      ],
      updated_at: 1_700_000_000,
    },
  });

  assert.deepEqual(data.alerts[0].affectedStops, ["Times Sq-42 St"]);
});

test("buildLeftRailData collapses repeated ETAs into one row per service direction", () => {
  const nowMs = 1_700_000_000_000;
  const data = buildLeftRailData({
    nowMs,
    liveFeed: {
      nearest_stop: {
        stop_id: "D18",
        stop_name: "Church Av",
        distance_m: 123,
        route_ids: ["B", "Q"],
      },
      stops: [],
      arrivals: [
        {
          route_id: "Q",
          stop_id: "D18N",
          station_name: "Church Av",
          parent_stop_name: "Church Av",
          distance_m: 123,
          arrival_time: 1_700_000_000,
          terminal_stop_name: "96 St",
          direction: "UPTOWN",
        },
        {
          route_id: "Q",
          stop_id: "D18N",
          station_name: "Church Av",
          parent_stop_name: "Church Av",
          distance_m: 123,
          arrival_time: 1_700_000_420,
          terminal_stop_name: "96 St",
          direction: "UPTOWN",
        },
        {
          route_id: "Q",
          stop_id: "D17N",
          station_name: "Parkside Av",
          parent_stop_name: "Parkside Av",
          distance_m: 510,
          arrival_time: 1_700_000_060,
          terminal_stop_name: "96 St",
          direction: "UPTOWN",
        },
        {
          route_id: "B",
          stop_id: "D18N",
          station_name: "Church Av",
          parent_stop_name: "Church Av",
          distance_m: 123,
          arrival_time: 1_700_000_120,
          terminal_stop_name: "Bedford Park Blvd",
          direction: "UPTOWN",
        },
      ],
      alerts: [],
      updated_at: 1_700_000_000,
    },
  });

  assert.deepEqual(
    data.arrivals.map((arrival) => `${arrival.line}:${arrival.stationName}`),
    ["Q:Church Av", "B:Church Av"],
  );
  assert.deepEqual(
    data.arrivals[0].nextArrivals?.map((arrival) => arrival.label),
    ["Now", "7 min"],
  );
});

test("bus arrivals keep one row per route + destination, not merged across directions", () => {
  const nowMs = 1_700_000_000_000;
  const liveFeed = {
    arrivals: [
      {
        route_id: "M34",
        mode: "bus",
        arrival_time: nowMs / 1000 + 180,
        direction: "0",
        terminal_stop_name: "EAST SIDE FERRY",
        station_name: "W 34 ST/7 AV",
        distance_m: 120,
        stop_id: "401234",
      },
      {
        route_id: "M34",
        mode: "bus",
        arrival_time: nowMs / 1000 + 300,
        direction: "1",
        terminal_stop_name: "JAVITS CENTER",
        station_name: "W 34 ST/7 AV",
        distance_m: 120,
        stop_id: "401235",
      },
      {
        route_id: "A",
        arrival_time: nowMs / 1000 + 240,
        direction: "North",
        terminal_stop_name: "Inwood-207 St",
        station_name: "34 St-Penn",
        distance_m: 200,
        stop_id: "A28N",
      },
    ],
  };

  const data = buildLeftRailData({ liveFeed, nowMs });
  const busRows = data.arrivals.filter((arrival) => arrival.mode === "bus");
  const dests = busRows.map((arrival) => arrival.dest).sort();

  assert.equal(busRows.length, 2, "two bus directions = two rows");
  assert.deepEqual(dests, ["EAST SIDE FERRY", "JAVITS CENTER"]);
  assert.equal(data.arrivals.some((arrival) => arrival.mode !== "bus"), true);
});

test("consecutive walk legs collapse, final step becomes Arrive", () => {
  const nowMs = 1_700_000_000_000;
  const routeSteps = [
    { type: "BUS", route_id: "B35", train_line: "B35", arrival_stop: "Church Av/E 18 St", minutes_until_arrival: 20 },
    { type: "WALK", minutes_until_arrival: 3 },
    { type: "WALK", arrival_stop: "Maimonides Medical Center", minutes_until_arrival: 5 },
  ];
  const candidate = { id: "c0", index: 0, steps: routeSteps, is_recommended: true, recommendation_reason: "Fastest." };
  const data = buildLeftRailData({
    nowMs,
    routeSteps,
    routeCandidates: [candidate],
    activeRouteCandidate: candidate,
  });
  // Without the merge this would be 3 rows (board + walk + walk); the merge
  // folds the two walks, then the final step is relabeled Arrive.
  assert.equal(data.plan.steps.length, 2, "bus board + merged final step");
  const last = data.plan.steps[data.plan.steps.length - 1];
  assert.equal(last.type, "arrive");
  assert.equal(last.action, "Arrive");
  assert.equal(last.title, "Maimonides Medical Center", "names the destination");
  assert.equal(data.plan.steps.filter((step) => step.action === "Walk").length, 0, "no leftover walk row");
});

test("route candidates become clickable alternatives; active one excluded", () => {
  const nowMs = 1_700_000_000_000;
  const mkSteps = (line, minutes) => [
    { type: "WALK", minutes_until_arrival: 2 },
    {
      type: "SUBWAY",
      route_id: line,
      train_line: line,
      departure_stop: "A St",
      arrival_stop: "B St",
      minutes_until_arrival: minutes,
    },
  ];
  const candidates = [
    {
      id: "candidate-0",
      index: 0,
      steps: mkSteps("Q", 20),
      is_recommended: true,
      recommendation_reason: "Fastest with no alerts.",
    },
    {
      id: "candidate-1",
      index: 1,
      steps: mkSteps("B", 29),
      is_recommended: false,
      rejection_reason: "Signal problems at DeKalb.",
    },
    {
      id: "candidate-2",
      index: 2,
      steps: mkSteps("D", 24),
      is_recommended: false,
      rejection_reason: "One extra transfer.",
    },
  ];

  const data = buildLeftRailData({
    nowMs,
    routeSteps: candidates[0].steps,
    routeCandidates: candidates,
    activeRouteCandidate: candidates[0],
  });

  const alts = data.plan.alternatives;
  assert.equal(alts.length, 2, "active candidate excluded");
  assert.deepEqual(alts.map((a) => a.id), ["candidate-1", "candidate-2"]);
  assert.equal(alts[0].line, "B");
  assert.equal(alts[0].delta, "+9 min");
  assert.equal(alts[0].sev, "high", ">=8 min slower reads high");
  assert.equal(alts[0].reason, "Signal problems at DeKalb.");
  assert.equal(alts[0].status, "rejected");
  assert.equal(alts[1].delta, "+4 min");
  assert.equal(alts[1].sev, "medium");
});

test("plan rationale shows what ATLAS says (the narration) for the picked route", () => {
  const nowMs = 1_700_000_000_000;
  const steps = [
    { type: "SUBWAY", route_id: "Q", train_line: "Q", departure_stop: "Church Av", arrival_stop: "Avenue M", minutes_until_arrival: 20 },
  ];
  const candidate = { id: "c0", index: 0, steps, is_recommended: true, recommendation_reason: "Fastest option." };
  const data = buildLeftRailData({
    nowMs,
    routeSteps: steps,
    routeCandidates: [candidate],
    activeRouteCandidate: candidate,
    recommendationText: "Very well, sir. Take the Q. You should arrive in roughly 20 minutes.",
    routeEta: "12:58 PM",
    routeTotalTime: "36 min",
  });
  assert.equal(
    data.plan.rationale,
    "Very well, sir. Take the Q. You should arrive in roughly 20 minutes.",
    "the spoken narration leads the card",
  );
  // Real ETA replaces the "Live · Calculated" placeholder.
  assert.equal(data.plan.eta, "12:58 PM");
  assert.equal(data.plan.totalTime, "36 min");

  // Without narration it falls back to the candidate's reason.
  const fallback = buildLeftRailData({
    nowMs,
    routeSteps: steps,
    routeCandidates: [candidate],
    activeRouteCandidate: candidate,
  });
  assert.equal(fallback.plan.rationale, "Fastest option.");
});

test("switching to a rejected candidate: recommended shows as alternative + headline override", () => {
  const nowMs = 1_700_000_000_000;
  const steps = (line, minutes) => [
    { type: "SUBWAY", route_id: line, train_line: line, minutes_until_arrival: minutes },
  ];
  const recommended = {
    id: "candidate-0",
    index: 0,
    steps: steps("Q", 20),
    is_recommended: true,
    recommendation_reason: "Fastest tonight.",
  };
  const rejected = {
    id: "candidate-1",
    index: 1,
    steps: steps("B", 26),
    is_recommended: false,
    rejection_reason: "Slower by six minutes.",
  };

  const data = buildLeftRailData({
    nowMs,
    routeSteps: rejected.steps,
    routeCandidates: [recommended, rejected],
    activeRouteCandidate: rejected,
    switchHeadline: "Rerouting via the B, sir.",
  });

  assert.equal(data.plan.headline, "Rerouting via the B, sir.");
  const alts = data.plan.alternatives;
  assert.equal(alts.length, 1);
  assert.equal(alts[0].id, "candidate-0");
  assert.equal(alts[0].status, "recommended");
  assert.equal(alts[0].delta, "-6 min", "recommended is faster than active");
  assert.equal(alts[0].reason, "Fastest tonight.");
});

test("bus arrivals split tabs by stop compass; crosstown and unknown go both ways", () => {
  const nowMs = 1_700_000_000_000;
  const bus = (route, dest, stopCompass, stopId) => ({
    route_id: route,
    mode: "bus",
    arrival_time: nowMs / 1000 + 180,
    direction: "0",
    terminal_stop_name: dest,
    station_name: "W 34 ST/5 AV",
    distance_m: 120,
    stop_id: stopId,
    ...(stopCompass === undefined ? {} : { stop_compass: stopCompass }),
  });
  const liveFeed = {
    arrivals: [
      bus("M1", "HARLEM 147 ST", "NE", "400001"),
      bus("M2", "SOHO", "SW", "400002"),
      bus("M3", "VILLAGE", "SE", "400003"),
      bus("M34", "JAVITS CENTER", "E", "400004"),
      bus("Q32", "JACKSON HEIGHTS", undefined, "400005"),
      {
        route_id: "A",
        arrival_time: nowMs / 1000 + 240,
        terminal_stop_name: "Far Rockaway",
        station_name: "34 St-Penn",
        distance_m: 200,
        stop_id: "A28S",
      },
    ],
  };

  const data = buildLeftRailData({ liveFeed, nowMs });
  const wayByLine = Object.fromEntries(
    data.arrivals.map((arrival) => [arrival.line, arrival.way]),
  );

  assert.equal(wayByLine.M1, "uptown", "NE has a north component");
  assert.equal(wayByLine.M2, "downtown", "SW has a south component");
  assert.equal(wayByLine.M3, "downtown", "SE has a south component");
  assert.equal(wayByLine.M34, "both", "pure E is crosstown");
  assert.equal(wayByLine.Q32, "both", "missing compass degrades to both");
  assert.equal(wayByLine.A, "downtown", "subway still uses the stop-id suffix");
});
