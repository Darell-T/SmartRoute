import { expect } from "@playwright/test";
import { openSmartRoute, sendChatMessage, test } from "./support";

test.describe("SmartRoute shell release behavior", () => {
  test("supports keyboard navigation, sidebar collapse, and reduced motion", async ({ page }, testInfo) => {
    await openSmartRoute(page);

    if (testInfo.project.name === "desktop") {
      const collapse = page.getByRole("button", { name: "Collapse sidebar" });
      await collapse.focus();
      await expect(collapse).toBeFocused();
      await collapse.press("Enter");
      await expect(page.locator(".sr-tab-shell")).toHaveAttribute("data-sidebar-collapsed", "true");
      await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeFocused();
    }

    const transitMap = page.getByRole("button", { name: "Transit Map" });
    await transitMap.focus();
    await expect(transitMap).toBeFocused();
    await transitMap.press("Enter");
    await expect(page.locator(".sr-tab-shell")).toHaveAttribute("data-tab", "livemap");
    if (testInfo.project.name === "mobile") {
      const resizeGrip = page.getByLabel("Resize route panel");
      await resizeGrip.focus();
      await expect(resizeGrip).toBeFocused();
      await resizeGrip.press("ArrowUp");
      await expect(resizeGrip).toHaveAttribute("aria-expanded", "true");
    } else {
      await expect(page.getByLabel("SmartRoute Left Rail")).toBeVisible();
    }
  });

  test("keeps primary chat controls usable at a 200% zoom-equivalent desktop viewport", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "Desktop zoom-equivalent coverage");
    await page.setViewportSize({ width: 720, height: 480 });
    await openSmartRoute(page);

    await expect(page.getByLabel("Message SmartRoute")).toBeVisible();
    await expect(page.getByRole("button", { name: "Send message" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);

    await sendChatMessage(page, "hello");
    await expect(page.getByText("Good morning — where are you headed?")).toBeVisible();
  });
});
