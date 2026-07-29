import { readFile, writeFile } from "node:fs/promises";

const SHA_PATTERN = /^[0-9a-f]{7,64}$/i;

const REQUIRED_CASES = [
  ["chat", "serves greeting, fare, plan, arrival, failure, and retry requests locally"],
  ["quick_mode", "persists Quick composer mode and sends it through the typed chat contract"],
  ["map_handoff", "passes the selected route card into the existing map handoff"],
  ["accessibility", "keeps the chat surface free of automated accessibility violations"],
  ["shell", "supports keyboard navigation, sidebar collapse, and reduced motion"],
  ["zoom", "keeps primary chat controls usable at a 200% zoom-equivalent desktop viewport"],
] as const;

type RequiredCaseId = (typeof REQUIRED_CASES)[number][0];

interface ProjectCoverage {
  passed_required_cases: RequiredCaseId[];
  expected_skipped_cases: RequiredCaseId[];
}

export interface BrowserEvidence {
  schema_version: 1;
  candidate: { commit_sha: string };
  status: "PASSED";
  runner: "playwright";
  required_cases: RequiredCaseId[];
  projects: { desktop: ProjectCoverage; mobile: ProjectCoverage };
  visual_comparison: { certified: false; scope: "platform_local_not_certified_in_linux_ci" };
}

interface PlaywrightTest {
  expectedStatus: string;
  projectName: string;
  status: string;
  results: unknown[];
}

interface PlaywrightSpec {
  title: string;
  tags: string[];
  tests: PlaywrightTest[];
}

function fail(reason: string): never {
  throw new Error(`browser evidence rejected: ${reason}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) {
    fail(`${label} must be an object`);
  }
  return value;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || !value) {
    fail(`${label} must be a non-empty string`);
  }
  return value;
}

function list(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    fail(`${label} must be an array`);
  }
  return value;
}

function specsFromSuite(value: unknown): PlaywrightSpec[] {
  const suite = record(value, "Playwright suite");
  const specs = list(suite.specs, "Playwright suite specs").map((spec) => {
    const row = record(spec, "Playwright spec");
    const tags = list(row.tags ?? [], "Playwright spec tags").map((tag) =>
      text(tag, "Playwright tag"),
    );
    const tests = list(row.tests, "Playwright spec tests").map((test) => {
      const result = record(test, "Playwright test");
      return {
        expectedStatus: text(result.expectedStatus, "Playwright expected test status"),
        projectName: text(result.projectName, "Playwright project name"),
        status: text(result.status, "Playwright test status"),
        results: list(result.results, "Playwright test results"),
      };
    });
    return {
      title: text(row.title, "Playwright spec title"),
      tags,
      tests,
    };
  });
  const nestedSuites = suite.suites === undefined
    ? []
    : list(suite.suites, "Playwright nested suites");
  return [...specs, ...nestedSuites.flatMap(specsFromSuite)];
}

function matchingTest(
  specs: PlaywrightSpec[],
  project: "desktop" | "mobile",
  id: RequiredCaseId,
  title: string,
): PlaywrightTest {
  const matching = specs.filter(
    (spec) => spec.title === title && spec.tests.some((test) => test.projectName === project),
  );
  if (matching.length !== 1) {
    fail(`${project} coverage is missing or duplicates ${id}`);
  }
  const test = matching[0].tests.find((candidate) => candidate.projectName === project);
  if (!test) fail(`${project} coverage is missing ${id}`);
  return test;
}

function passedCaseIds(
  specs: PlaywrightSpec[],
  project: "desktop" | "mobile",
  cases: readonly (typeof REQUIRED_CASES)[number][],
): RequiredCaseId[] {
  const passed: RequiredCaseId[] = [];
  for (const [id, title] of cases) {
    const test = matchingTest(specs, project, id, title);
    if (test.status !== "expected" || test.results.length !== 1) {
      fail(`${project} ${id} did not complete as one expected result`);
    }
    const result = record(test.results[0], "Playwright result");
    if (
      text(result.status, "Playwright result status") !== "passed"
      || list(result.errors, "Playwright result errors").length
    ) {
      fail(`${project} ${id} did not pass cleanly`);
    }
    passed.push(id);
  }
  return passed;
}

function validateAllTests(specs: PlaywrightSpec[]): void {
  for (const spec of specs) {
    for (const test of spec.tests) {
      if (test.status === "unexpected" || test.status === "flaky") {
        fail(`${test.projectName} has an ${test.status} test`);
      }
      for (const rawResult of test.results) {
        const result = record(rawResult, "Playwright result");
        const status = text(result.status, "Playwright result status");
        if (
          ["failed", "timedOut", "interrupted", "unexpected", "flaky"].includes(status)
        ) {
          fail(`${test.projectName} contains a ${status} result`);
        }
        if (list(result.errors, "Playwright result errors").length) {
          fail(`${test.projectName} contains a result error`);
        }
      }
    }
  }
}

export function buildBrowserEvidence(
  report: unknown,
  commitSha: string,
): BrowserEvidence {
  if (!SHA_PATTERN.test(commitSha)) {
    fail("candidate SHA must be a 7-64 character hexadecimal Git SHA");
  }
  const root = record(report, "Playwright report");
  if (list(root.errors, "Playwright report errors").length) {
    fail("Playwright report contains errors");
  }
  const stats = record(root.stats, "Playwright report stats");
  for (const name of ["unexpected", "flaky"] as const) {
    if (stats[name] !== 0) {
      fail(`Playwright report has ${name} cases`);
    }
  }
  const specs = list(root.suites, "Playwright report suites").flatMap(
    specsFromSuite,
  );
  if (
    specs.some(
      (spec) => spec.tags.includes("visual") || spec.title.startsWith("@visual"),
    )
  ) {
    fail("visual comparison must remain excluded from Linux CI evidence");
  }
  validateAllTests(specs);
  const desktop = passedCaseIds(specs, "desktop", REQUIRED_CASES);
  const mobile = passedCaseIds(specs, "mobile", REQUIRED_CASES.slice(0, -1));
  const [zoomId, zoomTitle] = REQUIRED_CASES.at(-1)
    ?? fail("zoom coverage is not configured");
  const mobileZoom = matchingTest(specs, "mobile", zoomId, zoomTitle);
  if (
    mobileZoom.expectedStatus !== "skipped"
    || mobileZoom.status !== "skipped"
    || mobileZoom.results.length !== 1
  ) {
    fail("mobile zoom coverage must remain an expected skip");
  }
  const mobileZoomResult = record(
    mobileZoom.results[0],
    "Playwright mobile zoom result",
  );
  if (
    text(mobileZoomResult.status, "Playwright mobile zoom result status") !== "skipped"
    || list(mobileZoomResult.errors, "Playwright mobile zoom result errors").length
  ) {
    fail("mobile zoom coverage did not skip cleanly");
  }
  return {
    schema_version: 1,
    candidate: { commit_sha: commitSha.toLowerCase() },
    status: "PASSED",
    runner: "playwright",
    required_cases: REQUIRED_CASES.map(([id]) => id),
    projects: {
      desktop: { passed_required_cases: desktop, expected_skipped_cases: [] },
      mobile: { passed_required_cases: mobile, expected_skipped_cases: [zoomId] },
    },
    visual_comparison: {
      certified: false,
      scope: "platform_local_not_certified_in_linux_ci",
    },
  };
}

async function main(): Promise<void> {
  const [input, output, commitSha] = process.argv.slice(2);
  if (!input || !output || !commitSha) {
    fail(
      "usage: build-browser-evidence.ts <playwright-results.json> <evidence.json> <commit-sha>",
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readFile(input, "utf8"));
  } catch {
    fail("Playwright JSON result is missing or malformed");
  }
  await writeFile(
    output,
    `${JSON.stringify(buildBrowserEvidence(parsed, commitSha))}\n`,
    "utf8",
  );
}

if (process.argv[1]?.endsWith("build-browser-evidence.ts")) {
  void main().catch((error: unknown) => {
    const message = error instanceof Error
      ? error.message
      : "browser evidence generation failed";
    process.stderr.write(`${message}\n`);
    process.exitCode = 1;
  });
}
