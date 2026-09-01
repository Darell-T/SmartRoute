import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAlternatives,
  candidateDelta,
  candidateSignature,
  canonicalDurationMinutes,
  canonicalTransferCount,
  clockFromIso,
  firstTransitStep,
  normalizeAlternateReason,
  transitRouteIdsFromSteps,
  trimReason,
} from "./route-candidates.ts";
import {
  detailStepsFromCanonicalItinerary,
  detailStepsFromSteps,
  isTransitStep,
  mergeConsecutiveWalks,
  routeStepToRailStep,
  stripFromSteps,
} from "./route-steps.ts";

test("route rail steps expose walk, ride, fallback, and live labels", () => {
  assert.deepEqual(routeStepToRailStep({ type: "WALK", arrival_stop: "" }, 0), {
    type: "walk",
    action: "Walk",
    title: "Walk",
    detail: "Continue on foot",
    duration: "walk",
  });
  assert.deepEqual(
    routeStepToRailStep(
      { type: "WALK", arrival_stop: "Canal St", duration_minutes: 2.4 },
      3,
    ),
    {
      type: "exit",
      action: "Walk",
      title: "Walk",
      detail: "To Canal St",
      duration: "2 min",
    },
  );
  assert.deepEqual(
    routeStepToRailStep(
      {
        type: "BUS",
        direction: "Downtown",
        minutes_until_train_arrives: 0.2,
      },
      0,
    ),
    {
      type: "board",
      action: "Board",
      line: "BUS",
      title: "BUS bus",
      detail: "Downtown",
      note: "Departs in 1 min",
      live: true,
      duration: "live",
    },
  );
  assert.deepEqual(
    routeStepToRailStep(
      { type: "SUBWAY", route_id: "R", arrival_stop: "Union Sq" },
      2,
    ),
    {
      type: "ride",
      action: "Ride",
      line: "R",
      title: "R train",
      detail: "Union Sq",
      duration: "live",
    },
  );
  assert.equal(routeStepToRailStep({ type: "SUBWAY", train_line: "Q" }, 1).line, "Q");
  assert.equal(isTransitStep({ type: "BUS" }), true);
  assert.equal(isTransitStep({ type: "WALK" }), false);
});

test("consecutive walks merge passenger timing and destination fields", () => {
  assert.deepEqual(
    mergeConsecutiveWalks([
      {
        type: "WALK",
        arrival_stop: "First",
        duration_minutes: 2,
        minutes_until_arrival: 9,
      },
      {
        type: "WALK",
        arrival_stop: "",
        duration_minutes: 3,
      },
      {
        type: "BUS",
        route_id: "M15",
      },
    ]),
    [
      {
        type: "WALK",
        arrival_stop: "First",
        duration_minutes: 5,
        minutes_until_arrival: 9,
      },
      {
        type: "BUS",
        route_id: "M15",
      },
    ],
  );
  assert.deepEqual(
    mergeConsecutiveWalks([
      { type: "WALK", arrival_stop: "Old" },
      { type: "WALK", arrival_stop: "Final", minutes_until_arrival: 4 },
    ]),
    [
      {
        type: "WALK",
        arrival_stop: "Final",
        minutes_until_arrival: 4,
        duration_minutes: undefined,
      },
    ],
  );
});

test("route strips distinguish missing walks, buses, and subway rides", () => {
  assert.deepEqual(stripFromSteps(undefined), []);
  assert.deepEqual(
    stripFromSteps([
      { type: "WALK" },
      { type: "BUS", route_id: "m15" },
      { type: "SUBWAY", train_line: "q" },
      { type: "WALK", duration_minutes: 0.2 },
    ]),
    [
      { kind: "walk", minutes: undefined },
      { kind: "ride", routeId: "M15", mode: "bus" },
      { kind: "ride", routeId: "Q", mode: "subway" },
      { kind: "walk", minutes: 1 },
    ],
  );
});

test("detail steps label walk destinations and transit boarding points", () => {
  const details = detailStepsFromSteps([
    { type: "WALK", arrival_stop: "Canal St station", duration_minutes: 2 },
    { type: "SUBWAY", route_id: "Q" },
    { type: "WALK", arrival_stop: "Atlantic Av" },
    { type: "SUBWAY", route_id: "B" },
    { type: "WALK" },
    { type: "BUS", route_id: "B41" },
    { type: "WALK" },
    { type: "SUBWAY", route_id: "A" },
    { type: "WALK", arrival_stop: "Home", duration_minutes: 4 },
  ]);
  assert.deepEqual(
    details.filter((step) => step.kind === "walk"),
    [
      {
        kind: "walk",
        title: "Walk to Canal St station",
        subtitle: "About 2 min",
      },
      {
        kind: "walk",
        title: "Walk to Atlantic Av station",
        subtitle: undefined,
      },
      {
        kind: "walk",
        title: "Walk to bus stop",
        subtitle: undefined,
      },
      {
        kind: "walk",
        title: "Walk to station",
        subtitle: undefined,
      },
      {
        kind: "walk",
        title: "Walk to destination",
        subtitle: "About 4 min",
      },
    ],
  );
});

test("transit details expose headsigns, stops, ride time, and transfers", () => {
  const details = detailStepsFromSteps([
    {
      type: "SUBWAY",
      route_id: "q",
      departure_stop: "Canal St",
      arrival_stop: "Atlantic Av",
      direction: "Downtown-bound",
      intermediate_stops: ["Canal St", "DeKalb Av", "Atlantic Av"],
      stop_count: 1,
      duration_minutes: 7,
    },
    {
      type: "BUS",
      train_line: "b41",
      departure_stop: "Atlantic Av",
      arrival_stop: "Flatbush Av",
      direction: "Coney Island",
      intermediate_stops: ["Park Pl", "Church Av"],
      minutes_until_train_arrives: 5,
    },
    {
      type: "SUBWAY",
      route_id: "2",
      direction: "",
    },
  ]);
  assert.deepEqual(details[0], {
    kind: "board",
    routeId: "Q",
    mode: "subway",
    title: "Board the Q train",
    subtitle: "Downtown-bound",
  });
  assert.deepEqual(details[1], {
    kind: "ride",
    routeId: "Q",
    mode: "subway",
    title: "Ride the Q",
    fromStop: "Canal St",
    toStop: "Atlantic Av",
    rideMeta: "Ride 1 stop · 7 min",
    transferTo: "B41",
    transferMode: "bus",
    stops: ["DeKalb Av"],
  });
  assert.deepEqual(details[2], {
    kind: "board",
    routeId: "B41",
    mode: "bus",
    title: "Board the B41 bus",
    subtitle: "Toward Coney Island",
    note: "Departs in 5 min",
    live: true,
  });
  assert.equal(details[3].rideMeta, "Ride 3 stops");
  assert.deepEqual(details.at(-1), {
    kind: "ride",
    routeId: "2",
    mode: "subway",
    title: "Ride the 2",
    fromStop: undefined,
    toStop: undefined,
    rideMeta: "Ride",
    transferTo: undefined,
    transferMode: undefined,
  });
});

test("direct canonical legs decorate walk, ride, and fallback stop details", () => {
  const details = detailStepsFromCanonicalItinerary(
    [
      { type: "WALK", arrival_stop: "Canal St" },
      {
        type: "SUBWAY",
        route_id: "A",
        departure_stop: "Canal St",
        arrival_stop: "Fulton St",
      },
    ],
    {
      legs: [
        { mode: "WALK", walk_seconds: 125 },
        {
          mode: "SUBWAY",
          ride_seconds: 480,
          stops: [
            { name: "Canal St" },
            { name: "Chambers St" },
            { name: "Fulton St" },
          ],
        },
      ],
    },
  );
  assert.equal(details[0].subtitle, "About 2 min");
  assert.equal(details[2].rideMeta, "Ride 2 stops · 8 min");
  assert.deepEqual(details[2].stops, ["Chambers St"]);
  assert.deepEqual(
    detailStepsFromCanonicalItinerary(
      [{ type: "WALK", arrival_stop: "Home", duration_minutes: 6 }],
      { segments: [], legs: [] },
    ),
    [
      {
        kind: "walk",
        title: "Walk to destination",
        subtitle: "About 6 min",
      },
    ],
  );
});

test("segmented canonical details use leg timing and both dwell labels", () => {
  const details = detailStepsFromCanonicalItinerary(
    [
      { type: "BUS", route_id: "M14", segment_index: 2, duration_minutes: 9 },
      { type: "WALK", arrival_stop: "Museum", segment_index: 1 },
      { type: "SUBWAY", route_id: "L", segment_index: 1, duration_minutes: 11 },
    ],
    {
      segments: [
        {
          segment_index: 2,
          destination: { label: "Home" },
          legs: [{ mode: "BUS", ride_seconds: 240 }],
        },
        {
          segment_index: 1,
          destination: { label: "Museum" },
          legs: [{ mode: "WALK", walk_seconds: 180 }],
        },
      ],
      dwell_events: [
        {
          event_type: "dwell",
          after_segment_index: 1,
          waypoint: { label: "Museum" },
          duration_seconds: 310,
          source: "default",
        },
        {
          event_type: "dwell",
          after_segment_index: 2,
          waypoint: { label: "Home" },
          duration_seconds: 121,
          source: "user",
        },
      ],
    },
  );
  assert.equal(details[0].title, "Leg 1 · To Museum");
  assert.equal(details[1].subtitle, "About 3 min");
  assert.equal(details[3].rideMeta, "Ride · 11 min");
  assert.deepEqual(details[4], {
    kind: "dwell",
    title: "Museum",
    subtitle: "5 min stop · Default dwell time",
  });
  assert.equal(details[7].rideMeta, "Ride · 4 min");
  assert.deepEqual(details[8], {
    kind: "dwell",
    title: "Home",
    subtitle: "2 min stop · Your planned dwell time",
  });
  assert.equal(
    detailStepsFromCanonicalItinerary(
      [],
      {
        segments: [{ segment_index: 0, destination: {}, legs: [] }],
        dwell_events: "not-an-array",
      },
    )[0].title,
    "Leg 1 · To Destination",
  );
});

test("candidate clocks, canonical facts, and deltas handle edge values", () => {
  assert.equal(clockFromIso(""), null);
  assert.equal(clockFromIso("not-a-date"), null);
  assert.equal(clockFromIso("2026-09-01T12:34:00Z"), "8:34 AM");
  assert.equal(canonicalDurationMinutes({ itinerary: { total_duration_seconds: 125 } }), 2);
  assert.equal(canonicalDurationMinutes({ total_minutes: 12.6 }), 13);
  assert.equal(
    canonicalDurationMinutes({ itinerary: { total_duration_seconds: -1 }, total_minutes: 7 }),
    7,
  );
  assert.equal(
    canonicalDurationMinutes({
      itinerary: { total_duration_seconds: Number.NaN },
      total_minutes: -2,
    }),
    null,
  );
  assert.equal(canonicalTransferCount({ itinerary: { transfer_count: 1.6 } }), 2);
  assert.equal(canonicalTransferCount({ itinerary: { transfer_count: -1 } }), undefined);
  assert.equal(canonicalDurationMinutes({ total_minutes: 14 }), 14);
  assert.deepEqual(candidateDelta({}, { total_minutes: 10 }), { delta: "n/a", sev: "low" });
  assert.deepEqual(candidateDelta({ total_minutes: 10 }, { total_minutes: 10 }), { delta: "same time", sev: "low" });
  assert.deepEqual(candidateDelta({ total_minutes: 12 }, { total_minutes: 10 }), { delta: "+2 min", sev: "low" });
  assert.deepEqual(candidateDelta({ total_minutes: 6 }, { total_minutes: 10 }), { delta: "-4 min", sev: "medium" });
  assert.deepEqual(candidateDelta({ total_minutes: 19 }, { total_minutes: 10 }), { delta: "+9 min", sev: "high" });
});

test("candidate route identity ignores walks and de-duplicates transit lines", () => {
  const steps = [
    { type: "WALK" },
    {
      type: "SUBWAY",
      train_line: "q",
      departure_stop: "Canal",
      arrival_stop: "Union",
    },
    { type: "BUS", route_id: "Q" },
    { type: "BUS", route_id: "m15" },
  ];
  assert.equal(firstTransitStep(undefined), undefined);
  assert.deepEqual(firstTransitStep(steps), steps[1]);
  assert.equal(candidateSignature({ steps: [{ type: "WALK" }] }), "walk-only");
  assert.equal(
    candidateSignature({ steps: [steps[1]] }),
    "SUBWAY:Q:Canal:Union",
  );
  assert.deepEqual(transitRouteIdsFromSteps(steps), ["Q", "M15"]);
});

test("alternatives omit active and duplicate routes while retaining later departures", () => {
  const active = {
    id: "active",
    total_minutes: 20,
    steps: [
      {
        type: "SUBWAY",
        route_id: "A",
        departure_stop: "Canal St",
        arrival_stop: "Fulton St",
      },
    ],
  };
  const sameTime = { ...active, id: "same-time" };
  const noTime = { ...active, id: "no-time", total_minutes: undefined };
  const later = {
    ...active,
    id: "later",
    total_minutes: 25,
    itinerary: {
      departure_at: "2026-09-01T12:34:00Z",
      arrival_at: "2026-09-01T12:59:00Z",
    },
  };
  const duplicateLater = { ...later, id: "duplicate-later", total_minutes: 28 };
  const distinct = {
    id: "distinct",
    total_minutes: 24,
    arrival_at: "2026-09-01T13:10:00Z",
    rejection_reason: "More walking under current service conditions.",
    steps: [
      { type: "WALK", duration_minutes: 2 },
      {
        type: "BUS",
        train_line: "m15",
        direction: "South Ferry",
        departure_stop: "Houston St",
        arrival_stop: "Water St",
        minutes_until_train_arrives: 2.4,
      },
    ],
  };
  assert.deepEqual(buildAlternatives(undefined, active), []);
  assert.deepEqual(buildAlternatives([], active), []);
  assert.deepEqual(buildAlternatives([active], undefined), []);
  const alternatives = buildAlternatives(
    [active, sameTime, noTime, later, duplicateLater, distinct],
    active,
  );
  assert.deepEqual(alternatives.map((alternative) => alternative.id), [
    "later",
    "distinct",
  ]);
  assert.equal(alternatives[0].line, "A");
  assert.equal(alternatives[0].dest, "Later departure");
  assert.equal(alternatives[0].delta, "+5 min");
  assert.equal(alternatives[0].reason, "Later departure");
  assert.equal(alternatives[0].leavesLabel, "8:34 AM");
  assert.equal(alternatives[0].arriveLabel, "8:59 AM");
  assert.deepEqual(alternatives[1].lines, ["M15"]);
  assert.equal(alternatives[1].line, "M15");
  assert.equal(alternatives[1].dest, "South Ferry");
  assert.equal(alternatives[1].reason, "More walking");
  assert.equal(alternatives[1].departsInMinutes, 2);
  assert.equal(alternatives[1].fromStop, "Houston St");
  assert.equal(alternatives[1].toStop, "Water St");
});

test("alternate reason labels remove implementation language and clip long copy", () => {
  assert.equal(trimReason(" About 12 minutes under current conditions. "), "12 min current conditions.");
  assert.equal(normalizeAlternateReason(undefined), "");
  assert.equal(normalizeAlternateReason("Route #2 is slower"), "");
  assert.equal(normalizeAlternateReason("Same route departing later"), "Later departure");
  assert.equal(normalizeAlternateReason("Departing later than the best trip"), "Later departure");
  assert.equal(normalizeAlternateReason("Includes more walking"), "More walking");
  assert.equal(normalizeAlternateReason("This alternative avoids multiple service disruptions downtown"), "This alternative avoids multiple service...");
});
