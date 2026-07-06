// One-off visual QA for the transit-line polish pass. The app flies to the
// geolocated user position once (z15.6, pitch 0), so each scene gets its own
// browser context with FAKE GEOLOCATION at the target station — the app's own
// intro camera frames the scene for us, flat.
import { chromium } from "playwright";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdirSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "../..");
const outDir = resolve(frontendRoot, "screenshots");
mkdirSync(outDir, { recursive: true });

const SCENES = [
  // 50 St (1, single-route) + 49 St (N/R/W) + 47-50 Sts Rockefeller (B/D/F/M)
  { name: "polish-midtown", center: [-73.982, 40.7605] },
  // Atlantic Av-Barclays: capsules, dense multi-route badges
  { name: "polish-atlantic", center: [-73.9779, 40.6843] },
  // Cathedral Pkwy (110 St) on the 1: single-route with a long wrapped name
  { name: "polish-cathedral", center: [-73.9669, 40.804] },
];

const browser = await chromium.launch({ headless: true });

for (const scene of SCENES) {
  const ctx = await browser.newContext({
    viewport: { width: 1400, height: 900 },
    deviceScaleFactor: 2,
    geolocation: { longitude: scene.center[0], latitude: scene.center[1] },
    permissions: ["geolocation"],
  });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.error("[page-error]", e.message));

  await page.goto("http://localhost:3000/", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await page.waitForSelector(".maplibregl-canvas", { timeout: 60000 });
  // Wait for the intro flyTo (2s animation) + tiles + overlay data to settle.
  await page.waitForTimeout(14000);

  await page.screenshot({
    path: resolve(outDir, `${scene.name}.png`),
    clip: { x: 430, y: 120, width: 740, height: 660 },
    timeout: 90000,
    animations: "disabled",
  });
  console.log("wrote", scene.name);
  await ctx.close();
}

await browser.close();
