import { expect, test as base, type Page } from "@playwright/test";
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

// Install before every test body so deterministic CI catches application
// errors even though the visual snapshots run on Windows only.
export const test = base.extend<ReleaseFixtures>({
  browserErrorGate: [
    async ({ page }, use) => {
      const browserErrors = collectBrowserErrors(page);
      await use();
      await browserErrors.settle();
      expect(browserErrors.errors).toEqual([]);
    },
    { auto: true },
  ],
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
    if (url.pathname.startsWith("/api/")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      return;
    }
    await route.continue();
  });
}

export async function openSmartRoute(page: Page, requests: AgentRequest[] = []): Promise<void> {
  await installDeterministicNetwork(page, requests);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByLabel("Message SmartRoute")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Near you" })).toBeVisible();
}

export async function sendChatMessage(page: Page, text: string): Promise<void> {
  const composer = page.getByLabel("Message SmartRoute");
  const send = page.getByLabel("Send message");
  await composer.click();
  await composer.pressSequentially(text);
  await expect(composer).toHaveValue(text);
  await expect(send).toBeEnabled();
  await send.click();
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
