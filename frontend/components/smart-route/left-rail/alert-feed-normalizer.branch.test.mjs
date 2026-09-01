import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeAlertFeedItems,
  normalizeRecentUpdates,
} from "./alert-feed-normalizer.ts";

function alert(overrides = {}) {
  return {
    sev: "watch",
    kind: "train",
    lines: [],
    title: "",
    sub: "",
    startedAgo: "now",
    lastUpdate: "now",
    ...overrides,
  };
}

function event(overrides = {}) {
  return {
    src: "SYSTEM",
    sev: "watch",
    line: null,
    title: "",
    time: "now",
    detail: "",
    ...overrides,
  };
}

test("normalizeAlertFeedItems deduplicates exact alerts and merges matching issue text", () => {
  const items = normalizeAlertFeedItems(
    [
      alert({ lines: ["A"], title: "Signal problem affecting downtown service", sub: "Expect delays near 14 Street." }),
      alert({ lines: ["A"], title: "Signal problem affecting downtown service", sub: "Expect delays near 14 Street." }),
      alert({ lines: ["C"], title: "Signal problem affecting downtown service", sub: "Expect delays near 14 St." }),
      alert({ lines: ["Q"], title: "Brief notice" }),
      alert({ lines: ["R"], title: "Brief notice" }),
    ],
    [],
  );

  const merged = items.find((item) => item.title.includes("Signal problem"));
  assert.deepEqual(merged?.routeIds, ["A", "C"]);
  assert.equal(merged?.serviceName, "Multiple lines");
  assert.equal(items.filter((item) => item.title === "Brief notice").length, 2);
});

test("normalizeRecentUpdates maps sources, fallback services, and thread labels", () => {
  const items = normalizeRecentUpdates([
    event({
      src: "FEED",
      sev: "major",
      line: null,
      title: "stalled",
      time: "7m",
      detail: "Response requested",
    }),
    event({
      src: "MTA",
      sev: "major",
      line: "UNKNOWN",
      title: "Track obstruction",
      time: "live",
      detail: "Service restored",
    }),
    event({
      src: "SYSTEM",
      sev: "planned",
      title: "Maintenance",
      time: "now",
      detail: "@ops monitoring",
    }),
  ]);

  assert.equal(items[0].serviceName, "Nearby incident");
  assert.equal(items[0].source, "nyc-alert");
  assert.equal(items[0].details.updates[0].title, "First reported");
  assert.equal(items[0].severity, "incident");
  assert.equal(items[1].serviceName, "UNKNOWN service");
  assert.equal(items[1].source, "mta");
  assert.equal(items[1].lifecycle, "resolved");
  assert.deepEqual(items[1].details.updates, []);
  assert.equal(items[2].source, "social");
  assert.equal(items[2].sourceLabel, "@ops");
  assert.equal(items[2].context, "Source: @ops");
});

test("normalizeRecentUpdates derives status text when feed copy is absent", () => {
  const [planned, minor, resolved] = normalizeRecentUpdates([
    event({ src: "MTA", sev: "planned", title: "", detail: "", time: "2h" }),
    event({ src: "MTA", sev: "minor", title: "", detail: "", time: "2m" }),
    event({ src: "SYSTEM", sev: "watch", title: "", detail: "Service has returned to normal", time: "3m" }),
  ]);

  assert.equal(planned.statusLabel, "Planned");
  assert.equal(planned.details.currentStatus, "Planned service change in effect.");
  assert.equal(minor.statusLabel, "Delay");
  assert.equal(minor.details.currentStatus, "Trains are running with delays.");
  assert.equal(resolved.statusLabel, "Resolved");
  assert.equal(resolved.details.updates[0].title, "Service resolved");
  assert.equal(resolved.details.updates[0].tone, "resolved");
});

test("service alerts cover empty, direction, stop, activity, and fallback-thread variants", () => {
  const items = normalizeAlertFeedItems(
    [
      alert(),
      alert({
        sev: "major",
        lines: ["Q"],
        title: "No Q service",
        sub: "No trains between Canal and Prospect Park.",
        fullText: "Use the R.",
        direction: " What's happening? Downtown ",
        affectedStops: ["Canal", "DeKalb", "Atlantic", "Prospect Park"],
        estClear: "9 PM",
        startedAgo: "20m",
        lastUpdate: "just now",
      }),
      alert({
        sev: "planned",
        lines: ["7"],
        title: "Weekend maintenance",
        sub: "Planned work",
        aiContext: "Crews are replacing track.",
        startedAgo: "1h",
        lastUpdate: "1h",
        activity: [
          { t: "1h", e: "work began" },
          { t: "5m", e: "service cleared" },
        ],
      }),
    ],
    [],
  );

  const empty = items.find((item) => item.routeIds.length === 0);
  assert.equal(empty?.serviceName, "Service alert");
  assert.equal(empty?.expandable, false);
  assert.equal(empty?.isLive, true);

  const suspended = items.find((item) => item.routeIds.includes("Q"));
  assert.equal(suspended?.severity, "suspension");
  assert.equal(suspended?.context, "Affected: Canal, DeKalb, Atlantic +1");
  assert.equal(suspended?.details.direction, "Downtown");
  assert.equal(suspended?.details.updates[0].title, "First reported");
  assert.match(suspended?.details.alternatives ?? "", /Use the R/);

  const planned = items.find((item) => item.routeIds.includes("7"));
  assert.equal(planned?.severity, "planned");
  assert.equal(planned?.details.updates[1].tone, "resolved");
});
