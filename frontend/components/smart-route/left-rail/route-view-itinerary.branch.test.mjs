import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  RecommendedRouteCard,
  RouteDirections,
  RouteStepStrip,
} from "./route-view-itinerary.tsx";

function plan(overrides = {}) {
  return {
    headline: "Take the A",
    rationale: "",
    eta: "5:30 PM",
    totalTime: "47 min",
    pickedLine: "A",
    steps: [],
    alternatives: [],
    notes: [],
    ...overrides,
  };
}

function cardHtml(planOverrides = {}, candidate = {}, extra = {}) {
  return renderToStaticMarkup(
    createElement(RecommendedRouteCard, {
      candidate,
      plan: plan(planOverrides),
      ...extra,
    }),
  );
}

function directionsHtml(planOverrides = {}, destination) {
  return renderToStaticMarkup(
    createElement(RouteDirections, {
      plan: plan(planOverrides),
      destination,
    }),
  );
}

test("missing leave-by label omits leave-by copy", () => {
  const html = cardHtml();
  assert.doesNotMatch(html, /Leave now/);
  assert.doesNotMatch(html, /Leave by /);
});

test("leave-by now tells the rider to leave now", () => {
  const html = cardHtml({ leaveByLabel: "now" });
  assert.match(html, /Leave now/);
});

test("clock leave-by copy names the clock time", () => {
  const html = cardHtml({ leaveByLabel: "4:43 PM" });
  assert.match(html, /Leave by 4:43 PM/);
});

test("Live eta is not shown as an arrival clock", () => {
  const html = cardHtml({ eta: "Live" });
  assert.doesNotMatch(html, /arrival/);
});

test("clock eta is labeled as arrival", () => {
  const html = cardHtml({ eta: "5:30 PM" });
  assert.match(html, /5:30 PM arrival/);
});

test("due departure says the next train is now", () => {
  const html = cardHtml({ nextDepartureMinutes: 0, pickedLine: "" });
  assert.match(html, /Next train now/);
});

test("future departure names the line and wait", () => {
  const html = cardHtml({ nextDepartureMinutes: 6, pickedLine: "a" });
  assert.match(html, /Next A/);
  assert.match(html, / in /);
});

test("non-finite next departure is omitted", () => {
  const html = cardHtml({ nextDepartureMinutes: Number.NaN });
  assert.doesNotMatch(html, /Next /);
});

test("Details stays closed when there are no detail steps", () => {
  const missing = cardHtml();
  const empty = cardHtml({ detailSteps: [] });
  assert.doesNotMatch(missing, /Details/);
  assert.doesNotMatch(empty, /Details/);
});

test("Details toggle stays collapsed when steps exist", () => {
  const html = cardHtml({
    detailSteps: [{ kind: "walk", title: "Walk to Jay St" }],
  });
  assert.match(html, /Details/);
  assert.match(html, /data-open="false"/);
  assert.match(html, /aria-expanded="false"/);
});

test("canonical transfer count uses the singular label", () => {
  const html = cardHtml({ transferCount: 1 });
  assert.match(html, /1 transfer/);
  assert.doesNotMatch(html, /1 transfers/);
});

test("missing plan transfer count falls back to the candidate count", () => {
  const html = cardHtml({}, { transfers: 2 });
  assert.match(html, /2 transfers/);
});

test("missing transfer facts read as zero transfers", () => {
  const html = cardHtml({}, {});
  assert.match(html, /0 transfers/);
});

test("numeric walk minutes join the footer meta", () => {
  const html = cardHtml({ transferCount: 0 }, { walkMinutes: 8 });
  assert.match(html, /8 min walk/);
});

test("non-numeric walk minutes stay out of the footer", () => {
  const html = cardHtml({ transferCount: 0 }, { walkMinutes: "far" });
  assert.doesNotMatch(html, /min walk/);
});

test("rationale without train context still mounts the reasoning region", () => {
  const html = cardHtml({ rationale: "There are 6 people on the platform." });
  assert.match(html, /sr-ai-reasoning/);
});

test("empty rationale omits the reasoning region", () => {
  const html = cardHtml({ rationale: "" });
  assert.doesNotMatch(html, /sr-ai-reasoning/);
});

test("journey places render only when more than two names exist", () => {
  const few = cardHtml({ journeyPlaces: ["Home", "Work"] });
  const many = cardHtml({ journeyPlaces: ["Home", "Jay St", "Work"] });
  assert.doesNotMatch(few, /aria-label="Journey stops"/);
  assert.match(many, /Home \u2192 Jay St \u2192 Work/);
});

test("an alternate pick is labeled Selected", () => {
  const html = cardHtml({ isAlternativeRoute: true });
  assert.match(html, />Selected</);
  assert.doesNotMatch(html, />Recommended</);
});

test("the winning pick is labeled Recommended", () => {
  const html = cardHtml({ isAlternativeRoute: false });
  assert.match(html, />Recommended</);
});

test("a walk strip without minutes is titled Walk", () => {
  const html = renderToStaticMarkup(
    createElement(RouteStepStrip, { segments: [{ kind: "walk" }] }),
  );
  assert.match(html, /title="Walk"/);
});

test("a walk strip with minutes names the duration", () => {
  const html = renderToStaticMarkup(
    createElement(RouteStepStrip, {
      segments: [{ kind: "walk", minutes: 3 }],
    }),
  );
  assert.match(html, /title="Walk 3 min"/);
});

test("a bus strip uses the bus chip", () => {
  const html = renderToStaticMarkup(
    createElement(RouteStepStrip, {
      segments: [
        { kind: "walk" },
        { kind: "ride", routeId: "B41", mode: "bus" },
      ],
    }),
  );
  assert.match(html, /B41 bus/);
});

test("a subway strip uses the train bullet", () => {
  const html = renderToStaticMarkup(
    createElement(RouteStepStrip, {
      segments: [{ kind: "ride", routeId: "A", mode: "subway" }],
    }),
  );
  assert.match(html, /A train/);
});

test("empty directions still start and arrive", () => {
  const html = directionsHtml();
  assert.match(html, />Start</);
  assert.match(html, /Your location/);
  assert.match(html, />Arrive</);
});

test("arrive names a trimmed destination", () => {
  const html = directionsHtml({}, "  Wall St  ");
  assert.match(html, /Wall St/);
});

test("a blank destination is omitted from arrive", () => {
  const html = directionsHtml({}, "   ");
  assert.match(html, />Arrive</);
  assert.doesNotMatch(html, /<small>\s*<\/small>/);
});

test("a walk row keeps its title and optional note without a live mark", () => {
  const html = directionsHtml({
    detailSteps: [
      {
        kind: "walk",
        title: "Walk to Jay St",
        subtitle: "3 min",
        note: "Use the north stair",
      },
    ],
  });
  assert.match(html, /Walk to Jay St/);
  assert.match(html, /3 min/);
  assert.match(html, /Use the north stair/);
  assert.doesNotMatch(html, /aria-label="Live arrival prediction"/);
});

test("an exit row is a generic titled step", () => {
  const html = directionsHtml({
    detailSteps: [{ kind: "exit", title: "Exit at Fulton St" }],
  });
  assert.match(html, /Exit at Fulton St/);
});

test("a subway board row with live note shows the live prediction", () => {
  const html = directionsHtml({
    detailSteps: [
      {
        kind: "board",
        title: "Board A",
        routeId: "A",
        mode: "subway",
        note: "Departs in 6 min",
        live: true,
      },
    ],
  });
  assert.match(html, /Board A/);
  assert.match(html, /Departs in 6 min/);
  assert.match(html, /aria-label="Live arrival prediction"/);
});

test("a bus board row uses the bus chip", () => {
  const html = directionsHtml({
    detailSteps: [
      { kind: "board", title: "Board B41", routeId: "B41", mode: "bus" },
    ],
  });
  assert.match(html, /B41 bus/);
  assert.match(html, /Board B41/);
});

test("a board row without a route id uses the walk glyph", () => {
  const html = directionsHtml({
    detailSteps: [{ kind: "board", title: "Board when it arrives" }],
  });
  assert.match(html, /Board when it arrives/);
  assert.doesNotMatch(html, /sr-detail-step__vehicle/);
});

test("collapsed transit keeps ride meta closed and hides intermediate stops", () => {
  const html = directionsHtml({
    detailSteps: [
      {
        kind: "ride",
        title: "Ride the A",
        routeId: "A",
        mode: "subway",
        fromStop: "Jay St",
        toStop: "Fulton St",
        rideMeta: "3 stops",
        stops: ["High St", "Broadway-Nassau"],
      },
    ],
  });
  assert.match(html, /Jay St/);
  assert.match(html, /Fulton St/);
  assert.match(html, /3 stops/);
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /High St/);
  assert.doesNotMatch(html, /Broadway-Nassau/);
});

test("a ride without meta has no disclosure", () => {
  const html = directionsHtml({
    detailSteps: [
      {
        kind: "ride",
        title: "Ride the A",
        routeId: "A",
        fromStop: "Jay St",
        toStop: "Fulton St",
      },
    ],
  });
  assert.doesNotMatch(html, /sr-detail-ride__meta/);
  assert.doesNotMatch(html, /sr-detail-ride__disclosure/);
});

test("ride meta without stops is static text", () => {
  const html = directionsHtml({
    detailSteps: [
      {
        kind: "ride",
        title: "Ride the A",
        routeId: "A",
        rideMeta: "Skip-stop",
        stops: [],
      },
    ],
  });
  assert.match(html, /Skip-stop/);
  assert.doesNotMatch(html, /sr-detail-ride__disclosure/);
});

test("a missing ride label pair still renders the ride row", () => {
  const html = directionsHtml({
    detailSteps: [{ kind: "ride", title: "Ride", routeId: "ZZ", rideMeta: "1 stop" }],
  });
  assert.match(html, /1 stop/);
  assert.doesNotMatch(html, /Jay St/);
  assert.doesNotMatch(html, /Fulton St/);
});

test("a bus ride uses the bus connector color", () => {
  const html = directionsHtml({
    detailSteps: [{ kind: "ride", title: "Ride B41", routeId: "B41", mode: "bus" }],
  });
  assert.match(html, /background:#38445c/);
});

test("an unknown subway line uses the fallback rule color", () => {
  const html = directionsHtml({
    detailSteps: [{ kind: "ride", title: "Ride ZZ", routeId: "ZZ", mode: "subway" }],
  });
  assert.match(html, /background:var\(--sr-rule-bright\)/);
});

test("a bus transfer names the bus", () => {
  const html = directionsHtml({
    detailSteps: [
      {
        kind: "ride",
        title: "Ride the A",
        routeId: "A",
        transferTo: "B41",
        transferMode: "bus",
      },
    ],
  });
  assert.match(html, /Transfer to the/);
  assert.match(html, /B41 bus/);
  assert.match(html, /bus/);
});

test("a subway transfer names the train", () => {
  const html = directionsHtml({
    detailSteps: [
      {
        kind: "ride",
        title: "Ride the A",
        routeId: "A",
        transferTo: "4",
        transferMode: "subway",
      },
    ],
  });
  assert.match(html, /Transfer to the/);
  assert.match(html, /4 train/);
  assert.match(html, /train/);
});

test("a segment row shows only its title", () => {
  const html = directionsHtml({
    detailSteps: [{ kind: "segment", title: "Toward Fulton St" }],
  });
  assert.match(html, /sr-detail-step--segment/);
  assert.match(html, /Toward Fulton St/);
});

test("a dwell row includes its wait subtitle", () => {
  const html = directionsHtml({
    detailSteps: [
      { kind: "dwell", title: "Wait at Fulton St", subtitle: "2 min transfer dwell" },
    ],
  });
  assert.match(html, /Wait at Fulton St/);
  assert.match(html, /2 min transfer dwell/);
});

test("a dwell row without a subtitle keeps the title only", () => {
  const html = directionsHtml({
    detailSteps: [{ kind: "dwell", title: "Wait at Fulton St" }],
  });
  assert.match(html, /Wait at Fulton St/);
  assert.doesNotMatch(html, /2 min transfer dwell/);
});
