import assert from "node:assert/strict";
import test from "node:test";

import {
  buildLeftRailData,
  buildRouteReasoningInsights,
  HALF_MILE_METERS,
} from "./live-data.ts";

test("route reasoning insights derive from real nearby facts only", () => {
  const groups = [
    {
      id: "church-av",
      name: "Church Av",
      mode: "subway",
      routeIds: ["Q", "B"],
      walkMinutes: 3,
      arrivals: [
        {
          id: "q-uptown",
          mode: "subway",
          routeIds: ["Q"],
          destination: "96 St",
          arrivalMinutes: [4],
          direction: "uptown",
          predictionType: "live",
        },
      ],
    },
  ];
  const busArrivals = [
    {
      id: "b41",
      mode: "bus",
      routeIds: ["B41"],
      destination: "Downtown Brooklyn",
      arrivalMinutes: [9],
      direction: "unknown",
      line: "B41",
      way: "unknown",
      dest: "Downtown Brooklyn",
      label: "9 min",
    },
  ];
  const alerts = [
    {
      sev: "minor",
      kind: "train",
      lines: ["Q"],
      title: "Delays",
      sub: "Active service notice",
      startedAgo: "5m",
      lastUpdate: "5m",
    },
  ];
  const incidents = [{ title: "Police activity · Church Av", severity: "high" }];

  const insights = buildRouteReasoningInsights({
    groups,
    busArrivals,
    alerts,
    incidents,
  });
  const texts = insights.map((insight) => insight.text).join("\n");

  assert.match(texts, /closest Q entrance is about a 3 min walk/);
  assert.match(texts, /Live arrivals favor the Q right now/);
  assert.match(texts, /service alerts on the Q/i);
  assert.match(
    texts,
    /Police activity was reported near Church Av, so reliability there is lower/,
    "incident language stays at 'reported', never confirmed",
  );
  assert.match(texts, /B41 is available, but the wait is longer right now/);
  assert.doesNotMatch(
    texts,
    /grok|claude|score|scan|analyz|graph|optimi[sz]/i,
    "no model/internal language in public insights",
  );

  // No facts → only the generic comparison closers remain.
  const bare = buildRouteReasoningInsights({
    groups: [],
    busArrivals: [],
    alerts: [],
    incidents: [],
  });
  assert.ok(bare.length >= 1);
  assert.ok(
    bare.every((insight) => insight.source === "comparison"),
    "fact-backed lines never render without their fact",
  );
});

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
    ["A", "B44", "C"],
  );
  assert.equal(data.arrivals[0].stationName, "Near Station");
  assert.equal(data.arrivals[0].label, "2 min");
  assert.equal(data.arrivals[1].mode, "bus");
  assert.equal(data.arrivals[1].stationName, "Nostrand Av/Eastern Pkwy");
  assert.equal(data.arrivals[2].stationName, "Second Station");
  assert.equal(data.arrivals[2].way, "downtown");
});

test("nearby arrivals expose passenger-facing destinations and grouped live times", () => {
  const nowMs = 1_700_000_000_000;
  const data = buildLeftRailData({
    nowMs,
    liveFeed: {
      nearest_stop: {
        stop_id: "R22",
        stop_name: "Prince St",
        distance_m: 252,
        route_ids: ["N", "Q", "R", "W"],
      },
      stops: [],
      arrivals: [
        {
          route_id: "N",
          stop_id: "R22N",
          station_name: "Prince St",
          parent_stop_name: "Prince St",
          distance_m: 252,
          arrival_time: nowMs / 1000 + 45,
          terminal_stop_name: "UPTOWN",
          direction: "UPTOWN",
        },
        {
          route_id: "N",
          stop_id: "R22N",
          station_name: "Prince St",
          parent_stop_name: "Prince St",
          distance_m: 252,
          arrival_time: nowMs / 1000 + 8 * 60,
          terminal_stop_name: "UPTOWN",
          direction: "UPTOWN",
        },
        {
          route_id: "Q",
          stop_id: "R22N",
          station_name: "Prince St",
          parent_stop_name: "Prince St",
          distance_m: 252,
          arrival_time: nowMs / 1000 + 2 * 60,
          terminal_stop_name: "96 St",
          direction: "UPTOWN",
        },
        {
          route_id: "F",
          stop_id: "D21N",
          station_name: "Broadway-Lafayette St",
          parent_stop_name: "Broadway-Lafayette St",
          distance_m: 256,
          arrival_time: nowMs / 1000 + 4 * 60,
          direction: "UPTOWN",
        },
        {
          route_id: "B63",
          mode: "bus",
          stop_id: "307710",
          station_name: "Court St stop",
          parent_stop_name: "Court St stop",
          distance_m: 168,
          arrival_time: nowMs / 1000 + 4 * 60,
          terminal_stop_name: "Downtown Brooklyn",
          direction: "0",
        },
      ],
      alerts: [],
      updated_at: nowMs / 1000,
    },
  });

  const n = data.arrivals.find((arrival) => arrival.routeIds?.[0] === "N");
  assert.ok(n, "N row exists");
  assert.equal(n.destination, "Astoria-Ditmars Blvd");
  assert.equal(n.label, "Now, 8 min");
  assert.deepEqual(n.arrivalMinutes, [0, 8]);
  assert.equal(n.servicePattern, "Broadway Local");
  assert.equal(n.stopName, "Prince St");
  assert.equal(n.walkMinutes, 3);
  assert.equal(n.direction, "uptown");
  assert.equal(n.predictionType, "live");
  assert.doesNotMatch(`${n.destination} ${n.label}`, /Uptown to Uptown|Northbound to Northbound/i);

  const q = data.arrivals.find((arrival) => arrival.routeIds?.[0] === "Q");
  assert.equal(q?.destination, "96 St");
  assert.equal(q?.label, "2 min");
  assert.equal(q?.servicePattern, "Broadway Express");

  const f = data.arrivals.find((arrival) => arrival.routeIds?.[0] === "F");
  assert.equal(f?.destination, "Jamaica-179 St");
  assert.equal(f?.servicePattern, "6 Av Local");

  const bus = data.arrivals.find((arrival) => arrival.mode === "bus");
  assert.equal(bus?.routeIds[0], "B63");
  assert.equal(bus?.destination, "Downtown Brooklyn");
  assert.equal(bus?.stopName, "Court St stop");
  assert.equal(bus?.walkMinutes, 2);
});

test("nearby subway arrivals group by station while buses remain separate rows", () => {
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
          arrival_time: nowMs / 1000 + 30,
          terminal_stop_name: "96 St",
          direction: "UPTOWN",
        },
        {
          route_id: "Q",
          stop_id: "D18N",
          station_name: "Church Av",
          parent_stop_name: "Church Av",
          distance_m: 123,
          arrival_time: nowMs / 1000 + 7 * 60,
          terminal_stop_name: "96 St",
          direction: "UPTOWN",
        },
        {
          route_id: "B",
          stop_id: "D18N",
          station_name: "Church Av",
          parent_stop_name: "Church Av",
          distance_m: 123,
          arrival_time: nowMs / 1000 + 2 * 60,
          terminal_stop_name: "Bedford Park Blvd",
          direction: "UPTOWN",
        },
        {
          route_id: "Q",
          stop_id: "D17N",
          station_name: "Beverley Rd",
          parent_stop_name: "Beverley Rd",
          distance_m: 620,
          arrival_time: nowMs / 1000 + 3 * 60,
          terminal_stop_name: "96 St",
          direction: "UPTOWN",
        },
        {
          route_id: "B63",
          mode: "bus",
          stop_id: "307710",
          station_name: "Court St stop",
          parent_stop_name: "Court St stop",
          distance_m: 168,
          arrival_time: nowMs / 1000 + 4 * 60,
          terminal_stop_name: "Downtown Brooklyn",
          direction: "0",
        },
      ],
      alerts: [],
      updated_at: nowMs / 1000,
    },
  });

  assert.deepEqual(
    data.nearbyTransitGroups.map((group) => group.name),
    ["Church Av", "Beverley Rd"],
  );

  const church = data.nearbyTransitGroups[0];
  assert.equal(church.mode, "subway");
  assert.deepEqual(church.routeIds, ["B", "Q"]);
  assert.equal(church.walkMinutes, 1);
  assert.equal(church.distanceMiles, 0.1);
  assert.deepEqual(
    church.arrivals.map((arrival) =>
      `${arrival.routeIds[0]}:${arrival.destination}:${arrival.arrivalMinutes.join(",")}`,
    ),
    ["Q:96 St:0,7", "B:Bedford Park Blvd:2"],
  );
  assert.equal(
    data.nearbyTransitGroups.some((group) =>
      group.arrivals.some((arrival) => arrival.mode === "bus"),
    ),
    false,
    "bus arrivals are not nested into subway station groups",
  );

  const beverley = data.nearbyTransitGroups[1];
  assert.deepEqual(beverley.routeIds, ["Q"]);
  assert.equal(beverley.arrivals[0].destination, "96 St");

  assert.deepEqual(
    data.nearbyBusArrivals.map((arrival) => `${arrival.routeIds[0]}:${arrival.destination}`),
    ["B63:Downtown Brooklyn"],
  );
  assert.equal(data.nearbyBusArrivals[0].stopName, "Court St stop");
  assert.equal(data.nearbyBusArrivals[0].walkMinutes, 2);
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
  assert.deepEqual(dests, ["East Side Ferry", "Javits Center"]);
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
  assert.equal(alts[0].reason, "Signal problems at DeKalb");
  assert.equal(alts[0].status, "rejected");
  assert.equal(alts[1].delta, "+4 min");
  assert.equal(alts[1].sev, "medium");
  assert.equal(alts[1].reason, "1 extra transfer");
});

test("plan rationale surfaces sanitized model route reasoning with fallback", () => {
  const nowMs = 1_700_000_000_000;
  const steps = [
    { type: "WALK", arrival_stop: "Church Av", minutes_until_arrival: 3 },
    {
      type: "SUBWAY",
      route_id: "Q",
      train_line: "Q",
      departure_stop: "Church Av",
      arrival_stop: "Avenue M",
      minutes_until_arrival: 20,
      minutes_until_train_arrives: 6,
    },
    {
      type: "WALK",
      arrival_stop: "Maimonides Medical Center",
      minutes_until_arrival: 1,
    },
  ];
  const candidate = {
    id: "c0",
    index: 0,
    steps,
    is_recommended: true,
    recommendation_reason:
      "Take the Q because it avoids the delayed B and keeps the trip direct.",
    score_breakdown: { duration_minutes: 20, transfers: 0, active_alerts: 0 },
  };
  const alternate = {
    id: "c1",
    index: 1,
    steps: [
      { type: "WALK", arrival_stop: "Church Av", minutes_until_arrival: 3 },
      {
        type: "SUBWAY",
        route_id: "B",
        train_line: "B",
        departure_stop: "Church Av",
        arrival_stop: "Avenue M",
        minutes_until_arrival: 33,
      },
    ],
    is_recommended: false,
    rejection_reason: "Slower by about 13 minutes under current service conditions",
  };
  const data = buildLeftRailData({
    nowMs,
    routeSteps: steps,
    routeCandidates: [candidate, alternate],
    activeRouteCandidate: candidate,
    recommendationText:
      "# Transit Route Analysis\n\n## Route Comparison\n\n| Route | Time |\nBased on the provided transit data, take the Q, sir.",
    routeEta: "12:58 PM",
    routeTotalTime: "36 min",
  });
  assert.match(
    data.plan.rationale,
    /Take the Q because it avoids the delayed B and keeps the trip direct\./,
    "rationale uses the compact recommendation reason from candidate_analysis",
  );
  assert.match(
    data.plan.rationale,
    /I did not pick the B because it is slower by 13 min/,
    "rationale explains why the leading alternate was not selected",
  );
  assert.doesNotMatch(
    data.plan.rationale,
    /analysis|comparison|based on|sir|[#|]/i,
    "raw narration and markdown never reach the rail",
  );
  assert.equal(data.plan.headsign, "Avenue M", "card title is the passenger-facing headsign");
  // Real ETA replaces the "Live · Calculated" placeholder.
  assert.equal(data.plan.eta, "12:58 PM");
  assert.equal(data.plan.totalTime, "36 min");
  assert.equal(
    data.plan.nextDepartureMinutes,
    6,
    "recommended card countdown uses the real first vehicle arrival prediction",
  );

  // The transit leg carries the live departure note for the timeline.
  const transit = data.plan.steps.find((step) => step.line === "Q");
  assert.equal(transit?.note, "Departs in 6 min");
  assert.equal(transit?.live, true);
  const last = data.plan.steps[data.plan.steps.length - 1];
  assert.equal(last.detail, "Arrive at destination");

  // Without candidate_analysis text, the card falls back to real route facts.
  const fallbackCandidate = { ...candidate, recommendation_reason: "" };
  const fallback = buildLeftRailData({
    nowMs,
    routeSteps: steps,
    routeCandidates: [fallbackCandidate],
    activeRouteCandidate: fallbackCandidate,
  });
  assert.match(
    fallback.plan.rationale,
    /Fastest available option/,
  );
  assert.match(
    fallback.plan.rationale,
    /live arrival in 6 min/,
  );
});

test("plan carries strip, detail steps, leave-by, and correct transfer count", () => {
  const nowMs = 1_700_000_000_000;
  const steps = [
    { type: "WALK", arrival_stop: "Church Av", minutes_until_arrival: 3 },
    {
      type: "SUBWAY",
      route_id: "Q",
      train_line: "Q",
      departure_stop: "Church Av",
      arrival_stop: "14 St-Union Sq",
      direction: "Manhattan-bound to 96 St",
      minutes_until_arrival: 24,
      minutes_until_train_arrives: 7,
      stop_count: 7,
    },
    {
      type: "SUBWAY",
      route_id: "5",
      train_line: "5",
      departure_stop: "14 St-Union Sq",
      arrival_stop: "Burke Av",
      direction: "Uptown to Nereid Av",
      minutes_until_arrival: 44,
      stop_count: 12,
    },
    { type: "WALK", arrival_stop: "Adee Av", minutes_until_arrival: 5 },
  ];
  const candidate = {
    id: "c0",
    index: 0,
    steps,
    is_recommended: true,
    total_minutes: 86,
  };
  const data = buildLeftRailData({
    nowMs,
    routeSteps: steps,
    routeCandidates: [candidate],
    activeRouteCandidate: candidate,
  });

  // Transfers = vehicle boardings minus one; start/end walks never count.
  assert.equal(data.plan.transferCount, 1);

  assert.deepEqual(
    data.plan.strip?.map((segment) =>
      segment.kind === "walk"
        ? `walk:${segment.minutes}`
        : `${segment.mode}:${segment.routeId}`,
    ),
    ["walk:3", "subway:Q", "subway:5", "walk:5"],
    "compact strip mirrors the journey order",
  );

  assert.equal(
    typeof data.plan.leaveByLabel,
    "string",
    "leave-by derives from transit departure minus the approach walk",
  );
  assert.equal(
    data.plan.nextDepartureMinutes,
    7,
    "route plan exposes the selected route's next vehicle countdown",
  );

  assert.deepEqual(
    data.plan.detailSteps?.map((step) => step.kind),
    ["walk", "board", "ride", "board", "ride", "walk"],
  );
  assert.equal(
    data.plan.detailSteps?.[0]?.title,
    "Walk to Church Av station",
    "approach walk names the subway station instead of a generic stop",
  );
  const boardQ = data.plan.detailSteps?.[1];
  assert.equal(boardQ?.title, "Board the Q train");
  assert.equal(boardQ?.subtitle, "Manhattan-bound to 96 St");
  assert.equal(boardQ?.note, "Departs in 7 min");
  const rideQ = data.plan.detailSteps?.[2];
  assert.equal(rideQ?.rideMeta, "Ride 7 stops · 24 min");
  assert.equal(rideQ?.transferTo, "5", "ride hands off to the next boarding");
  assert.equal(
    data.plan.detailSteps?.[data.plan.detailSteps.length - 1]?.title,
    "Walk to destination",
  );

  // Single-leg trip: zero transfers.
  const singleLeg = buildLeftRailData({
    nowMs,
    routeSteps: [steps[0], steps[1], steps[3]],
    routeCandidates: [candidate],
    activeRouteCandidate: candidate,
  });
  assert.equal(singleLeg.plan.transferCount, 0);
});

test("walk detail titles name subway stations and bus stops", () => {
  const nowMs = 1_700_000_000_000;
  const busSteps = [
    { type: "WALK", arrival_stop: "Court St stop", minutes_until_arrival: 2 },
    {
      type: "BUS",
      route_id: "B63",
      train_line: "B63",
      departure_stop: "Court St stop",
      arrival_stop: "Downtown Brooklyn",
      minutes_until_arrival: 12,
      minutes_until_train_arrives: 4,
    },
    { type: "WALK", arrival_stop: "Atlantic Terminal", minutes_until_arrival: 3 },
  ];
  const subwaySteps = [
    { type: "WALK", arrival_stop: "Church Av", minutes_until_arrival: 2 },
    {
      type: "SUBWAY",
      route_id: "Q",
      train_line: "Q",
      departure_stop: "Church Av",
      arrival_stop: "96 St",
      minutes_until_arrival: 28,
    },
  ];

  const busCandidate = { id: "bus", index: 0, steps: busSteps, is_recommended: true };
  const busData = buildLeftRailData({
    nowMs,
    routeSteps: busSteps,
    routeCandidates: [busCandidate],
    activeRouteCandidate: busCandidate,
  });
  assert.equal(
    busData.plan.detailSteps?.[0]?.title,
    "Walk to Court St stop",
    "bus approach walk uses the stop name without station wording",
  );

  const subwayCandidate = { id: "subway", index: 0, steps: subwaySteps, is_recommended: true };
  const subwayData = buildLeftRailData({
    nowMs,
    routeSteps: subwaySteps,
    routeCandidates: [subwayCandidate],
    activeRouteCandidate: subwayCandidate,
  });
  assert.equal(
    subwayData.plan.detailSteps?.[0]?.title,
    "Walk to Church Av station",
    "subway approach walk appends station when the backend only sends the station name",
  );
});

test("alternate routes deduplicate by route signature", () => {
  const nowMs = 1_700_000_000_000;
  const subway = (line, minutes, extra = {}) => [
    {
      type: "SUBWAY",
      route_id: line,
      train_line: line,
      departure_stop: "Church Av",
      arrival_stop: "Avenue M",
      minutes_until_arrival: minutes,
      ...extra,
    },
  ];
  const bus = (line, minutes) => [
    {
      type: "BUS",
      route_id: line,
      train_line: line,
      departure_stop: "Church Av/E 18 St",
      arrival_stop: "Kings Hwy",
      minutes_until_arrival: minutes,
    },
  ];
  const candidates = [
    { id: "c0", index: 0, steps: subway("Q", 22), is_recommended: true },
    // Identical route, identical time — pure clone, dropped entirely.
    { id: "c1", index: 1, steps: subway("Q", 22), is_recommended: false },
    // Identical route, later departure — kept once with a real distinction.
    { id: "c2", index: 2, steps: subway("Q", 26), is_recommended: false },
    // Another same-route clone — the one slot is already used.
    { id: "c3", index: 3, steps: subway("Q", 30), is_recommended: false },
    { id: "c4", index: 4, steps: bus("B49", 27), is_recommended: false, rejection_reason: "More walking." },
    // Duplicate of the B49 — first (better) one wins.
    { id: "c5", index: 5, steps: bus("B49", 33), is_recommended: false },
  ];

  const data = buildLeftRailData({
    nowMs,
    routeSteps: candidates[0].steps,
    routeCandidates: candidates,
    activeRouteCandidate: candidates[0],
  });

  const alts = data.plan.alternatives;
  assert.deepEqual(
    alts.map((alt) => alt.id),
    ["c2", "c4"],
    "clones collapse: one later-departure Q, one B49",
  );
  assert.equal(alts[0].dest, "Later departure");
  assert.equal(alts[0].reason, "Later departure");
  assert.equal(alts[1].line, "B49");
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

  assert.equal(data.plan.headline, "Rerouting via the B.");
  const alts = data.plan.alternatives;
  assert.equal(alts.length, 1);
  assert.equal(alts[0].id, "candidate-0");
  assert.equal(alts[0].status, "recommended");
  assert.equal(alts[0].delta, "-6 min", "recommended is faster than active");
  assert.equal(alts[0].reason, "Fastest tonight");
});

test("alternate reason copy normalizes long backend rejection strings", () => {
  const nowMs = 1_700_000_000_000;
  const steps = (line, minutes) => [
    { type: "SUBWAY", route_id: line, train_line: line, minutes_until_arrival: minutes },
  ];

  const data = buildLeftRailData({
    nowMs,
    routeSteps: steps("Q", 13),
    routeCandidates: [
      { id: "c0", index: 0, steps: steps("Q", 13), is_recommended: true },
      {
        id: "c1",
        index: 1,
        steps: steps("B", 26),
        is_recommended: false,
        rejection_reason: "Slower by about 13 minutes under current service conditions",
      },
      {
        id: "c2",
        index: 2,
        steps: steps("D", 15),
        is_recommended: false,
        rejection_reason: "One extra transfer.",
      },
      {
        id: "c3",
        index: 3,
        steps: steps("F", 16),
        is_recommended: false,
        rejection_reason: "More walking than the recommended route.",
      },
      {
        id: "c4",
        index: 4,
        steps: steps("A", 21),
        is_recommended: false,
        rejection_reason: "Slower by 8 minutes and affected by Signal problem near DeKalb Av.",
      },
      {
        id: "c5",
        index: 5,
        steps: steps("C", 11),
        is_recommended: false,
        rejection_reason: "Faster by 2 minutes, but affected by Stalled vehicle at 34 St.",
      },
    ],
    activeRouteCandidate: { id: "c0", index: 0, steps: steps("Q", 13), is_recommended: true },
  });

  assert.deepEqual(
    data.plan.alternatives.map((alternate) => alternate.reason),
    [
      "Slower by 13 min · service conditions",
      "1 extra transfer",
      "More walking",
      "Slower by 8 min · Signal problem near DeKalb Av",
      "Faster by 2 min · Stalled vehicle at 34 St",
    ],
  );
});

test("plan rationale explains faster disrupted alternates as reliability tradeoffs", () => {
  const nowMs = 1_700_000_000_000;
  const steps = (line, minutes) => [
    { type: "SUBWAY", route_id: line, train_line: line, minutes_until_arrival: minutes },
  ];

  const active = {
    id: "c0",
    index: 0,
    steps: steps("Q", 13),
    is_recommended: true,
    recommendation_reason: "Fastest clear route with no reported delays.",
  };
  const disrupted = {
    id: "c1",
    index: 1,
    steps: steps("C", 11),
    is_recommended: false,
    rejection_reason: "Faster by 2 minutes, but affected by Stalled vehicle at 34 St.",
  };

  const data = buildLeftRailData({
    nowMs,
    routeSteps: active.steps,
    routeCandidates: [active, disrupted],
    activeRouteCandidate: active,
  });

  assert.match(
    data.plan.rationale,
    /I did not pick the C because it is affected by Stalled vehicle at 34 St despite being 2 min faster\./,
  );
});

test("bus arrivals split tabs by stop compass; crosstown and unknown remain all-directions rows", () => {
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
  assert.equal(wayByLine.M34, "unknown", "pure E is crosstown");
  assert.equal(wayByLine.Q32, "unknown", "missing compass degrades to all-directions only");
  assert.equal(wayByLine.A, "downtown", "subway still uses the stop-id suffix");
});

test("nearby display rows expose prediction freshness, distance, and usefulness sort", () => {
  const nowMs = 1_700_000_000_000;
  const liveFeed = {
    arrivals: [
      {
        route_id: "B63",
        mode: "bus",
        arrival_time: nowMs / 1000 + 60,
        terminal_stop_name: "Downtown Brooklyn",
        station_name: "5 Av stop",
        distance_m: 520,
        stop_id: "308214",
        prediction_type: "live",
      },
      {
        route_id: "Q",
        arrival_time: nowMs / 1000 + 180,
        terminal_stop_name: "96 St",
        station_name: "Prince St",
        distance_m: 120,
        stop_id: "R22N",
        prediction_type: "live",
      },
      {
        route_id: "N",
        arrival_time: nowMs / 1000 - 90,
        terminal_stop_name: "Astoria-Ditmars Blvd",
        station_name: "Prince St",
        distance_m: 120,
        stop_id: "R22N",
        prediction_type: "live",
      },
      {
        route_id: "F",
        arrival_time: nowMs / 1000 + 240,
        terminal_stop_name: "Jamaica-179 St",
        station_name: "2 Av",
        distance_m: 180,
        stop_id: "D21N",
        prediction_type: "scheduled",
      },
    ],
  };

  const data = buildLeftRailData({ liveFeed, nowMs });

  assert.deepEqual(
    data.arrivals.map((arrival) => arrival.line),
    ["N", "Q", "F", "B63"],
    "nearby walks outrank a one-minute bus that is much farther away",
  );

  const q = data.arrivals.find((arrival) => arrival.line === "Q");
  assert.equal(q?.predictionFreshness, "fresh");
  assert.equal(q?.predictionType, "live");
  assert.equal(q?.distanceMiles, 0.1);

  const stale = data.arrivals.find((arrival) => arrival.line === "N");
  assert.equal(stale?.label, "Now");
  assert.equal(stale?.predictionFreshness, "stale");
  assert.equal(stale?.predictionType, "live");

  const scheduled = data.arrivals.find((arrival) => arrival.line === "F");
  assert.equal(scheduled?.predictionFreshness, "scheduled");
  assert.equal(scheduled?.predictionType, "scheduled");
});
