import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ChatArrivalsCard } from "./chat-arrivals-card.tsx";

function arrivals(overrides = {}) {
  return {
    routeId: "Q",
    stationName: "Church Av",
    sourceStatus: "live",
    groups: [{ direction: "uptown", label: "Uptown", minutes: [4, 9] }],
    ...overrides,
  };
}

function render(overrides, onSeeOnMap) {
  return renderToStaticMarkup(
    createElement(ChatArrivalsCard, {
      arrivals: arrivals(overrides),
      onSeeOnMap,
    }),
  );
}

test("scheduled status without an update time renders without a clock", () => {
  const html = render({ sourceStatus: "scheduled", updatedAt: undefined });

  assert.match(html, />Scheduled</);
  assert.doesNotMatch(html, /updated/);
});

test("stale status with an invalid update time renders without a clock", () => {
  const html = render({ sourceStatus: "stale", updatedAt: "not-a-date" });

  assert.match(html, />Stale</);
  assert.doesNotMatch(html, /updated/);
});

test("unknown source status omits the status label", () => {
  const html = render({ sourceStatus: "unknown", updatedAt: "2026-07-16T16:00:00Z" });

  assert.doesNotMatch(html, />Live</);
  assert.doesNotMatch(html, />Scheduled</);
  assert.doesNotMatch(html, />Stale</);
  assert.doesNotMatch(html, /updated/);
});

test("empty arrivals explain each unavailable source state", () => {
  const cases = [
    ["provider_unavailable", "Live predictions are temporarily unavailable."],
    ["stale", "The latest predictions are stale."],
    ["stop_not_resolved", "Choose a more specific station."],
    ["live", "No current predictions for this stop."],
  ];

  for (const [sourceStatus, copy] of cases) {
    assert.ok(render({ sourceStatus, groups: [] }).includes(copy), sourceStatus);
  }
});

test("missing station guidance omits the walking guidance row", () => {
  const html = render({ stationGuidance: undefined });

  assert.doesNotMatch(html, /sr-chat-arrivals-card__walk-icon/);
});

test("catchable arrival copy appears only for a numeric minute value", () => {
  const withCatchable = render({
    catchability: { catchable_arrival_minutes: 9 },
  });
  const withoutCatchable = render({ catchability: undefined });

  assert.ok(withCatchable.includes(
    "The 9 min arrival is the first one with enough walking time.",
  ));
  assert.doesNotMatch(withoutCatchable, /first one with enough walking time/);
});

test("Live Feed footer appears only when its action is provided", () => {
  const withoutAction = render({});
  const withAction = render({}, () => {});

  assert.doesNotMatch(withoutAction, /sr-chat-arrivals-card__footer/);
  assert.match(withAction, /sr-chat-arrivals-card__footer/);
  assert.match(withAction, />Open in Live Feed</);
});
