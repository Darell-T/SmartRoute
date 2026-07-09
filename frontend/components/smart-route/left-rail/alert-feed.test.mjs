import assert from "node:assert/strict";
import test from "node:test";

import {
  ALERT_ROUTE_TO_FAMILY,
  deriveLifecycle,
  normalizeAlertFeedItems,
  serviceNameForRoutes,
} from "./alert-feed.ts";

test("alert lifecycle is derived from text: resolved / monitoring / active", () => {
  assert.equal(deriveLifecycle("Service has returned to normal"), "resolved");
  assert.equal(deriveLifecycle("R trains have resumed service"), "resolved");
  assert.equal(deriveLifecycle("Crews are investigating a signal problem"), "monitoring");
  assert.equal(deriveLifecycle("Trains are running with delays"), "active");
});

test("grouped alert carries lifecycle, status pill, service name, and an update thread", () => {
  const alerts = [
    {
      sev: "minor",
      kind: "train",
      lines: ["2", "3"],
      title: "2 / 3 trains are running with delays in both directions",
      sub: "Signal problem near Franklin Av-Medgar Evers College.",
      aiContext: "Crews are investigating a signal problem. Use 4/5 service where possible.",
      startedAgo: "52m ago",
      lastUpdate: "13m ago",
      activity: [
        { t: "17m ago", e: "Signal problem addressed" },
        { t: "33m ago", e: "Delays first reported" },
      ],
    },
    {
      sev: "planned",
      kind: "train",
      lines: ["A"],
      title: "Weekend service change",
      sub: "No A trains between Lefferts and Howard Beach",
      startedAgo: "2h ago",
      lastUpdate: "18m ago",
    },
    {
      sev: "minor",
      kind: "train",
      lines: ["R"],
      title: "R trains have resumed normal service",
      sub: "Earlier signal problem at 36 St resolved.",
      aiContext: "Service has returned to normal.",
      startedAgo: "2h ago",
      lastUpdate: "2h ago",
    },
  ];

  const items = normalizeAlertFeedItems(alerts, []);

  const active = items.find((item) => item.routeIds.includes("2"));
  assert.equal(active.lifecycle, "monitoring", "investigating text reads as monitoring");
  // Status words are severity classifications (Major/Delay/Planned/Resolved),
  // not lifecycle pills — a minor disruption reads "Delay".
  assert.equal(active.statusLabel, "Delay");
  assert.equal(active.serviceName, "7 Avenue Express", "trunk name is the row identity");
  assert.ok(active.details.currentStatus, "current status present");
  assert.equal(active.details.updates.length, 2, "activity becomes the earlier-updates thread");
  assert.equal(
    active.details.alternatives,
    "Use 4/5 service where possible.",
    "alternatives are the alert's own instruction sentence, verbatim",
  );

  const planned = items.find((item) => item.routeIds.includes("A"));
  assert.equal(planned.statusLabel, "Planned", "planned severity reads Planned, not Active now");

  const resolved = items.find((item) => item.routeIds.includes("R"));
  assert.equal(resolved.lifecycle, "resolved");
  assert.equal(resolved.statusLabel, "Resolved");

  // Resolved alerts sort to the bottom of the day timeline.
  assert.equal(items[items.length - 1].lifecycle, "resolved", "resolved items sink to the bottom");
});

test("alert feed adapter merges service alerts and recent updates into passenger rows", () => {
  const alerts = [
    {
      sev: "minor",
      kind: "train",
      lines: ["Q"],
      title: "Person needed medical attention at Atlantic Av-Barclays Ctr",
      sub: "Trains are delayed in both directions near Atlantic Av.",
      aiContext: "Expect northbound and southbound Q delays while responders assist.",
      affectedStops: ["Atlantic Av-Barclays Ctr", "7 Av"],
      startedAgo: "5m ago",
      lastUpdate: "Just now",
    },
    {
      sev: "planned",
      kind: "train",
      lines: ["SIR"],
      title: "All [SIR] trains from Huguenot to Eltingville board from the opposite platform",
      sub: "Boarding changes include Huguenot, Annadale, and Eltingville.",
      startedAgo: "2h ago",
      lastUpdate: "18m ago",
    },
  ];
  const feed = [
    {
      src: "FEED",
      sev: "watch",
      line: "Q",
      title: "Medical - Atlantic Av-Barclays Ctr",
      time: "live",
      detail: "Reported near Atlantic Av-Barclays Ctr. @Derrick_NYC",
    },
    {
      src: "MTA",
      sev: "minor",
      line: "Q",
      title: "Person needed medical attention at Atlantic Av-Barclays Ctr",
      time: "5m",
      detail: "Trains are delayed in both directions near Atlantic Av.",
    },
  ];

  const items = normalizeAlertFeedItems(alerts, feed);

  assert.equal(
    items.some((item) => /MTA$|Grok says|Transit intelligence/i.test(item.title)),
    false,
    "feed titles stay passenger-facing",
  );
  assert.equal(
    items.filter((item) => item.title === "Medical assistance at Atlantic Av-Barclays Ctr").length,
    2,
    "service alert and live social update can coexist when source and summary differ",
  );
  assert.equal(
    items.some((item) => item.title.includes("[SIR]")),
    true,
    "bracket route tokens stay in titles — TransitText renders them as real badges, never glued-together text like 'SIRtrains'",
  );
  assert.equal(items[0].severity, "incident", "live incidents sort before routine alerts");
});

test("alert adapter recognizes NYC bus prefixes and express subway aliases", () => {
  assert.equal(serviceNameForRoutes(["S40"]), "S40 bus");
  assert.equal(serviceNameForRoutes(["BXM1"]), "BXM1 bus");
  assert.equal(serviceNameForRoutes(["6X"]), "Lexington Av Express");
  assert.equal(ALERT_ROUTE_TO_FAMILY.get("6X")?.name, "Lexington Avenue");
});

test("alert copy strips bracketed icon placeholders but keeps shuttle bus instructions", () => {
  const items = normalizeAlertFeedItems(
    [
      {
        sev: "major",
        kind: "train",
        lines: ["6"],
        title: "No 6 between Westchester Sq and Pelham Bay Park",
        sub: "[shuttle bus icon] Free Bx91 shuttle buses run between Westchester Sq and Pelham Bay Park.",
        aiContext:
          "The last stop for Bronx-bound trains is 3 Av-138 St. [bus icon] Free Bx91 shuttle buses make local stops.",
        startedAgo: "12m ago",
        lastUpdate: "7m ago",
      },
    ],
    [],
  );

  const alert = items[0];
  const visibleText = [
    alert.title,
    alert.summary,
    alert.details?.impact,
    alert.details?.alternatives,
  ].join(" ");

  assert.doesNotMatch(visibleText, /\[(?:shuttle\s+bus|bus)\s+icon\]/i);
  assert.match(visibleText, /Free Bx91 shuttle buses/);
  assert.match(visibleText, /3 Av-138 St/);
});

test("activity-only alert details remain expandable", () => {
  const items = normalizeAlertFeedItems(
    [
      {
        sev: "minor",
        kind: "train",
        lines: ["N"],
        title: "Skipping stations",
        sub: "Active service notice",
        startedAgo: "20m ago",
        lastUpdate: "10m ago",
        activity: [
          { t: "12m ago", e: "Trains started bypassing City Hall." },
        ],
      },
    ],
    [],
  );

  assert.equal(items[0].expandable, true);
  assert.equal(items[0].details.updates.length, 1);
});
