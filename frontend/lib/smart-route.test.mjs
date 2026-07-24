import assert from "node:assert/strict";
import test from "node:test";

import { summarizeRoute } from "./smart-route.ts";

const FIXED_NOW = new Date("2026-07-16T12:00:00-04:00");

const twoLegSteps = [
  {
    type: "SUBWAY",
    train_line: "Q",
    departure_stop: "Church Av",
    arrival_stop: "Union Sq",
    minutes_until_arrival: 20,
  },
  {
    type: "SUBWAY",
    train_line: "5",
    departure_stop: "Union Sq",
    arrival_stop: "Burke Av",
    minutes_until_arrival: 40,
  },
];

test("summarizeRoute prefers totalMinutesOverride over step-derived ETA", () => {
  const summary = summarizeRoute(twoLegSteps, FIXED_NOW, 89);
  assert.equal(summary.totalMin, 89);
  // now + 89 min → 1:29 PM
  assert.equal(summary.arriveLabel, "1:29 PM");
});

test("summarizeRoute prefers arrival ISO for arriveLabel over now+eta", () => {
  const summary = summarizeRoute(twoLegSteps, FIXED_NOW, 89, {
    arrivalAtIso: "2026-07-16T15:45:00-04:00",
  });
  assert.equal(summary.totalMin, 89);
  // Canonical wall clock, not 12:00 + 89 min
  assert.equal(summary.arriveLabel, "3:45 PM");
});

test("summarizeRoute falls back to now+eta when arrival ISO is absent", () => {
  const summary = summarizeRoute(twoLegSteps, FIXED_NOW, 30);
  assert.equal(summary.arriveLabel, "12:30 PM");
});

test("summarizeRoute falls back to now+eta when arrival ISO is unparseable", () => {
  const summary = summarizeRoute(twoLegSteps, FIXED_NOW, 30, {
    arrivalAtIso: "not-a-date",
  });
  assert.equal(summary.arriveLabel, "12:30 PM");
});

test("summarizeRoute prefers transfers override over step re-count", () => {
  // two consecutive transit legs re-count as 1 transfer; override says 2
  const summary = summarizeRoute(twoLegSteps, FIXED_NOW, 89, {
    transfers: 2,
  });
  assert.equal(summary.transfers, 2);
});

test("summarizeRoute recomputes transfers when override is absent", () => {
  const summary = summarizeRoute(twoLegSteps, FIXED_NOW, 40);
  assert.equal(summary.transfers, 1);
});

test("summarizeRoute accepts transfers override of zero", () => {
  const summary = summarizeRoute(twoLegSteps, FIXED_NOW, 40, {
    transfers: 0,
  });
  assert.equal(summary.transfers, 0);
});
