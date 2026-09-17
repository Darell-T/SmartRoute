import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  AlertDetailPanel,
  buildAlertDetailView,
  featuredAlertAdvice,
  featuredAlertBody,
  lineAlertSubtitle,
  shortAlertTimeLabel,
} from "./detail.tsx";

function item(overrides = {}) {
  return {
    id: "alert-a",
    routeIds: ["A"],
    serviceName: "8 Avenue Express",
    title: "A service update",
    timestampLabel: "live",
    severity: "notice",
    lifecycle: "active",
    statusLabel: "",
    expandable: true,
    ...overrides,
  };
}

test("buildAlertDetailView uses fallback status fields and summary-only updates", () => {
  assert.deepEqual(
    buildAlertDetailView(item({ details: { whatHappened: "Signal trouble." } })),
    {
      impact: undefined,
      alternatives: undefined,
      statusText: "Signal trouble.",
      stops: [],
      updates: [],
    },
  );
  assert.equal(
    buildAlertDetailView(item({ summary: "Track inspection.", details: {} }))?.statusText,
    "Track inspection.",
  );
  assert.equal(
    buildAlertDetailView(item({ details: { updates: [{ time: "", summary: "Crews responding.", tone: "active" }] } }))?.updates.length,
    1,
  );
  assert.equal(buildAlertDetailView(item({ details: {} })), null);
});

test("buildAlertDetailView separates guidance and suppresses repeated status", () => {
  const split = buildAlertDetailView(
    item({
      title: "No A service",
      details: {
        impact: "Take the C for alternative service.",
        currentStatus: "Take the C",
      },
    }),
  );
  assert.equal(split?.impact, undefined);
  assert.equal(split?.alternatives, "Take the C for alternative service.");
  assert.equal(split?.statusText, undefined);

  const punctuation = buildAlertDetailView(
    item({
      details: {
        impact: "A trains are delayed! Use nearby buses.",
        currentStatus: "Crews are responding.",
      },
    }),
  );
  assert.equal(punctuation?.impact, "A trains are delayed!");
  assert.equal(punctuation?.alternatives, "Use nearby buses.");
  assert.equal(punctuation?.statusText, "Crews are responding.");
});

test("featured alert copy handles generic, echoed, and duplicate advice", () => {
  assert.equal(
    featuredAlertBody(
      item({
        title: "A service update",
        summary: "Service change in effect",
        details: { impact: "Active service notice", currentStatus: "Trains are running with delays." },
      }),
    ),
    undefined,
  );
  assert.equal(
    featuredAlertBody(
      item({
        title: "A service update",
        summary: "A service update: trains bypass 50 St.",
      }),
    ),
    "Trains bypass 50 St.",
  );
  assert.equal(
    featuredAlertAdvice(item({ details: { alternatives: "..." } }), undefined),
    "...",
  );
  assert.equal(
    featuredAlertAdvice(
      item({ title: "Use the C.", details: { alternatives: "Use the C." } }),
      undefined,
    ),
    undefined,
  );
});

test("line subtitle and detail markup expose remaining display variants", () => {
  assert.equal(
    lineAlertSubtitle(
      item({
        routeIds: ["A", "C", "E"],
        summary: "A service update",
        details: { impact: "A service update", currentStatus: "A service update" },
      }),
    ),
    "Multiple lines affected",
  );
  assert.equal(lineAlertSubtitle(item({ summary: "A service update" })), "8 Avenue Express");
  assert.equal(shortAlertTimeLabel(""), undefined);
  assert.equal(shortAlertTimeLabel("just now"), undefined);

  const html = renderToStaticMarkup(
    createElement(AlertDetailPanel, {
      item: item(),
      detail: {
        stops: Array.from({ length: 10 }, (_, index) => `Stop ${index + 1}`),
        updates: [
          { time: "now", title: "", summary: "Service changed.", tone: undefined },
          { time: "", title: "No timestamp", tone: "active" },
        ],
      },
    }),
  );
  assert.match(html, /Affected stops/);
  assert.match(html, /Service changed/);
  assert.match(html, /data-tone="muted"/);
  assert.doesNotMatch(html, /Stop 9/);
});
