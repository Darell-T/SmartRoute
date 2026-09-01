import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  AlertEmptyState,
  AlertLineGroupList,
} from "./alert-line-list.tsx";

function item(overrides = {}) {
  return {
    id: "alert-a",
    routeIds: ["A"],
    serviceName: "8 Avenue Express",
    title: "A service update",
    timestampLabel: "5m",
    severity: "minor",
    lifecycle: "active",
    statusLabel: "Delay",
    ...overrides,
  };
}

test("AlertLineGroupList renders singular expandable alert rows", () => {
  const html = renderToStaticMarkup(
    createElement(AlertLineGroupList, {
      "aria-label": "Line alerts",
      groups: [
        {
          id: "8-avenue",
          name: "8 Avenue",
          routeIds: ["A", "C", "E"],
          rank: 40,
          firstIndex: 0,
          items: [
            item({
              summary: "Expect longer waits.",
              expandable: true,
              details: { impact: "Expect longer waits." },
            }),
          ],
        },
      ],
    }),
  );

  assert.match(html, /1 alert/);
  assert.match(html, /5 min ago/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /sr-alert-line-row__chevron/);
});

test("AlertLineGroupList renders plural static rows and service fallback", () => {
  const html = renderToStaticMarkup(
    createElement(AlertLineGroupList, {
      "aria-label": "System alerts",
      groups: [
        {
          id: "systemwide",
          name: "Systemwide",
          routeIds: [],
          rank: 900,
          firstIndex: 0,
          items: [
            item({
              id: "plain",
              routeIds: [],
              serviceName: "Service alert",
              timestampLabel: "now",
              expandable: false,
            }),
            item({
              id: "generic",
              routeIds: [],
              serviceName: "Service alert",
              title: "Service change in effect",
              summary: "Service change in effect",
              timestampLabel: "live",
              expandable: false,
            }),
          ],
        },
      ],
    }),
  );

  assert.match(html, /2 alerts/);
  assert.match(html, />Service</);
  assert.match(html, /data-static="true"/);
  assert.doesNotMatch(html, /<time>/);
  assert.doesNotMatch(html, /aria-expanded/);
});

test("AlertEmptyState renders the no-alert contract", () => {
  const html = renderToStaticMarkup(createElement(AlertEmptyState));
  assert.match(html, /No active alerts right now/);
  assert.match(html, /Service updates from today/);
});
