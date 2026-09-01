import assert from "node:assert/strict";
import test from "node:test";

import {
  formatDurationLabel,
  recommendedCandidateFromPlan,
  routeResultKey,
} from "./route-display-compat.ts";

test("route result keys distinguish recommendations and selected alternatives", () => {
  assert.equal(
    routeResultKey({
      isAlternativeRoute: false,
      pickedLine: "Q",
      headsign: "Coney Island",
      totalTime: "27 min",
    }),
    "recommended:Q:Coney Island:27 min",
  );
  assert.equal(
    routeResultKey({
      isAlternativeRoute: true,
      pickedLine: "",
      headsign: undefined,
      totalTime: undefined,
    }),
    "selected:walk::",
  );
});

test("duration labels preserve unknown text and format minute and hour totals", () => {
  assert.equal(formatDurationLabel("Live estimate"), "Live estimate");
  assert.equal(formatDurationLabel("47 minutes"), "47 min");
  assert.equal(formatDurationLabel("120 min"), "2 hr");
  assert.equal(formatDurationLabel("135 min"), "2 hr 15 min");
});

test("recommended route display sums walking rows and counts transit transfers", () => {
  assert.deepEqual(
    recommendedCandidateFromPlan({
      steps: [
        { type: "walk", duration: "4 min" },
        { type: "board", line: "Q", duration: "live" },
        { type: "exit", duration: "3 minutes" },
        { type: "ride", line: "A", duration: "18 min" },
        { type: "destination", duration: "2 min" },
      ],
    }),
    {
      walkMinutes: 9,
      transfers: 1,
    },
  );
});

test("recommended route display omits zero walking and ignores durations without digits", () => {
  assert.deepEqual(
    recommendedCandidateFromPlan({
      steps: [
        { type: "walk", duration: "-7 min" },
        { type: "exit", duration: "walk" },
        { type: "board", line: "M15", duration: undefined },
        { type: "ride", duration: "12 min" },
      ],
    }),
    {
      walkMinutes: undefined,
      transfers: 0,
    },
  );
});
