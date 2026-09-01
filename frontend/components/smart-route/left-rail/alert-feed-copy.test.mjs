import assert from "node:assert/strict";
import test from "node:test";

import {
  alertSlug,
  cleanPassengerAlertText,
  compactAlertSummary,
  compactAlertTimestamp,
  compactAlertTitle,
  compactFeedTitle,
  deriveLifecycle,
  leadSentences,
  parseAlertAlternatives,
  sentenceCase,
  splitSentences,
} from "./alert-feed-copy.ts";

test("passenger alert copy rewrites titles, summaries, and feed kinds", () => {
  assert.equal(cleanPassengerAlertText(undefined), "");
  assert.equal(deriveLifecycle("service restored"), "resolved");
  assert.equal(deriveLifecycle("crews are on scene"), "monitoring");
  assert.equal(deriveLifecycle("delays"), "active");
  assert.deepEqual(splitSentences(undefined), []);
  assert.ok(splitSentences("Trains at Jay St. Expect delays.").length >= 1);
  assert.equal(leadSentences(undefined), undefined);
  assert.match(leadSentences("First. Second. Third.", 2, 20) ?? "", /First/);
  assert.ok((leadSentences("Short. This second sentence will not fit in the cap.", 3, 18) ?? "").length <= 18);
  assert.match(leadSentences("A verylongtokenwithoutspaces that overflows the cap.", 1, 12) ?? "", /A/);
  assert.match(compactAlertTitle("Partial suspension - Canal and and DeKalb"), /between/);
  assert.equal(compactAlertTitle(""), "Service alert");
  assert.match(compactAlertTitle("Person needed medical attention at Jay St"), /Medical assistance/);
  assert.match(compactAlertTitle("Partial suspension - Canal and DeKalb"), /between/);
  assert.match(compactAlertTitle("Q trains are running with delays downtown"), /Delays/);
  assert.match(compactAlertTitle("There is no Q service between Canal and DeKalb."), /No service/);
  assert.match(compactAlertTitle("Q runs every 8 minutes late nights"), /Runs every 8 minutes/);
  assert.equal(compactAlertTitle("Q trains are running express"), "Running express");
  assert.equal(compactAlertTitle("Q trains are running local"), "Running local");
  assert.equal(compactAlertTitle("Q trains skipping DeKalb"), "Skipping stations");
  assert.equal(compactAlertTitle("Additional late-night service"), "Additional service");
  assert.equal(compactAlertTitle("Q trains", ["Q"]), "Trains running with delays");
  assert.match(compactAlertTitle("[Q] trains are delayed downtown"), /delayed|Delays/i);
  assert.equal(compactAlertSummary("Same", "Same"), undefined);
  assert.match(compactAlertSummary("MTA ALL trains btwn Canal") ?? "", /All trains/);
  assert.equal(compactAlertTimestamp(undefined), "now");
  assert.equal(compactAlertTimestamp("live"), "live");
  assert.equal(compactAlertTimestamp("5 min ago"), "5 min");
  assert.match(compactFeedTitle("medical - Jay St", ["Q"]), /Medical assistance/);
  assert.match(compactFeedTitle("fire response - Canal", []), /Fire response/);
  assert.match(compactFeedTitle("police activity - Union Sq", []), /Police activity/);
  assert.equal(compactFeedTitle("stalled train", ["Q"]), "Stalled Q train");
  assert.equal(compactFeedTitle("stalled", []), "Stalled train");
  assert.match(compactFeedTitle("partial suspension - Canal and DeKalb", []), /Partial suspension/);
  assert.match(compactFeedTitle("Signal problem - DeKalb", []), /at DeKalb/);
  assert.match(parseAlertAlternatives("Take the R downtown. For alternative service use the B.", undefined) ?? "", /Take the R|alternative/i);
  assert.match(parseAlertAlternatives(undefined, "~ 9 PM") ?? "", /Expected to clear/);
  assert.equal(parseAlertAlternatives(undefined, "-"), undefined);
  assert.equal(alertSlug("Q trains @ Jay St!"), "q-trains-jay-st");
  assert.equal(sentenceCase(""), "");
  assert.equal(sentenceCase("delays downtown"), "Delays downtown");
});
