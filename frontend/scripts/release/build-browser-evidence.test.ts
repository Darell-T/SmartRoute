import assert from "node:assert/strict";
import test from "node:test";

import { buildBrowserEvidence } from "./build-browser-evidence.ts";

const titles = [
  "serves greeting, fare, plan, arrival, failure, and retry requests locally",
  "persists Quick composer mode and sends it through the typed chat contract",
  "passes the selected route card into the existing map handoff",
  "keeps the chat surface free of automated accessibility violations",
  "supports keyboard navigation, sidebar collapse, and reduced motion",
  "keeps primary chat controls usable at a 200% zoom-equivalent desktop viewport",
];

function report(options: {
  error?: boolean;
  failedExtra?: boolean;
  failedRequired?: boolean;
  flaky?: boolean;
  missing?: boolean;
  retriedRequired?: boolean;
  unexpected?: boolean;
  visual?: boolean;
} = {}): object {
  const selectedTitles = options.missing ? [] : titles;
  const specs = selectedTitles.flatMap((title, index) => ["desktop", "mobile"].map((projectName) => {
    const mobileZoom = projectName === "mobile" && index === titles.length - 1;
    const failedRequired = options.failedRequired && projectName === "desktop" && index === 0;
    const retriedRequired = options.retriedRequired && projectName === "desktop" && index === 0;
    return {
      title,
      tags: options.visual && index === 0 && projectName === "desktop" ? ["visual"] : [],
      tests: [{
        projectName,
        expectedStatus: mobileZoom ? "skipped" : "passed",
        status: mobileZoom ? "skipped" : "expected",
        results: mobileZoom ? [{ status: "skipped", errors: [] }] : retriedRequired
          ? [{ status: "passed", errors: [] }, { status: "passed", errors: [] }]
          : [{ status: failedRequired ? "failed" : "passed", errors: [] }],
      }],
    };
  }));
  if (options.failedExtra) {
    specs.push({
      title: "extra release case",
      tags: [],
      tests: [{
        projectName: "desktop",
        expectedStatus: "passed",
        status: "expected",
        results: [{ status: "failed", errors: [] }],
      }],
    });
  }
  return {
    errors: options.error ? [{ message: "failed" }] : [],
    stats: { unexpected: options.unexpected ? 1 : 0, flaky: options.flaky ? 1 : 0 },
    suites: [{ specs, suites: [] }],
  };
}

test("creates sanitized evidence from complete Playwright coverage", () => {
  const evidence = buildBrowserEvidence(report(), "A1B2C3D4");

  assert.equal(evidence.status, "PASSED");
  assert.equal(evidence.candidate.commit_sha, "a1b2c3d4");
  assert.deepEqual(evidence.projects.mobile.expected_skipped_cases, ["zoom"]);
});

test("rejects malformed, failed, missing, and visual Playwright reports", () => {
  const cases: Array<[string, unknown]> = [
    ["malformed", {}],
    ["failed", report({ error: true })],
    ["missing", report({ missing: true })],
    ["visual", report({ visual: true })],
    ["failed required", report({ failedRequired: true })],
    ["retried required", report({ retriedRequired: true })],
    ["flaky stats", report({ flaky: true })],
    ["unexpected stats", report({ unexpected: true })],
    ["failed extra", report({ failedExtra: true })],
  ];

  for (const [name, value] of cases) {
    assert.throws(() => buildBrowserEvidence(value, "a1b2c3d4"), /browser evidence rejected/, name);
  }
  assert.throws(() => buildBrowserEvidence(report(), "not-a-sha"), /candidate SHA/);
});
