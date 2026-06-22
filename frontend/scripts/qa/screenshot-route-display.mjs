/**
 * screenshot-route-display.mjs
 *
 * End-to-end route display QA: plans a trip from the left rail search,
 * waits for narration to finish, then verifies the persistent route
 * display (stop dots, dashed walk, dimmed ambient network), switches to an
 * alternative, and clears.
 *
 * NOTE: requires a working /api/trip (Google + Anthropic credits) and the
 * dev servers on :3000/:8000.
 *
 * Usage: node scripts/qa/screenshot-route-display.mjs ["destination"]
 */
import { chromium } from "playwright";

const DESTINATION = process.argv[2] || "American Museum of Natural History";

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
  viewport: { width: 1500, height: 1000 },
  geolocation: { longitude: -73.953, latitude: 40.7715 },
  permissions: ["geolocation"],
});
const page = await ctx.newPage();
page.on("console", (m) => {
  if (m.type() === "error") console.error("[page-error]", m.text().slice(0, 200));
});

await page.goto("http://localhost:3000/?subway-visual=1&qa-map=1", {
  waitUntil: "domcontentloaded",
  timeout: 60000,
});
await page.waitForFunction(() => typeof window.__jarvisMap !== "undefined", {
  timeout: 60000,
  polling: 500,
});
await page.waitForTimeout(8000);

// 1. Plan a trip from the rail's WHERE TO input.
const input = page.locator(".sr-rail input").first();
await input.fill(DESTINATION);
await input.press("Enter");
console.log("[qa] trip requested:", DESTINATION);

// 2. Wait for the result state (steps render in the rail).
await page.waitForSelector("text=Plan", { timeout: 120000 });
console.log("[qa] plan visible");

// 3. Wait for narration to end (static display takes over).
await page.waitForFunction(
  () => {
    const map = window.__jarvisMap;
    if (!map || !map.getLayer || !map.getLayer("sr-route-stop-dot")) return false;
    const feats = map.querySourceFeatures("sr-route-stops");
    return feats && feats.length > 0;
  },
  { timeout: 180000, polling: 1000 },
);
console.log("[qa] route stop features present");

const ambientHidden = await page.evaluate(() => {
  const map = window.__jarvisMap;
  return map.getLayoutProperty("sr-subway-fill", "visibility");
});
console.log("[qa] ambient network visibility (expect none):", ambientHidden);

await page.screenshot({ path: "screenshots/route-display-static.png" });
console.log("[qa] wrote route-display-static.png");

// 4. Switch to an alternative (if any).
const altTab = page.locator(".sr-rail button", { hasText: "Alternatives" }).first();
await altTab.scrollIntoViewIfNeeded();
await altTab.click({ force: true });
await page.waitForTimeout(800);
const altRow = page
  .locator(".sr-rail button:has-text('Rejected'), .sr-rail button:has-text('Recommended')")
  .first();
if (await altRow.count()) {
  await altRow.click();
  await page.waitForTimeout(4000);
  await page.screenshot({ path: "screenshots/route-display-alternative.png" });
  console.log("[qa] wrote route-display-alternative.png (after switch)");
} else {
  console.log("[qa] no alternative rows to click");
}

// 5. Clear and confirm restore.
const clearBtn = page.locator("button[aria-label='Clear route']").first();
if (await clearBtn.count()) {
  await clearBtn.click();
  await page.waitForTimeout(2500);
  const restored = await page.evaluate(() => {
    const map = window.__jarvisMap;
    const visibility = map.getLayoutProperty("sr-subway-fill", "visibility");
    const feats = map.querySourceFeatures("sr-route-stops");
    return JSON.stringify({ visibility, routeStopCount: feats.length });
  });
  console.log("[qa] after clear:", restored.slice(0, 120));
}

await browser.close();
console.log("[qa] done");
