import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  BusChip,
  Dot,
  RouteBulletGroup,
  TransitText,
  btnGhost,
} from "../atoms.tsx";
import { buildPlan } from "./route-plan.ts";
import {
  buildAlternatives,
  candidateSignature,
  firstTransitStep,
  transitRouteIdsFromSteps,
} from "./route-candidates.ts";
import { buildVisibleRouteReason } from "./route-reason-copy.ts";
import {
  detailStepsFromCanonicalItinerary,
  detailStepsFromSteps,
  routeStepToRailStep,
  stripFromSteps,
} from "./route-steps.ts";
import {
  buildArrivalRows,
  buildNearbyBusArrivals,
  buildNearbySubwayGroups,
} from "./nearby-arrivals.ts";

const nowMs = 1_700_000_000_000;
const at = (mins) => nowMs / 1000 + mins * 60;

function live(overrides = {}) {
  return {
    route_id: "Q",
    stop_id: "Q01N",
    station_name: "Canal St",
    distance_m: 90,
    arrival_time: at(4),
    terminal_stop_name: "96 St",
    ...overrides,
  };
}

function subway(overrides = {}) {
  return {
    id: "q",
    mode: "subway",
    routeIds: ["Q"],
    destination: "96 St",
    stopName: "Canal St",
    walkMinutes: 3,
    arrivalMinutes: [4],
    direction: "uptown",
    predictionType: "live",
    predictionFreshness: "fresh",
    alertSeverity: "none",
    line: "Q",
    way: "uptown",
    dest: "96 St",
    label: "4 min",
    mins: 4,
    status: "On Time",
    stale: false,
    stationName: "Canal St",
    ...overrides,
  };
}

function bus(overrides = {}) {
  return subway({
    mode: "bus",
    routeIds: ["B41"],
    destination: "Downtown Brooklyn",
    dest: "Downtown Brooklyn",
    line: "B41",
    ...overrides,
  });
}

function stop(name, extra = {}) {
  return subway({ stationName: name, stopName: name, ...extra });
}

function candidate(overrides = {}) {
  return {
    id: "a",
    index: 0,
    is_recommended: true,
    recommendation_reason: "the A train is faster",
    steps: [{ type: "SUBWAY", route_id: "A", departure_stop: "Jay", arrival_stop: "High" }],
    ...overrides,
  };
}

test("equal-score buses break ties on measured walk, then line name", () => {
  const byLine = buildNearbyBusArrivals([
    bus({ id: "m15", line: "M15", routeIds: ["M15"], walkMinutes: 4, arrivalMinutes: [6], mins: 6 }),
    bus({ id: "b41", line: "B41", routeIds: ["B41"], walkMinutes: 4, arrivalMinutes: [6], mins: 6 }),
  ]);
  assert.deepEqual(byLine.map((row) => row.line), ["B41", "M15"]);

  const measuredFirst = buildNearbyBusArrivals([
    bus({ id: "known", line: "B42", routeIds: ["B42"], walkMinutes: 10, arrivalMinutes: [6], mins: 6 }),
    bus({ id: "unknown", line: "B63", routeIds: ["B63"], walkMinutes: undefined, arrivalMinutes: [6], mins: 6 }),
  ]);
  assert.equal(measuredFirst[0].line, "B42");

  const unknownFirst = buildNearbyBusArrivals([
    bus({ id: "unknown", line: "B63", routeIds: ["B63"], walkMinutes: undefined, arrivalMinutes: [6], mins: 6 }),
    bus({ id: "known", line: "B42", routeIds: ["B42"], walkMinutes: 10, arrivalMinutes: [6], mins: 6 }),
  ]);
  assert.equal(unknownFirst[0].line, "B42");
});

test("a measured station distance beats a missing distance on the same service", () => {
  const rows = buildArrivalRows({
    arrivals: [
      live({ station_name: "Unknown Dist", distance_m: undefined, arrival_time: at(2) }),
      live({ station_name: "Known Dist", stop_id: "Q02N", distance_m: 80, arrival_time: at(9) }),
    ],
  }, nowMs).serviceRows;
  assert.equal(rows[0].stopName, "Known Dist");
});

test("a measured walk beats a missing walk even when the farther station arrives sooner", () => {
  const omitted = buildNearbySubwayGroups([
    stop("Later", { walkMinutes: undefined, arrivalMinutes: [12], mins: 12 }),
    stop("Soon", { walkMinutes: undefined, arrivalMinutes: [4], mins: 4 }),
  ]);
  assert.deepEqual(omitted.map((group) => group.name).sort(), ["Later", "Soon"]);
  assert.equal(
    buildNearbySubwayGroups([
      stop("Far", { walkMinutes: undefined, arrivalMinutes: [2], mins: 2 }),
      stop("Near", { walkMinutes: 3, arrivalMinutes: [10], mins: 10 }),
    ])[0].name,
    "Near",
  );
  assert.equal(
    buildNearbySubwayGroups([
      stop("Near", { walkMinutes: 3, arrivalMinutes: [10], mins: 10 }),
      stop("Far", { walkMinutes: undefined, arrivalMinutes: [2], mins: 2 }),
    ])[0].name,
    "Near",
  );
});

test("grouped arrivals keep sooner trains first and fill missing route ids last", () => {
  const [times] = buildNearbySubwayGroups([
    subway({ id: "late", destination: "Apple", dest: "Apple", arrivalMinutes: [12], mins: 12 }),
    subway({ id: "soon", destination: "Zebra", dest: "Zebra", arrivalMinutes: [3], mins: 3 }),
  ]);
  assert.deepEqual(times.arrivals.map((row) => row.destination), ["Zebra", "Apple"]);

  const [routes] = buildNearbySubwayGroups([
    subway({ id: "blank", routeIds: [], line: "", destination: "96 St", dest: "96 St", arrivalMinutes: [4], mins: 4 }),
    subway({ id: "q", destination: "96 St", dest: "96 St", arrivalMinutes: [4], mins: 4 }),
  ]);
  assert.equal(routes.arrivals[0].routeIds[0], "Q");
});

test("the nearest walk in a station prefers a measured minute in either input order", () => {
  const omitFirst = buildNearbySubwayGroups([
    subway({ id: "omit", walkMinutes: undefined }),
    subway({ id: "near", walkMinutes: 2 }),
    subway({ id: "far", walkMinutes: 9 }),
  ]);
  assert.equal(omitFirst[0].walkMinutes, 2);

  const nearFirst = buildNearbySubwayGroups([
    subway({ id: "near", walkMinutes: 2 }),
    subway({ id: "omit", walkMinutes: undefined }),
    subway({ id: "far", walkMinutes: 9 }),
  ]);
  assert.equal(nearFirst[0].walkMinutes, 2);
});

test("a subway rail step without ids keeps an empty line and a train label", () => {
  const step = routeStepToRailStep({ type: "SUBWAY" }, 0);
  assert.equal(step.line, "");
  assert.equal(step.title, " train");
  assert.equal(step.detail, "Transit segment");
  assert.deepEqual(stripFromSteps([{ type: "SUBWAY" }]), [
    { kind: "ride", routeId: "", mode: "subway" },
  ]);
  assert.equal(detailStepsFromSteps([{ type: "SUBWAY" }])[0].title, "Board the  train");
  assert.deepEqual(detailStepsFromSteps(undefined), []);
});

test("canonical legs with invalid seconds keep the provider walk minutes", () => {
  const nanWalk = detailStepsFromCanonicalItinerary(
    [{ type: "WALK", arrival_stop: "Home", duration_minutes: 6 }],
    { legs: [{ mode: "WALK", walk_seconds: Number.NaN }] },
  );
  assert.equal(nanWalk[0].subtitle, "About 6 min");

  const negativeRide = detailStepsFromCanonicalItinerary(
    [{ type: "SUBWAY", route_id: "A", duration_minutes: 11 }],
    { legs: [{ mode: "SUBWAY", ride_seconds: -40 }] },
  );
  assert.equal(negativeRide[1].rideMeta, "Ride · 11 min");

  assert.deepEqual(
    detailStepsFromCanonicalItinerary(undefined, { legs: [{ mode: "WALK", walk_seconds: 120 }] }),
    [],
  );
  assert.equal(
    detailStepsFromCanonicalItinerary(undefined, {
      segments: [{ segment_index: 0, destination: { label: "Museum" }, legs: [] }],
    })[0].title,
    "Leg 1 · To Museum",
  );
});

test("segmented canonical details copy named stops and skip empty stop lists", () => {
  const details = detailStepsFromCanonicalItinerary(
    [
      {
        type: "SUBWAY",
        route_id: "L",
        segment_index: 0,
        departure_stop: "1 Av",
        arrival_stop: "8 Av",
      },
    ],
    {
      segments: [
        {
          segment_index: 0,
          destination: { label: "8 Av" },
          legs: [
            {
              mode: "SUBWAY",
              ride_seconds: 240,
              stops: [{ name: "1 Av" }, { name: "3 Av" }, { name: "8 Av" }],
            },
          ],
        },
      ],
    },
  );
  const ride = details.find((step) => step.kind === "ride");
  assert.deepEqual(ride.stops, ["3 Av"]);
  assert.equal(ride.rideMeta, "Ride 2 stops · 4 min");
});

test("buildPlan copy covers empty candidates, arrive fallbacks, and walking routes", () => {
  const empty = buildPlan(undefined, null);
  assert.equal(empty.headline, "Choose a destination for route guidance.");
  assert.equal(empty.totalTime, "Pending");
  assert.equal(empty.rationale, "Nearby arrivals are live within a half-mile radius.");

  const walked = buildPlan([{ type: "WALK", arrival_stop: "Canal St" }], null);
  assert.equal(walked.totalTime, "Calculated");
  assert.equal(walked.steps.at(-1).type, "arrive");

  const switched = buildPlan([], candidate({ is_recommended: false }), [], "Take the C instead.");
  assert.equal(switched.headline, "Take the C instead.");

  const alternative = buildPlan([], candidate({ is_recommended: false }));
  assert.equal(alternative.headline, "Alternative route engaged.");
  assert.equal(alternative.headsign, "Walking route");
  assert.equal(alternative.pickedLine, "");

  const whitespaceArrive = buildPlan(
    [{ type: "WALK", arrival_stop: "   ", departure_stop: "   " }],
    candidate(),
  );
  assert.equal(whitespaceArrive.steps.at(-1).title, "Destination");

  const byRouteId = buildPlan(
    [{ type: "SUBWAY", route_id: "R", arrival_stop: "Union Sq" }],
    candidate(),
  );
  assert.equal(byRouteId.pickedLine, "R");
  assert.equal(byRouteId.headsign, "Union Sq");

  const untitled = buildPlan(
    [{ type: "SUBWAY", train_line: "N" }],
    candidate(),
  );
  assert.equal(untitled.pickedLine, "N");
  assert.equal(untitled.headsign, untitled.steps.at(-1).title);

  const pendingFacts = buildPlan([], { id: "bare", is_recommended: true });
  assert.equal(pendingFacts.totalTime, "Pending");
  assert.equal(pendingFacts.eta, "Live");

  const chat = buildPlan([{ type: "WALK" }], candidate(), [candidate()], null, null, null, "chat");
  assert.equal(chat.rationale, "");
});

test("candidate signatures and alternatives keep walk-only and blank transit ids", () => {
  assert.equal(candidateSignature(undefined), "walk-only");
  assert.equal(candidateSignature({}), "walk-only");
  assert.equal(
    candidateSignature({ steps: [{ type: "SUBWAY", departure_stop: "Canal", arrival_stop: "Union" }] }),
    "SUBWAY::Canal:Union",
  );
  assert.deepEqual(transitRouteIdsFromSteps(undefined), []);
  assert.deepEqual(
    transitRouteIdsFromSteps([{ type: "SUBWAY" }, { type: "BUS", route_id: "  " }, { type: "WALK" }]),
    [],
  );
  assert.equal(firstTransitStep([]), undefined);

  const active = candidate({ total_minutes: 20 });
  const walkAlt = buildAlternatives(
    [active, { id: "walk", total_minutes: 30, steps: [{ type: "WALK" }] }],
    active,
  );
  assert.equal(walkAlt[0].line, "WALK");
  assert.equal(walkAlt[0].dest, "Alternate routing");

  const noSteps = buildAlternatives(
    [active, { id: "bare", total_minutes: 28 }],
    active,
  );
  assert.equal(noSteps[0].line, "WALK");
});

test("visible route reason names walk-only options, missing indexes, and transfer stops", () => {
  const walkOnly = candidate({
    id: "walk",
    recommendation_reason: "",
    steps: [{ type: "WALK" }],
  });
  assert.match(buildVisibleRouteReason(walkOnly, walkOnly.steps, [walkOnly]), /another route|Fastest available option/);

  const indexed = candidate({
    recommendation_reason: "route 9 is the backup",
  });
  assert.match(buildVisibleRouteReason(indexed, indexed.steps, [indexed]), /That option is the backup/);

  const busTransfer = candidate({
    id: "bus",
    is_recommended: false,
    rejection_reason: "More walking",
    steps: [
      { type: "BUS", route_id: "B41", departure_stop: "Atlantic" },
      { type: "BUS", route_id: "B63", departure_stop: "Church Av" },
    ],
  });
  const recommended = candidate();
  assert.match(
    buildVisibleRouteReason(recommended, recommended.steps, [recommended, busTransfer]),
    /via Church Av/,
  );

  const otherA = candidate({
    id: "other",
    is_recommended: false,
    rejection_reason: "More walking",
    steps: [{ type: "SUBWAY", route_id: "A", departure_stop: "High St", arrival_stop: "Far Rockaway" }],
  });
  assert.match(
    buildVisibleRouteReason(recommended, recommended.steps, [recommended, otherA]),
    /from High St/,
  );

  assert.match(buildVisibleRouteReason(recommended, recommended.steps, undefined), /A train is faster|Fastest available option/);
});

test("rail atoms keep idle dots, unclipped bullets, long bus labels, and unknown tokens", () => {
  const idle = renderToStaticMarkup(createElement(Dot, { color: "#0f0" }));
  assert.match(idle, /none/);
  assert.doesNotMatch(idle, /srPulse/);

  const allLines = renderToStaticMarkup(
    createElement(RouteBulletGroup, { lines: ["Q", "N"] }),
  );
  assert.doesNotMatch(allLines, /\+/);

  const longBus = renderToStaticMarkup(createElement(BusChip, { route: "M34-SBS" }));
  assert.match(longBus, /M34-SBS/);

  const unknown = renderToStaticMarkup(
    createElement(TransitText, { text: "See [HELLO] then the [Q] platform." }),
  );
  assert.match(unknown, /HELLO/);
  assert.match(unknown, /sr-line-token/);
  assert.match(unknown, /platform\./);

  assert.equal(btnGhost().background, "transparent");
});
