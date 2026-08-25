import { expect, type Page } from "@playwright/test";
import { openSmartRoute, sendChatMessage, test } from "./support";

async function dragMobileNavigationClosed(page: Page) {
  const dialog = page.getByRole("dialog", { name: "SmartRoute navigation" });
  const pageEdge = page.locator(".sr-mobile-stage-dismiss");
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
      await expect(page.locator(".sr-mobile-top-bar__brand")).toHaveCount(0);
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
    const pageEdge = page.locator(".sr-mobile-stage-dismiss");

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

  test("keeps the composer inside the panned iOS visual viewport", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "Mobile visual viewport coverage");
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
      (window as Window & {
        __setSmartRouteViewport?: (height: number, offsetTop: number) => void;
      }).__setSmartRouteViewport = (height, offsetTop) => {
        viewport.height = height;
        viewport.offsetTop = offsetTop;
        viewport.dispatchEvent(new Event("resize"));
        viewport.dispatchEvent(new Event("scroll"));
      };
    });
    await openSmartRoute(page);
    // Let hydration finish before Playwright temporarily hides carets for the
    // capture; mutating textarea styles during hydration creates a false
    // mismatch that cannot occur in the product.
    await page.waitForTimeout(250);
    await page.screenshot({ path: testInfo.outputPath("390x844-keyboard-closed.png") });

    const composer = page.getByLabel("Message SmartRoute");
    await composer.focus();
    await page.evaluate(() => {
      (window as Window & {
        __setSmartRouteViewport?: (height: number, offsetTop: number) => void;
      }).__setSmartRouteViewport?.(420, 96);
    });
    await page.waitForTimeout(20);

    const stageBox = await page.locator(".sr-mobile-stage").boundingBox();
    const composerBox = await page.locator(".sr-chat-composer").boundingBox();
    const threadBox = await page.locator(".sr-chat-thread").boundingBox();
    expect(stageBox).not.toBeNull();
    expect(composerBox).not.toBeNull();
    expect(threadBox).not.toBeNull();
    expect(Math.round(stageBox?.y ?? 0)).toBe(96);
    expect(Math.round(stageBox?.height ?? 0)).toBe(420);
    expect((composerBox?.y ?? 0) + (composerBox?.height ?? 0)).toBeLessThanOrEqual(516);
    expect(threadBox?.height ?? 0).toBeGreaterThan(0);
    await expect(page.locator(".sr-chat-empty__suggestions")).toBeHidden();

    await composer.fill("First line\nSecond line\nThird line");
    await expect(composer).toBeVisible();
    await expect(composer).toHaveValue("First line\nSecond line\nThird line");
    await page.screenshot({ path: testInfo.outputPath("390x844-keyboard-open.png") });

    await composer.fill("");
    await composer.blur();
    await page.setViewportSize({ width: 375, height: 667 });
    await page.evaluate(() => {
      (window as Window & {
        __setSmartRouteViewport?: (height: number, offsetTop: number) => void;
      }).__setSmartRouteViewport?.(667, 0);
    });
    await page.waitForTimeout(20);
    const compactComposerBox = await page.locator(".sr-chat-composer").boundingBox();
    expect(compactComposerBox).not.toBeNull();
    expect(
      (compactComposerBox?.y ?? 0) + (compactComposerBox?.height ?? 0),
    ).toBeLessThanOrEqual(667);
    await expect(page.locator(".sr-chat-empty__suggestions")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("375x667-keyboard-closed.png") });
  });

  test("renders one recoverable failed turn and retries without a duplicate user bubble", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "Mobile failed-turn coverage");
    await openSmartRoute(page);
    let attempts = 0;
    await page.route("**/api/agent/chat", async (route) => {
      attempts += 1;
      if (attempts <= 2) {
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          headers: { "x-smartroute-request-id": "release-test-request" },
          body: [
            "event: meta\ndata: {\"session_id\":\"s1\",\"turn_id\":\"t1\"}\n\n",
            "event: error\ndata: {\"code\":\"upstream_error\",\"message\":\"SmartRoute couldn’t complete this request.\",\"retryable\":true}\n\n",
            "event: done\ndata: {\"session_id\":\"s1\",\"turn_id\":\"t1\",\"stop_reason\":\"error\",\"usage\":{}}\n\n",
          ].join(""),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          "event: meta\ndata: {\"session_id\":\"s1\",\"turn_id\":\"t1\"}\n\n",
          "event: token\ndata: {\"text\":\"Your route is ready.\"}\n\n",
          "event: done\ndata: {\"session_id\":\"s1\",\"turn_id\":\"t1\",\"stop_reason\":\"end_turn\",\"usage\":{}}\n\n",
        ].join(""),
      });
    });

    const prompt = "Plan a trip to Coney Island with less walking";
    await sendChatMessage(page, prompt);
    const failure = page.locator(".sr-chat-turn-error");
    await expect(failure).toHaveCount(1);
    await expect(failure).toContainText("SmartRoute couldn’t complete this request.");
    await expect(page.locator(".sr-chat-error-banner")).toHaveCount(0);
    await expect(page.getByText("Upstream request failed.")).toHaveCount(0);
    await expect(page.getByLabel("Message SmartRoute")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("390x844-failed-turn.png") });

    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByText("Your route is ready.")).toBeVisible();
    await expect(failure).toHaveCount(0);
    await expect(page.getByText(prompt, { exact: true })).toHaveCount(1);
    expect(attempts).toBe(3);
  });
});
