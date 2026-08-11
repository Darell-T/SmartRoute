import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { RouteView } from "./route-view.tsx";
import { recommendedCandidateFromPlan } from "./route-display-compat.ts";

const ROOT = path.resolve(import.meta.dirname, "../../..");

const canonicalPlan = {
  headline: "Take the A to Fulton St",
  rationale: "The A has the fastest verified arrival.",
  headsign: "Far Rockaway-Mott Av",
  eta: "5:30 PM",
  totalTime: "47 min",
  leaveByLabel: "4:43 PM",
  nextDepartureMinutes: 6,
  transferCount: 2,
  journeyPlaces: ["Fort Greene", "Jay St-MetroTech", "Fulton St", "Wall St"],
  pickedLine: "A",
  strip: [
    { kind: "walk", minutes: 3 },
    { kind: "ride", routeId: "A", mode: "subway" },
    { kind: "ride", routeId: "4", mode: "subway" },
    { kind: "walk", minutes: 4 },
  ],
  detailSteps: [
    { kind: "walk", title: "Walk to Jay St-MetroTech", subtitle: "3 min" },
    {
      kind: "board",
      title: "Board A toward Far Rockaway-Mott Av",
      routeId: "A",
      mode: "subway",
      note: "Departs in 6 min",
      live: true,
    },
    {
      kind: "ride",
      title: "Ride the A",
      routeId: "A",
      mode: "subway",
      fromStop: "Jay St-MetroTech",
      toStop: "Fulton St",
      rideMeta: "3 stops",
      transferTo: "4",
      transferMode: "subway",
    },
    {
      kind: "dwell",
      title: "Wait at Fulton St",
      subtitle: "2 min transfer dwell",
    },
    { kind: "walk", title: "Walk to Wall St", subtitle: "4 min" },
  ],
  steps: [
    { type: "walk", action: "Walk", title: "Walk", detail: "3 min", duration: "3 min" },
    { type: "board", action: "Board", line: "A", title: "Board A", detail: "", duration: "0 min" },
  ],
  alternatives: [
    {
      id: "candidate-4",
      line: "4",
      dest: "Wall St",
      delta: "+4 min",
      reason: "Less reliable due to a service alert.",
      sev: "medium",
      totalMinutes: 51,
      leavesLabel: "4:51 PM",
      fromStop: "Borough Hall",
      toStop: "Wall St",
      strip: [{ kind: "ride", routeId: "4", mode: "subway" }],
    },
  ],
  notes: [],
};

test("route result renders server-owned canonical timing, route chain, and detail semantics", () => {
  const markup = renderRouteView({ plan: canonicalPlan });

  assert.match(markup, /47 min/);
  assert.match(markup, /5:30 PM arrival/);
  assert.match(markup, /Leave by 4:43 PM/);
  assert.match(markup, /2 transfers/);
  assert.match(markup, /Next A/);
  assert.match(
    markup,
    /Fort Greene \u2192 Jay St-MetroTech \u2192 Fulton St \u2192 Wall St/,
  );
  assert.match(markup, /aria-label="Route sequence"/);
  assert.match(markup, /aria-label="Route directions"/);
  assert.match(markup, /Wait at Fulton St/);
  assert.match(markup, /3 stops/);
  assert.match(markup, /data-open="false"/);
  assert.match(markup, /aria-expanded="false"/);
  assert.match(markup, /Other routes/);
  assert.match(markup, /aria-label="Alternate routes"/);
  assert.match(markup, /Use this route instead: Borough Hall \u2192 Wall St/);
});

test("route result excludes alternatives when the active canonical candidate has none", () => {
  const markup = renderRouteView({
    plan: { ...canonicalPlan, alternatives: [] },
  });

  assert.doesNotMatch(markup, /Other routes/);
  assert.doesNotMatch(markup, /candidate-4/);
});

test("chat Open on map handoff renders directions only without replanning UI", () => {
  const markup = renderRouteView({
    plan: { ...canonicalPlan, entryContext: "chat" },
  });

  assert.match(markup, /aria-label="Route directions"/);
  assert.match(markup, /Wait at Fulton St/);
  assert.match(markup, /Wall St/);
  assert.doesNotMatch(markup, /Finding routes/);
  assert.doesNotMatch(markup, /The A has the fastest verified arrival/);
  assert.doesNotMatch(markup, /Recommended/);
  assert.doesNotMatch(markup, /Other routes/);
  assert.doesNotMatch(markup, /aria-expanded="false"/);
});

test("canonical transfer count wins while legacy display fallback remains stable", () => {
  const fallback = recommendedCandidateFromPlan(canonicalPlan);
  assert.deepEqual(fallback, { walkMinutes: 3, transfers: 0 });

  const markup = renderRouteView({ plan: canonicalPlan });
  assert.match(markup, /2 transfers/);
  assert.doesNotMatch(markup, /0 transfers/);
});

test("standby route view preserves nearby direction controls and live arrival markup", () => {
  const markup = renderRouteView({
    routeStatus: "standby",
    plan: { ...canonicalPlan, alternatives: [] },
    nearbyTransitGroups: [
      {
        id: "jay-st",
        name: "Jay St-MetroTech",
        mode: "subway",
        routeIds: ["A", "C"],
        walkMinutes: 3,
        distanceMiles: 0.2,
        arrivals: [
          {
            id: "a-uptown",
            mode: "subway",
            routeIds: ["A"],
            destination: "Inwood-207 St",
            arrivalMinutes: [6],
            direction: "uptown",
            predictionType: "live",
            predictionFreshness: "fresh",
          },
        ],
      },
    ],
  });

  assert.match(markup, /aria-label="Arrival direction"/);
  assert.match(markup, /role="radio"/);
  assert.match(markup, /aria-checked="true"/);
  assert.match(markup, /Jay St-MetroTech/);
  assert.match(markup, /Inwood-207 St/);
  assert.match(markup, /aria-label="Live arrival prediction"/);
});

test("route view keeps explicit controls and motion-safe state on the intended elements", () => {
  const source = routeViewSource();

  assert.match(source, /className="sr-details-toggle"[\s\S]*aria-expanded={detailsOpen}/);
  assert.match(source, /className="sr-alternates__trigger"[\s\S]*aria-expanded={open}/);
  assert.match(source, /onSelectAlternative\?\.\(alternative\.id!\)/);
  assert.match(source, /onRequestRailExpand\?\.\(\);[\s\S]*onWayChange/);
  assert.match(source, /shouldReduceMotion \? "auto" : "smooth"/);
});

function routeViewSource() {
  return [
    "route-view.tsx",
    "route-view-actions.tsx",
    "route-view-alternatives.tsx",
    "route-view-itinerary.tsx",
    "route-view-nearby.tsx",
    "route-view-state.tsx",
  ]
    .map((file) =>
      fs.readFileSync(
        path.join(ROOT, "components/smart-route/left-rail", file),
        "utf8",
      ),
    )
    .join("\n");
}

function renderRouteView({
  routeStatus = "result",
  plan,
  nearbyTransitGroups = [],
} = {}) {
  return renderToStaticMarkup(
    createElement(RouteView, {
      station: { name: "Jay St-MetroTech", walk: "3 min walk", dist: "0.2 mi", updatedSec: 0 },
      health: {
        status: "clear",
        alerts: 0,
        lines: 0,
        major: 0,
        stale: 0,
        summary: "Good service",
        affected: [],
      },
      arrivals: [],
      nearbyTransitGroups,
      nearbyBusArrivals: [],
      alerts: [],
      incidents: [],
      plan,
      way: "uptown",
      onWayChange: () => {},
      routeStatus,
      onRouteStatusChange: () => {},
      onSelectAlternative: () => {},
    }),
  );
}
