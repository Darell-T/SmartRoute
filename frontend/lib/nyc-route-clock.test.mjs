import assert from "node:assert/strict";
import test from "node:test";

import { formatNycRouteClock } from "./nyc-route-clock.ts";

test("formats Eastern Daylight Time ISO as NYC 12-hour clock", () => {
  // 15:45-04:00 on a summer date is 3:45 PM in America/New_York.
  assert.equal(formatNycRouteClock("2026-07-16T15:45:00-04:00"), "3:45 PM");
});

test("formats Eastern Standard Time ISO as NYC 12-hour clock", () => {
  // 15:45-05:00 on a winter date is 3:45 PM in America/New_York.
  assert.equal(formatNycRouteClock("2026-01-15T15:45:00-05:00"), "3:45 PM");
});

test("formats UTC instant into Eastern wall clock", () => {
  // 19:45Z in July is 3:45 PM EDT.
  assert.equal(formatNycRouteClock("2026-07-16T19:45:00.000Z"), "3:45 PM");
});

test("formats epoch milliseconds into Eastern wall clock", () => {
  const ms = Date.parse("2026-07-16T15:45:00-04:00");
  assert.equal(formatNycRouteClock(ms), "3:45 PM");
});

test("invalid or empty input stays unavailable", () => {
  assert.equal(formatNycRouteClock(null), null);
  assert.equal(formatNycRouteClock(undefined), null);
  assert.equal(formatNycRouteClock(""), null);
  assert.equal(formatNycRouteClock("bad"), null);
  assert.equal(formatNycRouteClock("not-a-date"), null);
  assert.equal(formatNycRouteClock(Number.NaN), null);
});
