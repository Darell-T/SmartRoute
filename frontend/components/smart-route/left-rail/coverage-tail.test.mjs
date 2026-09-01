import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { HomeNearYou } from "../chat/home-near-you.tsx";
import { AlertRouteBadgeGroup } from "./alert-badges.tsx";
import { AlertCard } from "./alert-featured-card.tsx";

function nearby(overrides = {}) {
  return {
    locationState: "precise_nyc",
    locationLabel: "Near you",
    locationNotice: null,
    stationName: "Canal St",
    arrivals: [
      { id: "q", routeId: "Q", destination: "Coney Island", minutes: [4] },
    ],
    arrivalsState: "ready",
    condition: { state: "clear", label: "No active service changes nearby" },
    issue: null,
    ...overrides,
  };
}

function alertItem(overrides = {}) {
  return {
    id: "alert-1",
    routeIds: ["Q"],
    serviceName: "Broadway Express",
    title: "Q trains skip DeKalb",
    summary: "Trains bypass DeKalb Av.",
    timestampLabel: "12m",
    severity: "minor",
    lifecycle: "active",
    statusLabel: "Skip-stop",
    source: "mta",
    expandable: true,
    details: {
      currentStatus: "Q trains skip DeKalb Av.",
      impact: "Walk to Atlantic Av.",
      alternatives: "Take the B or D instead.",
    },
    ...overrides,
  };
}

test("mixed subway and bus badges share one group without an explicit size", () => {
  const html = renderToStaticMarkup(
    createElement(AlertRouteBadgeGroup, { routeIds: ["Q", "B41"] }),
  );
  assert.match(html, /title="Q train"|aria-label="Q train"/);
  assert.match(html, /B41/);
});

test("home nearby exposes an alert condition and issue row", () => {
  const html = renderToStaticMarkup(
    createElement(HomeNearYou, {
      model: nearby({
        condition: { state: "alert", label: "Q delays nearby" },
        issue: { label: "Signal trouble at Canal", confidence: "high" },
      }),
      onOpenLiveMap() {},
    }),
  );
  assert.match(html, /data-has-issue="true"/);
  assert.match(html, /role="status"/);
  assert.match(html, /Signal trouble at Canal/);
});

test("featured alert cards cover systemwide, stacked routes, advice, and clocks", () => {
  const systemwide = renderToStaticMarkup(
    createElement(AlertCard, {
      item: alertItem({
        routeIds: ["A", "C", "E", "B", "D", "F", "M", "G"],
        serviceName: "Subway",
        statusLabel: "",
      }),
    }),
  );
  assert.match(systemwide, /Systemwide/);
  assert.doesNotMatch(systemwide, /sr-alert-status/);

  const stacked = renderToStaticMarkup(
    createElement(AlertCard, {
      item: alertItem({ routeIds: ["Q", "B", "N"] }),
    }),
  );
  assert.match(stacked, /Broadway Express/);
  assert.match(stacked, /Take the B or D instead/);
  assert.match(stacked, /Updated 12 min ago/);
});
