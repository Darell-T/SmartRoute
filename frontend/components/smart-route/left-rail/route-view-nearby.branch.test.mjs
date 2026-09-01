import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { NearbyTransitPanel, PredictionStatus } from "./route-view-nearby.tsx";

const station = {
  name: "Jay St-MetroTech",
  walk: "3 min walk",
  dist: "0.2 mi",
  updatedSec: 0,
};

function grouped(overrides = {}) {
  return {
    id: "a-up",
    mode: "subway",
    routeIds: ["A"],
    destination: "Inwood-207 St",
    arrivalMinutes: [6],
    direction: "uptown",
    ...overrides,
  };
}

function group(overrides = {}) {
  return {
    id: "jay",
    name: "Jay St-MetroTech",
    mode: "subway",
    routeIds: ["A", "C"],
    arrivals: [grouped()],
    ...overrides,
  };
}

function busArrival(overrides = {}) {
  return {
    id: "b41-1",
    mode: "bus",
    routeIds: ["B41"],
    line: "B41",
    destination: "Downtown Brooklyn",
    dest: "Downtown Brooklyn",
    arrivalMinutes: [4],
    direction: "unknown",
    way: "unknown",
    label: "4 min",
    mins: 4,
    status: "On Time",
    stale: false,
    ...overrides,
  };
}

function panel(overrides = {}) {
  return renderToStaticMarkup(
    createElement(NearbyTransitPanel, {
      station,
      arrivals: [],
      nearbyTransitGroups: [],
      nearbyBusArrivals: [],
      way: "uptown",
      onWayChange() {},
      ...overrides,
    }),
  );
}

function status(props) {
  return renderToStaticMarkup(createElement(PredictionStatus, props));
}

test("empty uptown groups tell the rider to try Downtown", () => {
  const html = panel({ way: "uptown" });
  assert.match(html, /No uptown subway arrivals nearby/);
  assert.match(html, /Try Downtown or refresh live data./);
});

test("empty downtown groups tell the rider to try Uptown", () => {
  const html = panel({ way: "downtown" });
  assert.match(html, /No downtown subway arrivals nearby/);
  assert.match(html, /Try Uptown or refresh live data./);
});

test("a downtown-only group is empty when Uptown is selected", () => {
  const html = panel({
    way: "uptown",
    nearbyTransitGroups: [
      group({ arrivals: [grouped({ direction: "downtown", destination: "Far Rockaway" })] }),
    ],
  });
  assert.match(html, /No uptown subway arrivals nearby/);
  assert.doesNotMatch(html, /Far Rockaway/);
});

test("unknown-direction arrivals still appear for Downtown", () => {
  const html = panel({
    way: "downtown",
    nearbyTransitGroups: [
      group({
        arrivals: [grouped({ id: "unk", direction: "unknown", destination: "Terminal" })],
      }),
    ],
  });
  assert.match(html, /Terminal/);
  assert.doesNotMatch(html, /No downtown subway arrivals nearby/);
});

test("buses render without the empty subway message", () => {
  const html = panel({
    nearbyBusArrivals: [busArrival()],
  });
  assert.match(html, /Nearby buses/);
  assert.match(html, /Downtown Brooklyn/);
  assert.doesNotMatch(html, /No uptown subway arrivals nearby/);
});

test("a station without walk or distance omits the walk meta", () => {
  const html = panel({ nearbyTransitGroups: [group()] });
  assert.match(html, /Jay St-MetroTech/);
  assert.doesNotMatch(html, /sr-station-header__walk/);
});

test("walk minutes render without miles when distance is missing", () => {
  const html = panel({ nearbyTransitGroups: [group({ walkMinutes: 3 })] });
  assert.match(html, />3 min walk</);
  assert.doesNotMatch(html, /\d+\.\d+ mi/);
});

test("distance miles render without a walk when minutes are missing", () => {
  const html = panel({ nearbyTransitGroups: [group({ distanceMiles: 0.2 })] });
  assert.match(html, /0.2 mi/);
  assert.doesNotMatch(html, /min walk/);
});

test("walk and miles join in the station header", () => {
  const html = panel({
    nearbyTransitGroups: [group({ walkMinutes: 3, distanceMiles: 0.24 })],
  });
  assert.match(html, /3 min walk · 0.2 mi/);
});

test("a subway row without a route id still names the destination", () => {
  const html = panel({
    nearbyTransitGroups: [
      group({ arrivals: [grouped({ routeIds: [], destination: "Inwood-207 St" })] }),
    ],
  });
  assert.match(html, /Inwood-207 St/);
});

test("service pattern and via join under the subway destination", () => {
  const html = panel({
    nearbyTransitGroups: [
      group({
        arrivals: [grouped({ servicePattern: "Express", via: "8 Av" })],
      }),
    ],
  });
  assert.match(html, /Express · 8 Av/);
});

test("a subway row without pattern or via omits the detail line", () => {
  const html = panel({ nearbyTransitGroups: [group()] });
  assert.match(html, /Inwood-207 St/);
  assert.doesNotMatch(html, /<small>/);
});

test("missing subway minutes fall back to Soon", () => {
  const html = panel({
    nearbyTransitGroups: [
      group({ arrivals: [grouped({ arrivalMinutes: [] })] }),
    ],
  });
  assert.match(html, />Soon</);
});

test("a bus row uses the bus chip and walk detail", () => {
  const html = panel({
    nearbyBusArrivals: [
      busArrival({
        servicePattern: "Limited",
        stopName: "Atlantic Av",
        walkMinutes: 2,
      }),
    ],
  });
  assert.match(html, /B41 bus/);
  assert.match(html, /Limited · Atlantic Av · 2 min walk/);
});

test("a subway-shaped bus-list row uses the train bullet", () => {
  const html = panel({
    nearbyBusArrivals: [
      busArrival({
        id: "a-bus-slot",
        mode: "subway",
        routeIds: ["A"],
        line: "A",
        destination: "Inwood-207 St",
        stopName: undefined,
        walkMinutes: undefined,
        servicePattern: undefined,
      }),
    ],
  });
  assert.match(html, /A train/);
  assert.match(html, /Inwood-207 St/);
});

test("a bus row without minutes uses its label fallback", () => {
  const html = panel({
    nearbyBusArrivals: [busArrival({ arrivalMinutes: [], label: "Approaching" })],
  });
  assert.match(html, />Approaching</);
});

test("a bus row without route ids falls back to the line id", () => {
  const html = panel({
    nearbyBusArrivals: [busArrival({ routeIds: [], line: "B54" })],
  });
  assert.match(html, /B54 bus/);
});

test("an alert severity other than none is a warning", () => {
  const html = status({ alertSeverity: "major" });
  assert.match(html, /data-state="warning"/);
  assert.match(html, /aria-label="Affected by service alert"/);
});

test("alert severity none is not a warning", () => {
  const html = status({ alertSeverity: "none", predictionFreshness: "fresh" });
  assert.match(html, /data-state="fresh"/);
  assert.match(html, /aria-label="Live arrival prediction"/);
});

test("a scheduled prediction type is a scheduled estimate", () => {
  const html = status({ predictionType: "scheduled" });
  assert.match(html, /data-state="scheduled"/);
  assert.match(html, /aria-label="Scheduled estimate"/);
});

test("scheduled freshness is a scheduled estimate", () => {
  const html = status({ predictionFreshness: "scheduled" });
  assert.match(html, /data-state="scheduled"/);
  assert.match(html, /aria-label="Scheduled estimate"/);
});

test("stale freshness is an older live prediction", () => {
  const html = status({ predictionFreshness: "stale" });
  assert.match(html, /data-state="stale"/);
  assert.match(html, /aria-label="Older live arrival prediction"/);
});

test("fresh live predictions keep the live label", () => {
  const html = status({ predictionType: "live", predictionFreshness: "fresh" });
  assert.match(html, /data-state="fresh"/);
  assert.match(html, /aria-label="Live arrival prediction"/);
});
