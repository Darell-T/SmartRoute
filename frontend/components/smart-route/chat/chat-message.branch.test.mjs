import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ChatMessage } from "./chat-message.tsx";

const arrivals = {
  routeId: "Q",
  stationName: "Jay St",
  stationGuidance: "3 min walk",
  sourceStatus: "live",
  updatedAt: "2026-07-16T12:00:00-04:00",
  groups: [{ direction: "uptown", label: "Uptown", minutes: [4] }],
};

function assistant(overrides = {}) {
  return {
    role: "assistant",
    turnId: "t1",
    text: "The Q is the fastest option.",
    reasoning: "",
    toolChips: [],
    routeCards: [],
    isStreaming: false,
    ...overrides,
  };
}

function render(turn, extras = {}) {
  return renderToStaticMarkup(
    createElement(ChatMessage, { turn, theme: "dark", ...extras }),
  );
}

test("a local turn without arrivals renders nothing", () => {
  const html = render({
    role: "assistant",
    turnId: "local",
    local: true,
    text: "Next Q trains",
    reasoning: "",
    toolChips: [],
    routeCards: [],
    isStreaming: false,
  });
  assert.equal(html, "");
});

test("a failed turn without handlers has no recovery buttons", () => {
  const html = render(assistant({
    text: "",
    error: {
      code: "upstream_error",
      message: "SmartRoute couldn’t complete this request.",
      retryable: true,
    },
  }));
  assert.match(html, /couldn’t complete this request/);
  assert.doesNotMatch(html, /Try again/);
  assert.doesNotMatch(html, /Dismiss/);
});

test("a non-retryable failure can still dismiss", () => {
  const html = render(
    assistant({
      text: "",
      error: {
        code: "upstream_error",
        message: "This request cannot be retried.",
        retryable: false,
      },
    }),
    { onDismissError() {} },
  );
  assert.match(html, /cannot be retried/);
  assert.match(html, /Dismiss/);
  assert.doesNotMatch(html, /Try again/);
});

test("a streaming search labels the orb as searching current sources", () => {
  const html = render(assistant({
    text: "",
    isStreaming: true,
    toolChips: [{
      id: "search-1",
      tool: "web_search",
      label: "Checking sources",
      status: "running",
    }],
  }));
  assert.match(html, /Searching current sources/);
  assert.match(html, /data-visible="true"/);
});

test("a streaming reply without a search tool labels the orb as deliberating", () => {
  const html = render(assistant({
    text: "",
    isStreaming: true,
  }));
  assert.match(html, /Deliberating/);
  assert.match(html, /data-visible="true"/);
});

test("a settled transit-status turn offers View alerts and Live Feed", () => {
  const html = render(
    assistant({
      notice: "Live subway status for the Q.",
      transitStatusAction: "view_alerts",
      arrivals,
    }),
    {
      onViewAlerts() {},
      onSeeArrivalsOnMap() {},
    },
  );
  assert.match(html, /Live subway status for the Q/);
  assert.match(html, /View alerts/);
  assert.match(html, /Open in Live Feed|Jay St|Uptown/);
});

test("alerts and arrivals stay hidden until their actions and payloads exist", () => {
  const html = render(assistant({
    transitStatusAction: "view_alerts",
  }));
  assert.doesNotMatch(html, /View alerts/);
  assert.doesNotMatch(html, /Open in Live Feed/);
});

test("View alerts does not appear from alert wording in the prose", () => {
  const html = render(
    assistant({ text: "There is an alert on the Q." }),
    { onViewAlerts() {} },
  );
  assert.doesNotMatch(html, /View alerts/);
  assert.doesNotMatch(html, /sr-chat-transit-action/);
});

test("source attribution follows assistant prose in rendered markup", () => {
  const html = render(assistant({
    text: "Take the Q.",
    sources: [
      { title: "Damn Lines", url: "https://damnlines.com/camera/l-industrie" },
    ],
  }));
  const proseAt = html.indexOf("Take the Q.");
  const sourcesAt = html.indexOf("sr-chat-sources");
  assert.ok(proseAt >= 0, "assistant prose is visible");
  assert.ok(sourcesAt > proseAt, "sources render after assistant prose");
  assert.match(html, /href="https:\/\/damnlines\.com\/camera\/l-industrie"/);
});
