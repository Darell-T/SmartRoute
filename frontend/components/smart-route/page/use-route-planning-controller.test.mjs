import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  planningErrorText,
  prepareRouteSubmit,
  useRoutePlanningController,
} from "./use-route-planning-controller.ts";

function PlanningProbe() {
  const controller = useRoutePlanningController({ userLocation: { lng: -73.99, lat: 40.7 } });
  return createElement("div", {
    "data-phase": controller.planningPhase,
    "data-loading": String(controller.isLoading),
  }, controller.inputValue || "empty");
}

test("route planning controller starts idle before a destination is submitted", () => {
  const html = renderToStaticMarkup(createElement(PlanningProbe));
  assert.match(html, /data-phase="idle"/);
  assert.match(html, /data-loading="false"/);
});

test("prepareRouteSubmit waits for GPS and refuses a blank destination", () => {
  assert.deepEqual(prepareRouteSubmit(undefined, "  ", { lng: -73.99, lat: 40.7 }), {
    ok: false,
  });
  assert.deepEqual(prepareRouteSubmit("JFK", "", null), {
    ok: false,
    error: "Waiting for GPS location...",
  });
  assert.deepEqual(prepareRouteSubmit("JFK", "", { lng: -73.99, lat: 40.7 }), {
    ok: true,
    destination: "JFK",
  });
});

test("planningErrorText keeps the missing-route copy without inventing a path", () => {
  assert.match(
    planningErrorText(new Error("Failed to plan trip")),
    /No route found/,
  );
  assert.match(planningErrorText("boom"), /Connection error/);
});
