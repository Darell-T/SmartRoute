import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
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

type ReportOptions = {
  error?: boolean;
  failedExtra?: boolean;
  failedRequired?: boolean;
  flaky?: boolean;
  missing?: boolean;
  retriedRequired?: boolean;
  unexpected?: boolean;
  visual?: boolean;
};

type SpecResult = { status: string; errors: { message?: string }[] };

function passingResults(retried: boolean): SpecResult[] {
  if (retried) return [{ status: "passed", errors: [] }, { status: "passed", errors: [] }];
  return [{ status: "passed", errors: [] }];
}

function specTestResults(skipZoom: boolean, failRequired: boolean, retryRequired: boolean): SpecResult[] {
  if (skipZoom) return [{ status: "skipped", errors: [] }];
  if (failRequired) return [{ status: "failed", errors: [] }];
  return passingResults(retryRequired);
}

function coverageSpec(title: string, index: number, projectName: string, options: ReportOptions) {
  const skipZoom = projectName === "mobile" && index === titles.length - 1;
  const failRequired = Boolean(options.failedRequired && projectName === "desktop" && index === 0);
  const retryRequired = Boolean(options.retriedRequired && projectName === "desktop" && index === 0);
  return {
    title,
    tags: options.visual && index === 0 && projectName === "desktop" ? ["visual"] : [],
    tests: [{
      projectName,
      expectedStatus: skipZoom ? "skipped" : "passed",
      status: skipZoom ? "skipped" : "expected",
      results: specTestResults(skipZoom, failRequired, retryRequired),
    }],
  };
}

function requiredCoverageSpecs(options: ReportOptions) {
  return titles.flatMap((title, index) =>
    ["desktop", "mobile"].map((projectName) => coverageSpec(title, index, projectName, options)),
  );
}

function extraFailedSpec() {
  const tags: string[] = [];
  return {
    title: "extra release case",
    tags,
    tests: [{
      projectName: "desktop",
      expectedStatus: "passed",
      status: "expected",
      results: [{ status: "failed", errors: [] }],
    }],
  };
}

function playwrightReport(options: ReportOptions = {}) {
  const specs = options.missing ? [] : requiredCoverageSpecs(options);
  if (options.failedExtra) specs.push(extraFailedSpec());
  return {
    errors: options.error ? [{ message: "failed" }] : [],
    stats: { unexpected: options.unexpected ? 1 : 0, flaky: options.flaky ? 1 : 0 },
    suites: [{ specs, suites: [] }],
  };
}

test("creates sanitized evidence from complete Playwright coverage", () => {
  const evidence = buildBrowserEvidence(playwrightReport(), "A1B2C3D4");

  assert.equal(evidence.status, "PASSED");
  assert.equal(evidence.candidate.commit_sha, "a1b2c3d4");
  assert.deepEqual(evidence.projects.mobile.expected_skipped_cases, ["zoom"]);
});

test("rejects malformed Playwright JSON", () => {
  assert.throws(() => buildBrowserEvidence({}, "a1b2c3d4"), /browser evidence rejected/);
});

test("rejects failed, missing, visual, and extra Playwright coverage", () => {
  const rejected = [
    playwrightReport({ error: true }),
    playwrightReport({ missing: true }),
    playwrightReport({ visual: true }),
    playwrightReport({ failedRequired: true }),
    playwrightReport({ retriedRequired: true }),
    playwrightReport({ flaky: true }),
    playwrightReport({ unexpected: true }),
    playwrightReport({ failedExtra: true }),
  ];

  for (const report of rejected) {
    assert.throws(() => buildBrowserEvidence(report, "a1b2c3d4"), /browser evidence rejected/);
  }
});

test("rejects a candidate SHA that is not hexadecimal", () => {
  assert.throws(() => buildBrowserEvidence(playwrightReport(), "not-a-sha"), /candidate SHA/);
});

test("CLI writes evidence and rejects missing arguments", () => {
  const frontendRoot = process.cwd();
  const script = path.join(frontendRoot, "scripts", "release", "build-browser-evidence.ts");
  const tsxCli = path.join(frontendRoot, "node_modules", "tsx", "dist", "cli.mjs");
  const missing = spawnSync(process.execPath, [tsxCli, script], { encoding: "utf8" });
  assert.notEqual(missing.status, 0);
  assert.match(`${missing.stderr}${missing.stdout}`, /usage: build-browser-evidence/);

  const dir = mkdtempSync(path.join(tmpdir(), "browser-evidence-"));
  const input = path.join(dir, "playwright.json");
  const output = path.join(dir, "evidence.json");
  writeFileSync(input, `${JSON.stringify(playwrightReport())}\n`);
  const ok = spawnSync(process.execPath, [tsxCli, script, input, output, "abc1234"], {
    encoding: "utf8",
  });
  assert.equal(ok.status, 0, ok.stderr || ok.stdout);
  const evidence = JSON.parse(readFileSync(output, "utf8"));
  assert.equal(evidence.status, "PASSED");
  assert.equal(evidence.candidate.commit_sha, "abc1234");
  rmSync(dir, { recursive: true, force: true });
});

test("CLI rejects malformed, array, and null Playwright JSON", () => {
  const frontendRoot = process.cwd();
  const script = path.join(frontendRoot, "scripts", "release", "build-browser-evidence.ts");
  const tsxCli = path.join(frontendRoot, "node_modules", "tsx", "dist", "cli.mjs");
  const dir = mkdtempSync(path.join(tmpdir(), "browser-evidence-bad-"));
  const output = path.join(dir, "evidence.json");
  const malformed = path.join(dir, "bad.json");
  writeFileSync(malformed, "{not json");
  const missingFile = spawnSync(process.execPath, [tsxCli, script, malformed, output, "abc1234"], { encoding: "utf8" });
  assert.notEqual(missingFile.status, 0);
  assert.match(`${missingFile.stderr}${missingFile.stdout}`, /missing or malformed/);

  writeFileSync(malformed, "[]\n");
  const arrayDoc = spawnSync(process.execPath, [tsxCli, script, malformed, output, "abc1234"], { encoding: "utf8" });
  assert.notEqual(arrayDoc.status, 0);
  assert.match(`${arrayDoc.stderr}${arrayDoc.stdout}`, /must be an object/);

  writeFileSync(malformed, "null\n");
  const nullDoc = spawnSync(process.execPath, [tsxCli, script, malformed, output, "abc1234"], { encoding: "utf8" });
  assert.notEqual(nullDoc.status, 0);
  rmSync(dir, { recursive: true, force: true });
});

test("rejects empty titles, nested suites, duplicate coverage, and dirty result errors", () => {
  const nested = {
    errors: [],
    stats: { unexpected: 0, flaky: 0 },
    suites: [{
      specs: [],
      suites: [{ specs: requiredCoverageSpecs({}), suites: undefined }],
    }],
  };
  const evidence = buildBrowserEvidence(nested, "A1B2C3D4");
  assert.equal(evidence.status, "PASSED");

  const emptyTitle = playwrightReport();
  emptyTitle.suites[0].specs[0].title = "";
  assert.throws(() => buildBrowserEvidence(emptyTitle, "a1b2c3d4"), /non-empty string/);

  const duplicate = playwrightReport();
  duplicate.suites[0].specs.push(duplicate.suites[0].specs[0]);
  assert.throws(() => buildBrowserEvidence(duplicate, "a1b2c3d4"), /missing or duplicates/);

  const dirty = playwrightReport();
  dirty.suites[0].specs[0].tests[0].results[0] = { status: "passed", errors: [{ message: "x" }] };
  assert.throws(() => buildBrowserEvidence(dirty, "a1b2c3d4"), /contains a result error/);

  const unexpectedStatus = playwrightReport();
  unexpectedStatus.suites[0].specs[0].tests[0].status = "unexpected";
  assert.throws(() => buildBrowserEvidence(unexpectedStatus, "a1b2c3d4"), /has an unexpected test/);

  const timedOut = playwrightReport({ failedExtra: true });
  timedOut.suites[0].specs.at(-1)!.tests[0].results[0].status = "timedOut";
  assert.throws(() => buildBrowserEvidence(timedOut, "a1b2c3d4"), /contains a timedOut result/);
});

test("rejects a mobile zoom case that did not skip cleanly", () => {
  const report = playwrightReport();
  const zoom = report.suites[0].specs.find((spec) => spec.tests[0].projectName === "mobile" && spec.title.includes("200%"));
  assert.ok(zoom);
  zoom.tests[0].status = "expected";
  zoom.tests[0].expectedStatus = "passed";
  zoom.tests[0].results = [{ status: "passed", errors: [] }];
  assert.throws(() => buildBrowserEvidence(report, "a1b2c3d4"), /must remain an expected skip/);
});

test("rejects an array Playwright report and a missing stats object", () => {
  assert.throws(() => buildBrowserEvidence(JSON.parse("[]"), "a1b2c3d4"), /must be an object/);

  const noStats = JSON.parse(JSON.stringify(playwrightReport()));
  delete noStats.stats;
  assert.throws(() => buildBrowserEvidence(noStats, "A1B2C3D4"), /unexpected cases/);
});

test("rejects a non-array tags field and a mobile zoom skip that still reported errors", () => {
  const tagged = JSON.parse(JSON.stringify(playwrightReport()));
  tagged.suites[0].specs[0].tags = "visual";
  assert.throws(() => buildBrowserEvidence(tagged, "a1b2c3d4"), /must be an array/);

  const zoom = playwrightReport();
  const mobileZoom = zoom.suites[0].specs.find(
    (spec) => spec.tests[0].projectName === "mobile" && spec.title.includes("200%"),
  );
  assert.ok(mobileZoom);
  mobileZoom.tests[0].results = [{ status: "skipped", errors: [{ message: "x" }] }];
  assert.throws(() => buildBrowserEvidence(zoom, "a1b2c3d4"), /contains a result error/);
});

test("treats a missing tags field as empty and rejects a duplicated required title", () => {
  const missingTags = JSON.parse(JSON.stringify(playwrightReport()));
  delete missingTags.suites[0].specs[0].tags;
  const ok = buildBrowserEvidence(missingTags, "a1b2c3d4");
  assert.equal(ok.status, "PASSED");

  const dup = playwrightReport();
  const copy = structuredClone(dup.suites[0].specs[0]);
  dup.suites[0].specs.push(copy);
  assert.throws(() => buildBrowserEvidence(dup, "a1b2c3d4"), /missing or duplicates/);
});
