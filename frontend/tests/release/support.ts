import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { expect, test as base, type Page, type TestInfo } from "@playwright/test";
import { eventsForRequest, sseBody } from "./agent-chat-fixtures";

type AgentRequest = {
  message?: string;
  response_presentation?: string;
};

const LOCAL_ORIGIN = new URL(
  process.env.SMARTROUTE_RELEASE_BASE_URL ?? "http://127.0.0.1:3100",
).host;
const CARTO_STYLE_URL = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const MAPTILER_HOST = "api.maptiler.com";
const VERCEL_ANALYTICS_HOST = "va.vercel-scripts.com";
const EMPTY_MAP_STYLE = JSON.stringify({ version: 8, sources: {}, layers: [] });

const RELEASE_SUBWAY_STEP = {
  type: "SUBWAY",
  train_line: "2",
  route_id: "2",
  line_color: "#EE352E",
  departure_stop: "Times Sq-42 St",
  arrival_stop: "Atlantic Av-Barclays Ctr",
  start_point: { latitude: 40.7553, longitude: -73.987 },
  end_point: { latitude: 40.6844, longitude: -73.9777 },
  departure_coords: { latitude: 40.7553, longitude: -73.987 },
  arrival_coords: { latitude: 40.6844, longitude: -73.9777 },
  minutes_until_train_arrives: 4,
};

const RELEASE_ITINERARY = {
  itinerary_id: "it-release-1",
  total_duration_seconds: 1860,
  transfer_count: 0,
  departure_at: "2026-07-16T15:14:00-04:00",
  arrival_at: "2026-07-16T15:45:00-04:00",
  legs: [
    { mode: "WALK", walk_seconds: 180, board: "Times Square", alight: "Times Sq-42 St" },
    {
      mode: "SUBWAY",
      service_id: "2",
      ride_seconds: 1680,
      board: "Times Sq-42 St",
      alight: "Atlantic Av-Barclays Ctr",
      stop_count: 8,
    },
  ],
};

const RELEASE_TRIP = {
  recommendation: "Take the 2.",
  route: [RELEASE_SUBWAY_STEP],
  selected_route_index: 0,
  alerts: [],
  route_candidates: [
    {
      id: "cand-2",
      index: 0,
      is_recommended: true,
      total_minutes: 31,
      recommendation_reason: "Fastest available option",
      can_enrich_on_select: false,
      enriched: true,
      score_breakdown: {
        transfers: 0,
        active_alerts: 0,
        duration_minutes: 31,
        transit_lines: ["2"],
      },
      steps: [RELEASE_SUBWAY_STEP],
      itinerary: RELEASE_ITINERARY,
    },
    {
      id: "cand-q",
      index: 1,
      is_recommended: false,
      total_minutes: 38,
      rejection_reason: "1 extra transfer",
      can_enrich_on_select: true,
      enriched: false,
      score_breakdown: {
        transfers: 1,
        active_alerts: 1,
        duration_minutes: 38,
        transit_lines: ["Q"],
      },
      steps: [
        {
          ...RELEASE_SUBWAY_STEP,
          train_line: "Q",
          route_id: "Q",
          line_color: "#FCCC0A",
          departure_stop: "Herald Sq",
        },
      ],
      itinerary: {
        ...RELEASE_ITINERARY,
        itinerary_id: "it-release-q",
        transfer_count: 1,
        total_duration_seconds: 2280,
      },
    },
  ],
};

type BrowserErrorCollector = {
  errors: string[];
  settle: () => Promise<void>;
};

type ReleaseFixtures = {
  browserErrorGate: void;
};

export function collectBrowserErrors(page: Page): BrowserErrorCollector {
  const errors: string[] = [];
  const pending = new Set<Promise<void>>();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") {
      const pendingError = Promise.all(
        message.args().map(async (argument) => {
          try {
            return await argument.evaluate((value) => {
              if (value instanceof Error) {
                return JSON.stringify({
                  name: value.name,
                  message: value.message,
                  stack: value.stack,
                });
              }
              return JSON.stringify(value);
            });
          } catch {
            return "[unserializable console argument]";
          }
        }),
      ).then((arguments_) => {
        const location = message.location();
        errors.push(`${message.text()} ${arguments_.join(" ")} (${location.url}:${location.lineNumber})`);
      });
      pending.add(pendingError);
      void pendingError.finally(() => pending.delete(pendingError));
    }
  });
  return {
    errors,
    settle: async () => {
      await Promise.all(pending);
    },
  };
}

const OWNED_WEBPACK_MODULE =
  /(?:webpack-internal:\/\/\/\([^)]+\)\/|webpack:\/\/[^/]*\/)\.\/(components|app|lib|scripts)\//;

function compactOwnedCoverage(entries: Array<{ url: string; source?: string; functions?: unknown[] }>) {
  return entries
    .filter((entry) => OWNED_WEBPACK_MODULE.test(String(entry.url).replace(/[?#].*$/, "")))
    .map((entry) => ({
      url: entry.url,
      source: entry.source ?? "",
      functions: entry.functions ?? [],
    }));
}

async function withInlineSourceMap(source: string, page: Page): Promise<string> {
  const found = /sourceMappingURL=(\S+)/.exec(source);
  if (!found) return source;
  const spec = found[1];
  if (spec.startsWith("data:")) return source;
  const url = spec.startsWith("//") ? `http:${spec}` : spec;
  if (!url.startsWith("http://") && !url.startsWith("https://")) return source;
  try {
    const response = await page.request.get(url);
    if (!response.ok()) return source;
    const encoded = Buffer.from(await response.body()).toString("base64");
    return source.replace(
      found[0],
      `sourceMappingURL=data:application/json;base64,${encoded}`,
    );
  } catch {
    return source;
  }
}

async function persistOwnedBrowserCoverage(page: Page, testInfo: TestInfo): Promise<void> {
  const owned = compactOwnedCoverage(await page.coverage.stopJSCoverage());
  const inlined = [];
  for (const entry of owned) {
    inlined.push({
      ...entry,
      source: await withInlineSourceMap(entry.source, page),
    });
  }
  const file = path.join(
    process.cwd(),
    "coverage",
    "browser-v8",
    `${testInfo.project.name}-${testInfo.testId.replaceAll(/[^\w.-]+/g, "_")}.json`,
  );
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, `${JSON.stringify(inlined)}\n`);
}

// Install before every test body so deterministic CI catches application
// errors even though the visual snapshots run on Windows only.
export const test = base.extend<ReleaseFixtures>({
  browserErrorGate: [
    async ({ page }, provide) => {
      const browserErrors = collectBrowserErrors(page);
      await provide();
      await browserErrors.settle();
      expect(browserErrors.errors).toEqual([]);
    },
    { auto: true },
  ],
  page: async ({ page }, provide, testInfo) => {
    const collect = process.env.SMARTROUTE_BROWSER_COVERAGE === "1";
    if (collect) {
      await page.coverage.startJSCoverage({
        resetOnNavigation: false,
        reportAnonymousScripts: true,
      });
    }
    await provide(page);
    if (collect) await persistOwnedBrowserCoverage(page, testInfo);
  },
});

export async function installDeterministicNetwork(page: Page, requests: AgentRequest[]): Promise<void> {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.href === CARTO_STYLE_URL) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: EMPTY_MAP_STYLE,
      });
      return;
    }
    if (url.host === VERCEL_ANALYTICS_HOST) {
      await route.fulfill({
        status: 200,
        contentType: "application/javascript",
        body: "",
      });
      return;
    }
    if (url.host === MAPTILER_HOST) {
      // The app's optional building layer is valid only when its vector tiles
      // load. A no-content tile is MapLibre's supported no-data response and
      // keeps this deterministic chat suite offline without triggering map
      // load failures in Next's development error overlay.
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    if (url.host !== LOCAL_ORIGIN) {
      await route.abort("blockedbyclient");
      return;
    }
    if (url.pathname === "/api/agent/chat") {
      const request = route.request().postDataJSON() as AgentRequest;
      requests.push(request);
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "cache-control": "no-store" },
        body: sseBody(eventsForRequest(request)),
      });
      return;
    }
    if (url.pathname === "/api/trip") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(RELEASE_TRIP),
      });
      return;
    }
    if (url.pathname === "/api/trip/enrich-route") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          steps: RELEASE_TRIP.route_candidates[1].steps,
          enriched: true,
        }),
      });
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      return;
    }
    await route.continue();
  });
}

export async function openSmartRoute(page: Page, requests: AgentRequest[] = []): Promise<void> {
  await page.context().grantPermissions(["geolocation"]);
  await page.context().setGeolocation({ latitude: 40.7484, longitude: -73.9857 });
  await installDeterministicNetwork(page, requests);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/?qa-map=1");
  await expect(page.getByLabel("Message SmartRoute")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Near you" })).toBeVisible();
}

export async function sendChatMessage(page: Page, text: string): Promise<void> {
  const composer = page.getByLabel("Message SmartRoute");
  const send = page.getByLabel("Send message");
  await composer.click();
  await composer.fill(text);
  await expect(composer).toHaveValue(text);
  await expect(send).toBeEnabled();
  await send.click();
}

export async function installIosVisualViewportStub(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const viewport = new EventTarget() as EventTarget & {
      height: number;
      offsetTop: number;
      width: number;
      offsetLeft: number;
      pageTop: number;
      pageLeft: number;
      scale: number;
    };
    Object.assign(viewport, {
      height: 844,
      offsetTop: 0,
      width: 390,
      offsetLeft: 0,
      pageTop: 0,
      pageLeft: 0,
      scale: 1,
    });
    Object.defineProperty(window, "visualViewport", {
      configurable: true,
      value: viewport,
    });
    const win = window as Window & {
      __setSmartRouteViewport?: (height: number, offsetTop: number) => void;
    };
    win.__setSmartRouteViewport = (height, offsetTop) => {
      viewport.height = height;
      viewport.offsetTop = offsetTop;
      viewport.dispatchEvent(new Event("resize"));
      viewport.dispatchEvent(new Event("scroll"));
    };
  });
}

export async function setSmartRouteViewport(
  page: Page,
  height: number,
  offsetTop: number,
): Promise<void> {
  await page.evaluate(
    ([nextHeight, nextOffset]) => {
      const win = window as Window & {
        __setSmartRouteViewport?: (height: number, offsetTop: number) => void;
      };
      win.__setSmartRouteViewport?.(nextHeight, nextOffset);
    },
    [height, offsetTop],
  );
}

export function boxBottom(box: { y: number; height: number } | null): number {
  expect(box).not.toBeNull();
  if (!box) return 0;
  return box.y + box.height;
}

export async function chooseQuickMode(page: Page): Promise<void> {
  const trigger = page.getByRole("button", { name: "Response style: Auto" });
  await trigger.focus();
  await trigger.press("ArrowDown");
  const auto = page.getByRole("menuitemradio", { name: /Auto/ });
  await expect(auto).toBeFocused();
  await auto.press("ArrowDown");
  const quick = page.getByRole("menuitemradio", { name: /Quick/ });
  await expect(quick).toBeFocused();
  await quick.press("Enter");
  await expect(page.getByRole("button", { name: "Response style: Quick" })).toBeVisible();
}
