import { defineConfig, devices } from "@playwright/test";

const releaseBaseURL = process.env.SMARTROUTE_RELEASE_BASE_URL ?? "http://127.0.0.1:3100";

/**
 * The release suite only talks to the local Next server. Individual tests
 * intercept the agent stream and feed endpoints so a browser run can never
 * call a paid provider or depend on live transit data.
 */
export default defineConfig({
  testDir: "./tests/release",
  outputDir: "test-results/release",
  timeout: 30_000,
  expect: {
    timeout: 7_000,
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      maxDiffPixelRatio: 0.01,
    },
  },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["github"], ["html", { open: "never" }], ["json", { outputFile: "test-results/release/results.json" }]]
    : [["list"], ["json", { outputFile: "test-results/release/results.json" }]],
  use: {
    baseURL: releaseBaseURL,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "America/New_York",
    trace: {
      mode: "retain-on-failure",
      // Prevent the CI artifact trace from retaining network snapshots or
      // mocked request bodies. Failures still include source and screenshot
      // context for triage.
      snapshots: false,
      screenshots: true,
      sources: true,
    },
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 960 } },
    },
    {
      name: "mobile",
      use: {
        ...devices["iPhone 13"],
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
      },
    },
  ],
  webServer: process.env.SMARTROUTE_RELEASE_BASE_URL ? undefined : {
    command: "node node_modules/next/dist/bin/next dev --webpack --hostname 127.0.0.1 --port 3100",
    port: 3100,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
