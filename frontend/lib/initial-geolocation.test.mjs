import assert from "node:assert/strict";
import test from "node:test";
import {
  authoritativeChatOrigin,
  locationStateForCoordinates,
  nextLocationState,
  requestInitialLocation,
} from "./initial-geolocation.ts";

test("initial geolocation falls back when unavailable and ignores late callbacks after cleanup", async () => {
  const values = [];
  const cleanup = requestInitialLocation(undefined, { lat: 40.7, lng: -73.9 }, (value) => values.push(value));
  await Promise.resolve();
  assert.deepEqual(values, [{ status: "fallback_nyc", coordinates: { lat: 40.7, lng: -73.9 } }]);
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
  assert.deepEqual(values, [{ status: "precise_nyc", coordinates: { lat: 40.71, lng: -73.91 } }]);
  requestInitialLocation({ getCurrentPosition(_ok, bad) { failure = bad; } }, { lat: 1, lng: 2 }, (value) => values.push(value));
  failure();
  assert.deepEqual(values.at(-1), { status: "outside_service_area" });
});

test("initial geolocation cleanup cancels its timeout before a Strict Mode-style remount", () => {
  const values = [];
  const timers = [];
  const setTimer = (callback) => {
    const timer = { callback, cleared: false };
    timers.push(timer);
    return timer;
  };
  const clearTimer = (timer) => {
    timer.cleared = true;
  };
  let firstSuccess;
  const firstCleanup = requestInitialLocation(
    { getCurrentPosition(success) { firstSuccess = success; } },
    { lat: 1, lng: 2 },
    (value) => values.push(value),
    setTimer,
    clearTimer,
  );

  firstCleanup();
  timers[0].callback();
  firstSuccess({ coords: { latitude: 10, longitude: 20 } });
  assert.equal(timers[0].cleared, true);
  assert.deepEqual(values, []);

  let secondSuccess;
  const secondCleanup = requestInitialLocation(
    { getCurrentPosition(success) { secondSuccess = success; } },
    { lat: 3, lng: 4 },
    (value) => values.push(value),
    setTimer,
    clearTimer,
  );
  secondSuccess({ coords: { latitude: 30, longitude: 40 } });
  secondCleanup();

  assert.deepEqual(values, [{ status: "outside_service_area" }]);
  assert.equal(timers[1].cleared, true);
});

test("initial geolocation does not let a late precise callback replace its fallback", () => {
  const values = [];
  const timers = [];
  let success;
  requestInitialLocation(
    { getCurrentPosition(next) { success = next; } },
    { lat: 40.7484, lng: -73.9857 },
    (value) => values.push(value),
    (callback) => {
      const timer = { callback };
      timers.push(timer);
      return timer;
    },
    () => {},
  );

  timers[0].callback();
  success({ coords: { latitude: 40.71, longitude: -73.91 } });

  assert.deepEqual(values, [{
    status: "fallback_nyc",
    coordinates: { lat: 40.7484, lng: -73.9857 },
  }]);
});

test("initial geolocation keeps a precise location outside NYC out of transit requests", () => {
  assert.deepEqual(
    locationStateForCoordinates({ lat: 40.7128, lng: -74.006 }),
    { status: "precise_nyc", coordinates: { lat: 40.7128, lng: -74.006 } },
  );
  assert.deepEqual(
    locationStateForCoordinates({ lat: 42.3601, lng: -71.0589 }),
    { status: "outside_service_area" },
  );
});

test("a map fallback cannot overwrite a precise or out-of-service-area location", () => {
  const fallback = locationStateForCoordinates(
    { lat: 40.7484, lng: -73.9857 },
    "fallback",
  );
  const precise = locationStateForCoordinates({ lat: 40.71, lng: -73.91 });
  const outside = locationStateForCoordinates({ lat: 42.3601, lng: -71.0589 });

  assert.deepEqual(nextLocationState(precise, fallback), precise);
  assert.deepEqual(nextLocationState(outside, fallback), outside);
  assert.deepEqual(nextLocationState(fallback, precise), precise);
});

test("only a precise device location is authoritative for chat routing", () => {
  const precise = locationStateForCoordinates({ lat: 40.71, lng: -73.91 });
  const fallback = locationStateForCoordinates(
    { lat: 40.7484, lng: -73.9857 },
    "fallback",
  );

  assert.deepEqual(authoritativeChatOrigin(precise), {
    lat: 40.71,
    lng: -73.91,
  });
  assert.equal(authoritativeChatOrigin(fallback), null);
  assert.equal(authoritativeChatOrigin({ status: "pending" }), null);
  assert.equal(authoritativeChatOrigin({ status: "outside_service_area" }), null);
});
