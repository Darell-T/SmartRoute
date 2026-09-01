import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ItineraryLeg, JourneyTitle } from "./itinerary-card-legs.tsx";

function event(kind, extra = {}) {
  return {
    id: kind,
    kind,
    routeIds: extra.routeIds ?? ["Q"],
    title: extra.title ?? kind,
    fromLabel: extra.fromLabel ?? "Jay St",
    toLabel: extra.toLabel ?? "Canal St",
    durationLabel: "4 min",
    stopCount: extra.stopCount ?? 3,
    stops: extra.stops,
    subtitle: extra.subtitle,
    sourceLabel: extra.sourceLabel,
  };
}

test("journey title joins waypoint names", () => {
  const html = renderToStaticMarkup(
    createElement(JourneyTitle, { id: "t", names: ["Jay St", "Canal St"] }),
  );
  assert.match(html, /Jay St to Canal St/);
});

test("itinerary legs render subway, bus, rail, walk, wait, transfer, and waypoint", () => {
  const subway = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("subway", { stops: ["DeKalb", "Pacific"] }),
      expanded: true,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(subway, /subway leg/);
  assert.match(subway, /Ride 3 stops, 4 min/);
  const bus = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("bus", { routeIds: ["B54"], stopCount: 1 }),
      expanded: false,
      onToggle() {},
      reduceMotion: false,
    }),
  );
  assert.match(bus, /B54/);
  const rail = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("rail", { routeIds: ["LIRR"], stops: [] }),
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(rail, /rail leg/);
  const walk = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("walk"),
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(walk, /Walking directions/);
  const wait = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("wait", { subtitle: "Jay St" }),
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(wait, /Wait before boarding/);
  const transfer = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("transfer", { subtitle: "Same station", routeIds: [] }),
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(transfer, /Same station/);
  const emptyGlyph = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("subway", { routeIds: [""], stopCount: 1 }),
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(emptyGlyph, /subway leg/);
  const waypoint = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("waypoint", { subtitle: "Shop", sourceLabel: "Place" }),
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(waypoint, /Planned stop/);
  const dest = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("destination"),
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.equal(dest, "");
});

test("transit legs fall back when stop count, labels, or route ids are missing", () => {
  const noStops = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: {
        id: "subway",
        kind: "subway",
        routeIds: ["Q"],
        title: "Q ride",
        durationLabel: "4 min",
      },
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(noStops, /Board/);
  assert.match(noStops, /Q ride/);
  assert.match(noStops, /Ride 4 min/);
  const busNoId = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("bus", { routeIds: [], stopCount: 2 }),
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(busNoId, /bus leg/);
  assert.doesNotMatch(busNoId, /sr-itinerary-card__bus-route/);
  const looksLikeBus = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("subway", { routeIds: ["B54"] }),
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(looksLikeBus, /B54|subway leg/);
  const expandedStops = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: event("subway", { stops: ["DeKalb", "Pacific"], stopCount: 2 }),
      expanded: true,
      onToggle() {},
      reduceMotion: false,
    }),
  );
  assert.match(expandedStops, /DeKalb/);
  assert.match(expandedStops, /aria-expanded="true"/);
});

test("walk wait transfer and pickup keep fallback copy without optional fields", () => {
  const walk = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: {
        id: "walk",
        kind: "walk",
        routeIds: [],
        title: "On foot",
      },
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(walk, />Walk</);
  assert.match(walk, /On foot/);
  assert.doesNotMatch(walk, /sr-itinerary-card__walk-duration/);
  const wait = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: { id: "wait", kind: "wait", routeIds: ["Q"], title: "Wait for Q" },
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(wait, /Wait for Q/);
  assert.doesNotMatch(wait, / at /);
  const transfer = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: { id: "xfer", kind: "transfer", routeIds: [], title: "Transfer" },
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(transfer, /Transfer/);
  assert.doesNotMatch(transfer, /sr-itinerary-card__walk-meta/);
  const pickup = renderToStaticMarkup(
    createElement(ItineraryLeg, {
      event: { id: "p", kind: "pickup", routeIds: [], title: "Pickup" },
      expanded: false,
      onToggle() {},
      reduceMotion: true,
    }),
  );
  assert.match(pickup, /Planned stop/);
  assert.match(pickup, /Pickup/);
});

test("journey title omits the arrow on a single place name", () => {
  const html = renderToStaticMarkup(
    createElement(JourneyTitle, { id: "t", names: ["Canal St"] }),
  );
  assert.match(html, /Canal St/);
  assert.doesNotMatch(html, /sr-itinerary-card__title-arrow/);
});
