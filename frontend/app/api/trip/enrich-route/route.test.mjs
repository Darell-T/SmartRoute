import assert from "node:assert/strict";
import test from "node:test";
import { EnrichRouteSchema } from "./route.ts";

const step = { type: "SUBWAY", route_id: "A", train_line: "A", line_color: "#0039A6", direction: "Downtown", departure_stop: "Jay St", arrival_stop: "59 St", departure_time_iso: "2026-07-27T12:00:00-04:00", arrival_time_iso: "2026-07-27T12:20:00-04:00", minutes_until_train_arrives: -1, minutes_until_arrival: 19, route_total_minutes: 20, route_total_seconds: 1200, duration_minutes: 18, distance_meters: 4300, stop_count: 5, segment_index: 1, start_point: { lat: 40.692, lng: -73.987 }, end_point: { lat: 40.764, lng: -73.98 }, departure_coords: { latitude: 40.692, longitude: -73.987 }, arrival_coords: { latitude: 40.764, longitude: -73.98 }, polyline: { encodedPolyline: "abc" }, intermediate_stops: ["Canal St"], intermediate_stop_locations: [{ name: "Canal St", lat: 40.72, lng: -74.0 }] };

test("enrich schema retains complete route steps and rejects unknown or malformed values", () => {
  assert.equal(EnrichRouteSchema.safeParse({ steps: [step, { type: "WALK", duration_minutes: 3, distance_meters: 240 }] }).success, true);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, unexpected: true }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, intermediate_stop_locations: [{ name: "bad", lat: "no", lng: -74 }] }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, route_id: {} }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, departure_time_iso: 123 }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, direction: [] }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, route_id: "x".repeat(301) }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, arrival_time_iso: "x".repeat(65) }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, departure_coords: null }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, start_point: { lat: 40.692 } }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, end_point: { lat: 40.764, longitude: -73.98 } }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, arrival_coords: { lat: 40.764, lng: -73.98, latitude: 40.764, longitude: -73.98 } }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, start_point: { lat: true, lng: -73.987 } }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, end_point: { lat: Number.NaN, lng: -73.98 } }] }).success, false);
  assert.equal(EnrichRouteSchema.safeParse({ steps: [{ ...step, departure_coords: { latitude: 42, longitude: -73.987 } }] }).success, false);
});
