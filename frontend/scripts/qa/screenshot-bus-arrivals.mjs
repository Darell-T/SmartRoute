// One-off QA: capture the left rail's arrivals board at Herald Sq and verify
// bus rows (BusChip badges) render alongside subway bullets.
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
  viewport: { width: 1400, height: 900 },
  geolocation: { longitude: -73.9857, latitude: 40.7484 },
  permissions: ["geolocation"],
});
const page = await ctx.newPage();
const target = process.env.QA_URL || "http://localhost:3000/";
await page.goto(target, { waitUntil: "domcontentloaded", timeout: 60000 });
await page.waitForSelector(".sr-rail", { timeout: 60000 });
// Live feed arrives over WS; give it time to populate arrivals.
await page.waitForTimeout(25000);
const busChips = await page.locator(".sr-rail span[aria-label$=' bus']").count();
console.log("bus chips rendered (uptown tab):", busChips);
await page.screenshot({
  path: "screenshots/rail-bus-arrivals-uptown.png",
  clip: { x: 0, y: 0, width: 420, height: 880 },
  timeout: 60000,
});
await page.getByText("DOWNTOWN", { exact: false }).first().click();
await page.waitForTimeout(1500);
const busChipsDown = await page.locator(".sr-rail span[aria-label$=' bus']").count();
console.log("bus chips rendered (downtown tab):", busChipsDown);
await page.screenshot({
  path: "screenshots/rail-bus-arrivals-downtown.png",
  clip: { x: 0, y: 0, width: 420, height: 880 },
  timeout: 60000,
});
console.log("wrote rail-bus-arrivals-{uptown,downtown}.png");
await browser.close();
