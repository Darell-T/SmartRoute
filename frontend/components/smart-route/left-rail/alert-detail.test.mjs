import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  AlertDetailPanel,
  alertSeverityLabel,
  buildAlertDetailView,
  featuredAlertAdvice,
  featuredAlertBody,
  lineAlertSubtitle,
  shortAlertTimeLabel,
} from "./alert-detail.tsx";

function item(overrides = {}) {
  return {
    id: "alert-q",
    routeIds: ["Q"],
    serviceName: "Broadway Express",
    title: "Q trains are running with delays",
    summary: "Trains are moving slower than usual.",
    timestampLabel: "12m",
    severity: "minor",
    lifecycle: "active",
    statusLabel: "Delays",
    source: "mta",
    expandable: true,
    details: {
      currentStatus: "Trains are delayed.",
      impact: "Expect extra wait time.",
      alternatives: "Take the B or D.",
      affectedStops: ["DeKalb Av"],
      updates: [{ time: "5m", title: "Earlier delay", summary: "Signal trouble" }],
    },
    ...overrides,
  };
}

test("alert detail helpers cover severity, clocks, and featured copy", () => {
  assert.equal(alertSeverityLabel("planned"), "Planned work");
  assert.equal(alertSeverityLabel("minor"), "Minor delay");
  assert.equal(alertSeverityLabel("major"), "Major disruption");
  assert.equal(alertSeverityLabel("suspension"), "Suspension");
  assert.equal(alertSeverityLabel("incident"), "Nearby incident");
  assert.equal(alertSeverityLabel("notice"), "Service change");
  assert.equal(shortAlertTimeLabel("now"), undefined);
  assert.equal(shortAlertTimeLabel("12m"), "12 min ago");
  assert.equal(shortAlertTimeLabel("3h"), "3 hr ago");
  assert.equal(shortAlertTimeLabel("yesterday"), "Yesterday");
  const body = featuredAlertBody(item());
  assert.match(body ?? "", /slower|wait/i);
  assert.match(featuredAlertAdvice(item(), body) ?? "", /Take the B/);
  assert.equal(
    featuredAlertAdvice(item({ details: { alternatives: "q trains are running with delays" } }), body),
    undefined,
  );
  assert.match(lineAlertSubtitle(item()) ?? "", /slower|wait|Broadway/i);
  assert.ok(lineAlertSubtitle(item({ summary: item().title })));
});

test("buildAlertDetailView splits guidance copy and keeps updates", () => {
  const view = buildAlertDetailView(
    item({
      details: {
        impact: "No Q between Canal St. Take the R for alternative service downtown.",
        alternatives: "Use the R.",
        affectedStops: ["Canal St"],
        updates: [{ time: "now", title: " " }],
      },
    }),
  );
  assert.ok(view);
  assert.match(view.impact ?? "", /Canal/);
  assert.match(view.alternatives ?? "", /Take the R|Use the R/i);
  assert.deepEqual(view.stops, ["Canal St"]);
  assert.equal(buildAlertDetailView(item({ details: undefined })), null);
  assert.equal(
    buildAlertDetailView(item({ expandable: false, details: {} })),
    null,
  );
});

test("alert detail panel renders impact, alternatives, stops, and updates", () => {
  const view = buildAlertDetailView(item());
  const html = renderToStaticMarkup(
    createElement(AlertDetailPanel, { item: item(), detail: view }),
  );
  assert.match(html, /Impact/);
  assert.match(html, /Travel alternatives/);
  assert.match(html, /Affected stop/);
  assert.match(html, /Earlier delay/);
  const systemwide = renderToStaticMarkup(
    createElement(AlertDetailPanel, {
      item: item({
        routeIds: ["A", "C", "E", "B", "D", "F", "M", "G"],
        serviceName: "Multiple lines",
      }),
      detail: { statusText: "Citywide", stops: ["a", "b"], updates: [] },
    }),
  );
  assert.match(systemwide, /Current status/);
  assert.match(systemwide, /Affected stops/);
});
