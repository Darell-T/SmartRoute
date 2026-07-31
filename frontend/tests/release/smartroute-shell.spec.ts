import { expect, type Page } from "@playwright/test";
import { openSmartRoute, sendChatMessage, test } from "./support";

async function dragMobileNavigationClosed(page: Page) {
  const dialog = page.getByRole("dialog", { name: "SmartRoute navigation" });
  const pageEdge = page.getByRole("button", { name: "Close navigation" });
  const edgeBox = await pageEdge.boundingBox();
  expect(edgeBox).not.toBeNull();
  if (!edgeBox) return;

  const stage = page.locator(".sr-mobile-stage");
  const openStageBox = await stage.boundingBox();
  expect(openStageBox).not.toBeNull();
  if (!openStageBox) return;

  const startX = edgeBox.x + edgeBox.width / 2;
  const y = edgeBox.y + edgeBox.height / 2;
  await page.mouse.move(startX, y);
  await page.mouse.down();
  await page.mouse.move(startX - 166, y, { steps: 8 });
  const draggedStageBox = await stage.boundingBox();
  expect(draggedStageBox).not.toBeNull();
  expect(draggedStageBox?.x).toBeLessThan(openStageBox.x - 100);
  await page.mouse.up();

  await expect(dialog).toBeHidden();
}

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

    if (testInfo.project.name === "mobile") {
      await page.getByRole("button", { name: "Open navigation menu" }).click();
      await expect(page.getByRole("dialog", { name: "SmartRoute navigation" })).toBeVisible();
      await expect(page.getByRole("button", { name: "New Trip" })).toBeFocused();
    }

    const transitMap = page.getByRole("button", { name: /^Transit Map/ });
    await transitMap.focus();
    await expect(transitMap).toBeFocused();
    await transitMap.press("Enter");
    await expect(page.locator(".sr-tab-shell")).toHaveAttribute("data-tab", "livemap");
    if (testInfo.project.name === "mobile") {
      await expect(page.getByRole("dialog", { name: "SmartRoute navigation" })).toBeHidden();
      const resizeGrip = page.getByLabel("Resize route panel");
      await resizeGrip.focus();
      await expect(resizeGrip).toBeFocused();
      await resizeGrip.press("ArrowUp");
      await expect(resizeGrip).toHaveAttribute("aria-expanded", "true");
    } else {
      await expect(page.getByLabel("SmartRoute Left Rail")).toBeVisible();
    }
  });

  test("dismisses mobile navigation by tapping or dragging the page edge", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "Mobile gesture coverage");
    await openSmartRoute(page);

    const openMenu = page.getByRole("button", { name: "Open navigation menu" });
    const dialog = page.getByRole("dialog", { name: "SmartRoute navigation" });
    const pageEdge = page.getByRole("button", { name: "Close navigation" });

    await openMenu.click();
    await pageEdge.click();
    await expect(dialog).toBeHidden();

    await openMenu.click();
    await dragMobileNavigationClosed(page);

    await page.emulateMedia({ reducedMotion: "no-preference" });
    await openMenu.click();
    await dragMobileNavigationClosed(page);

    await openMenu.click();
    await pageEdge.click();
    await expect(dialog).toBeHidden();
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
