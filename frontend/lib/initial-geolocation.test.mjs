import assert from "node:assert/strict";
import test from "node:test";
import { requestInitialLocation } from "./initial-geolocation.ts";

test("initial geolocation falls back when unavailable and ignores late callbacks after cleanup", async () => {
  const values = [];
  const cleanup = requestInitialLocation(undefined, { lat: 40.7, lng: -73.9 }, (value) => values.push(value));
  await Promise.resolve();
  assert.deepEqual(values, [{ lat: 40.7, lng: -73.9 }]);
  let success;
  const lateCleanup = requestInitialLocation({ getCurrentPosition(next) { success = next; } }, { lat: 1, lng: 2 }, (value) => values.push(value));
  lateCleanup();
  success({ coords: { latitude: 9, longitude: 10 } });
  cleanup();
  assert.equal(values.length, 1);
});

test("initial geolocation applies success and error fallback", () => {
  const values = [];
  let success; let failure;
  requestInitialLocation({ getCurrentPosition(ok, bad) { success = ok; failure = bad; } }, { lat: 1, lng: 2 }, (value) => values.push(value));
  success({ coords: { latitude: 40.71, longitude: -73.91 } });
  assert.deepEqual(values, [{ lat: 40.71, lng: -73.91 }]);
  requestInitialLocation({ getCurrentPosition(_ok, bad) { failure = bad; } }, { lat: 1, lng: 2 }, (value) => values.push(value));
  failure();
  assert.deepEqual(values.at(-1), { lat: 1, lng: 2 });
});
