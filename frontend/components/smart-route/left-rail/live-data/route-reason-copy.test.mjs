import assert from "node:assert/strict";
import test from "node:test";

import { buildVisibleRouteReason, publicRecommendationText } from "./route-reason-copy.ts";

function subway(routeId, stop, extra = {}) {
  return {
    type: "SUBWAY",
    route_id: routeId,
    departure_stop: stop,
    arrival_stop: "Dest",
    minutes_until_train_arrives: 4,
    ...extra,
  };
}

function candidate(overrides) {
  return {
    id: "a",
    index: 0,
    is_recommended: true,
    recommendation_reason: "the A train is faster",
    steps: [subway("A", "Jay")],
    score_breakdown: { active_alerts: 0, transfers: 0 },
    ...overrides,
  };
}

test("visible route reason uses live arrivals, alert counts, and why-not phrases", () => {
  assert.equal(publicRecommendationText("Very well, ATLAS, sir."), "SmartRoute.");
  const recommended = candidate({
    recommendation_reason: "recommend: because this option is fastest",
  });
  const extraTransfer = candidate({
    id: "b",
    index: 1,
    is_recommended: false,
    rejection_reason: "1 extra transfer",
    steps: [subway("C", "Jay"), subway("F", "Bergen")],
  });
  const moreWalking = candidate({
    id: "c",
    index: 2,
    is_recommended: false,
    rejection_reason: "More walking",
    steps: [subway("Q", "DeKalb")],
  });
  const later = candidate({
    id: "d",
    index: 3,
    is_recommended: false,
    rejection_reason: "Later departure",
    steps: [subway("N", "Canal")],
  });
  const delayed = candidate({
    id: "e",
    index: 4,
    is_recommended: false,
    rejection_reason: "Affected by delays",
    steps: [subway("R", "Union")],
  });
  const fasterRisk = candidate({
    id: "f",
    index: 5,
    is_recommended: false,
    rejection_reason: "faster by 4 min · delays downtown",
    steps: [subway("B", "Pacific")],
  });
  const faster = candidate({
    id: "g",
    index: 6,
    is_recommended: false,
    rejection_reason: "faster overall",
    steps: [subway("D", "Herald")],
  });
  const slower = candidate({
    id: "h",
    index: 7,
    is_recommended: false,
    rejection_reason: "slower by 6 min",
    steps: [subway("E", "World")],
  });
  const generic = candidate({
    id: "i",
    index: 8,
    is_recommended: false,
    rejection_reason: "crowded cars",
    steps: [subway("F", "West 4")],
  });
  const similar = candidate({
    id: "j",
    index: 9,
    is_recommended: false,
    rejection_reason: "similar time",
    steps: [subway("G", "Clinton")],
  });

  const withAlerts = candidate({
    id: "k",
    recommendation_reason: "",
    score_breakdown: { active_alerts: 2, transfers: 0 },
    steps: [subway("A", "Jay", { minutes_until_train_arrives: 3 })],
  });
  const oneAlert = candidate({
    id: "l",
    recommendation_reason: "[CANDIDATE_ANALYSIS] json payload",
    score_breakdown: { active_alerts: 1, transfers: 0 },
  });
  const altRoute = candidate({
    id: "m",
    is_recommended: false,
    recommendation_reason: "",
    rejection_reason: "",
    steps: [{ type: "BUS", route_id: "B41", departure_stop: "Atlantic" }],
  });
  const duplicateA = candidate({
    id: "n",
    recommendation_reason: "the A train and the A train run often",
    steps: [subway("A", "High")],
  });
  const longReason = candidate({
    recommendation_reason: "x".repeat(140),
  });
  const indexed = candidate({
    recommendation_reason: "route 1 is better than route index notes",
  });
  const markdown = candidate({
    recommendation_reason: "because **this** option wins",
  });

  assert.match(
    buildVisibleRouteReason(recommended, recommended.steps, [recommended, extraTransfer, moreWalking]),
    /I did not pick/,
  );
  assert.match(
    buildVisibleRouteReason(recommended, recommended.steps, [recommended, later, delayed]),
    /leaves later|affected by delays/,
  );
  assert.match(
    buildVisibleRouteReason(recommended, recommended.steps, [recommended, fasterRisk, faster]),
    /min faster|lower reliability/,
  );
  assert.match(
    buildVisibleRouteReason(recommended, recommended.steps, [recommended, slower, generic]),
    /slower|crowded/,
  );
  assert.doesNotMatch(
    buildVisibleRouteReason(recommended, recommended.steps, [recommended, similar]),
    /I did not pick/,
  );
  assert.match(buildVisibleRouteReason(withAlerts, withAlerts.steps, [withAlerts]), /2 service alerts/);
  assert.match(buildVisibleRouteReason(oneAlert, oneAlert.steps, [oneAlert]), /1 service alert/);
  assert.match(buildVisibleRouteReason(altRoute, altRoute.steps, [altRoute]), /Alternative route|bus option/);
  assert.match(buildVisibleRouteReason(duplicateA, duplicateA.steps, [duplicateA, candidate({ id: "dup", steps: [subway("A", "Low")] })]), /from |alternatives|A route/);
  assert.match(buildVisibleRouteReason(longReason, longReason.steps, [longReason]), /\.\.\.|xx/);
  assert.equal(buildVisibleRouteReason(indexed, indexed.steps, [indexed, extraTransfer]).includes("route 1"), false);
  assert.match(buildVisibleRouteReason(markdown, markdown.steps, [markdown]), /This route wins|wins/);
});
