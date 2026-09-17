import assert from "node:assert/strict";
import test from "node:test";

import { normalizeAlertFeedItems, normalizeRecentUpdates } from "./feed-normalizer.ts";

function alert(overrides = {}) {
  return {
    sev: "minor",
    kind: "train",
    lines: ["Q"],
    title: "Q trains are running with delays downtown this afternoon",
    sub: "Expect extra wait time on the Broadway line.",
    startedAgo: "12m",
    lastUpdate: "5m",
    ...overrides,
  };
}

test("alert feed normalizer merges, threads, and classifies passenger items", () => {
  const merged = normalizeAlertFeedItems(
    [
      alert(),
      alert({ lines: ["B"], title: "Q trains are running with delays downtown this afternoon" }),
      alert({ title: "Q trains are running with delays downtown this afternoon", sub: "Expect extra wait time on the Broadway line." }),
      alert({
        sev: "major",
        title: "No Q service between Canal and DeKalb",
        sub: "Take the R.",
        fullText: "Suspension in both directions. Take the R downtown.",
        affectedStops: ["Canal", "DeKalb", "Pacific", "Atlantic"],
        direction: "downtown",
        estClear: "~ 9 PM",
        activity: [
          { t: "20m", e: "First reported" },
          { t: "5m", e: "service restored at Canal" },
        ],
      }),
      alert({
        sev: "planned",
        title: "Weekend work on the Q",
        sub: "Planned skip-stop service.",
        startedAgo: "just now",
        lastUpdate: "just now",
      }),
    ],
    [
      {
        src: "FEED",
        sev: "major",
        line: "Q",
        title: "police activity - Union Sq",
        time: "live",
        detail: "NYPD on scene @MTA",
      },
      {
        src: "MTA",
        sev: "minor",
        line: "B",
        title: "B trains running local",
        time: "8m",
        detail: "Local stops in Brooklyn.",
      },
      {
        src: "SYSTEM",
        sev: "watch",
        line: null,
        title: "Signal problems",
        time: "now",
        detail: "Crews on scene.",
      },
    ],
  );
  assert.ok(merged.length >= 3);
  assert.ok(merged.some((item) => item.severity === "suspension" || /No service|suspension/i.test(item.title)));
  assert.ok(merged.some((item) => item.lifecycle === "resolved" || item.statusLabel === "Resolved" || item.details?.updates?.length));

  const feed = normalizeRecentUpdates([
    {
      src: "FEED",
      sev: "major",
      line: "2",
      title: "stalled train",
      time: "3m",
      detail: "Train held at Chambers.",
    },
    {
      src: "MTA",
      sev: "planned",
      line: "7",
      title: "service restored at Times Sq",
      time: "15m",
      detail: "Normal service has resumed.",
    },
  ]);
  assert.ok(feed.some((item) => item.severity === "incident"));
  assert.ok(feed.some((item) => item.lifecycle === "resolved" || /resolved|restored/i.test(item.title + (item.details?.updates?.[0]?.title ?? ""))));
});
