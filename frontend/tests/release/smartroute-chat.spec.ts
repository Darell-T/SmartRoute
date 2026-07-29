import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { chooseQuickMode, openSmartRoute, sendChatMessage, test } from "./support";

const axeCorePath = resolve(process.cwd(), "node_modules", "axe-core", "axe.min.js");

test.describe("SmartRoute deterministic chat release flows", () => {
  test("serves greeting, fare, plan, arrival, failure, and retry requests locally", async ({ page }) => {
    const requests: Array<{ message?: string; response_presentation?: string }> = [];
    await openSmartRoute(page, requests);

    await sendChatMessage(page, "hello");
    await expect(page.getByText("Good morning — where are you headed?")).toBeVisible();

    await sendChatMessage(page, "fare");
    await expect(page.getByText("A subway ride is $3.00.")).toBeVisible();

    await sendChatMessage(page, "plan");
    await expect(page.getByRole("button", { name: "Open on map" })).toBeVisible();
    await expect(page.getByText("31 min")).toBeVisible();

    await sendChatMessage(page, "arrival");
    const arrivalsCard = page.locator(".sr-chat-arrivals-card");
    await expect(arrivalsCard.getByText("Times Sq-42 St")).toBeVisible();
    await expect(arrivalsCard.getByText("4 min")).toBeVisible();

    await sendChatMessage(page, "fail");
    await expect(page.locator(".sr-chat-error-banner")).toContainText("temporarily unavailable");

    await sendChatMessage(page, "retry");
    await expect(page.getByText("Retry succeeded with grounded local test data.")).toBeVisible();
    expect(requests.map((request) => request.message)).toEqual([
      "hello", "fare", "plan", "arrival", "fail", "retry",
    ]);
    expect(requests.every((request) => request.response_presentation === "auto")).toBe(true);
  });

  test("persists Quick composer mode and sends it through the typed chat contract", async ({ page }) => {
    const requests: Array<{ message?: string; response_presentation?: string }> = [];
    await openSmartRoute(page, requests);

    await chooseQuickMode(page);
    await sendChatMessage(page, "fare");
    await expect(page.getByText("A subway ride is $3.00.")).toBeVisible();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ message: "fare", response_presentation: "quick" });
  });

  test("passes the selected route card into the existing map handoff", async ({ page }) => {
    await openSmartRoute(page);
    await sendChatMessage(page, "plan");
    await page.getByRole("button", { name: "Open on map" }).click();

    await expect(page.locator(".sr-tab-shell")).toHaveAttribute("data-tab", "livemap");
    await expect(page.locator('[data-active-tab="livemap"]')).toBeVisible();
  });

  test("keeps the chat surface free of automated accessibility violations", async ({ page }) => {
    await openSmartRoute(page);
    await sendChatMessage(page, "plan");
    await page.addScriptTag({ path: axeCorePath });

    const violations = await page.evaluate(async () => {
      type Axe = { run: (context: Document, options?: { rules?: Record<string, { enabled: boolean }> }) => Promise<{ violations: Array<{ id: string; impact: string | null; nodes: Array<{ target: string[] }> }> }> };
      const axe = (window as unknown as { axe: Axe }).axe;
      const result = await axe.run(document);
      return result.violations;
    });

    expect(violations).toEqual([]);
  });

  test("@visual matches the deterministic chat snapshot", async ({ page }) => {
    await openSmartRoute(page);
    await sendChatMessage(page, "plan");
    await expect(page.getByRole("button", { name: "Open on map" })).toBeVisible();
    await page.waitForLoadState("networkidle");
    await expect(page.locator(".sr-chat-tab-inner")).toHaveScreenshot("chat-plan.png", {
      animations: "disabled",
      caret: "hide",
    });
  });
});
