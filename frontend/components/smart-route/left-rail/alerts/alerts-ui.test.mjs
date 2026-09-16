import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AlertEmptyState, AlertLineGroupList } from "./line-list.tsx";
import { AlertRouteBadge, AlertRouteBadgeGroup } from "./badges.tsx";
import { AlertCard } from "./featured-card.tsx";
import { AlertsView } from "./alerts-view.tsx";
import { LeftRail } from "../index.ts";
import {
  groupAlertItemsByLine,
  isSystemwideAlert,
  partitionAlertItems,
} from "./view-model.ts";
import { buildAlertDetailView } from "./detail.tsx";
import { MapMiniControls } from "../../map-mini-controls.tsx";

function alertItem(overrides = {}) {
  return {
    id: "alert-q",
    routeIds: ["Q"],
    serviceName: "Broadway Express",
    title: "Q trains are running with delays",
    summary: "Trains are moving slower than usual.",
    timestampLabel: "now",
    severity: "minor",
    lifecycle: "active",
    statusLabel: "Delays",
    source: "mta",
    expandable: true,
    details: {
      currentStatus: "Trains are delayed.",
      impact: "Expect extra wait time.",
    },
    ...overrides,
  };
}

test("systemwide alerts stay out of featured nearby rows", () => {
  const nearby = alertItem();
  const systemwide = alertItem({
    id: "sys",
    routeIds: ["A", "C", "E", "B", "D", "F", "M", "G"],
    title: "Multiple lines",
  });
  assert.equal(isSystemwideAlert(systemwide), true);
  const { featured, rest } = partitionAlertItems([nearby, systemwide], ["Q"], 2);
  assert.equal(featured[0].id, "alert-q");
  assert.ok(rest.some((item) => item.id === "sys"));
});

test("groupAlertItemsByLine keeps Q on the Broadway family", () => {
  const groups = groupAlertItemsByLine([alertItem()]);
  assert.ok(groups.length >= 1);
  assert.ok(groups[0].items[0].routeIds.includes("Q"));
});

test("alert empty state and mini map controls render accessible copy", () => {
  const empty = renderToStaticMarkup(createElement(AlertEmptyState));
  assert.match(empty, /No active alerts right now/);
  const controls = renderToStaticMarkup(
    createElement(MapMiniControls, { onExpand: () => {}, onRecenter: () => {} }),
  );
  assert.match(controls, /aria-label="Map controls"/);
  assert.match(controls, /Toggle fullscreen/);
  assert.match(controls, /Recenter map/);
});

test("alert badges and featured cards render route identity", () => {
  const badge = renderToStaticMarkup(
    createElement(AlertRouteBadge, { routeId: "Q", size: 22 }),
  );
  assert.match(badge, /Q/);
  const group = renderToStaticMarkup(
    createElement(AlertRouteBadgeGroup, { routeIds: ["Q", "B"], limit: 2, size: 22 }),
  );
  assert.match(group, /Q|B/);
  const card = renderToStaticMarkup(createElement(AlertCard, { item: alertItem() }));
  assert.match(card, /Broadway Express/);
  assert.match(card, /Q trains are running with delays/);
});

test("alert line groups render expandable rows with route identity", () => {
  const groups = groupAlertItemsByLine([
    alertItem(),
    alertItem({
      id: "alert-q-skip",
      title: "Q trains skipping DeKalb",
      expandable: false,
      details: undefined,
    }),
  ]);
  const html = renderToStaticMarkup(
    createElement(AlertLineGroupList, {
      groups,
      "aria-label": "Other service alerts",
    }),
  );
  assert.match(html, /aria-label="Other service alerts"/);
  assert.match(html, /Q trains are running with delays/);
  assert.match(html, /aria-expanded="false"/);
});

test("alerts view shows featured nearby alerts when the feed has them", () => {
  const html = renderToStaticMarkup(
    createElement(AlertsView, {
      alerts: [
        {
          sev: "minor",
          kind: "train",
          lines: ["Q"],
          title: "Q trains are running with delays",
          sub: "Trains are moving slower than usual.",
          startedAgo: "now",
          lastUpdate: "now",
        },
      ],
      feed: [],
      nearbyRouteIds: ["Q"],
    }),
  );
  assert.match(html, /Near you/);
  assert.match(html, /Trains are moving slower than usual/);
});

test("alerts view shows the empty state when nothing is active", () => {
  const html = renderToStaticMarkup(
    createElement(AlertsView, { alerts: [], feed: [], nearbyRouteIds: ["Q"] }),
  );
  assert.match(html, /No active alerts right now/);
});

test("buildAlertDetailView keeps expandable status copy", () => {
  const view = buildAlertDetailView(alertItem());
  assert.ok(view);
  assert.match(view.impact || view.statusText || "", /delay|wait/i);
});

test("left rail renders route and alerts tabs from the same data", () => {
  const data = {
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
    nearbyTransitGroups: [{ id: "jay", name: "Jay St", mode: "subway", routeIds: ["Q"], arrivals: [] }],
    plan: { headline: "", rationale: "", steps: [], alternatives: [], notes: [] },
    feed: [],
    lineState: {},
    alerts: [],
  };
  const route = renderToStaticMarkup(createElement(LeftRail, { data, tab: "route" }));
  assert.match(route, /aria-label="SmartRoute sections"/);
  assert.match(route, />Route</);
  const alerts = renderToStaticMarkup(createElement(LeftRail, { data, tab: "alerts" }));
  assert.match(alerts, /No active alerts right now/);
});
