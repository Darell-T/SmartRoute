/**
 * screenshot-bundle-zooms.mjs
 *
 * Low-zoom bundle legibility QA: captures the zoom range (z11-13.5) where
 * Apple Maps keeps parallel lines individually readable. Compare against
 * docs/assets/apple-maps-refs.
 *
 * Usage:
 *   node scripts/qa/screenshot-bundle-zooms.mjs <label>
 * Writes screenshots/bundle-zooms-<label>-<scene>.png
 *
 * Requires: dev server at http://localhost:3000/
 */
import { chromium } from "playwright";

const label = process.argv[2] || "current";

const SCENES = [
  { name: "citywide-z11.3", lng: -73.9600, lat: 40.7200, zoom: 11.3 },
  { name: "midtown-z12.5", lng: -73.9850, lat: 40.7450, zoom: 12.5 },
  { name: "downtown-bk-z12.5", lng: -73.9850, lat: 40.6950, zoom: 12.5 },
  { name: "qb-lic-z13", lng: -73.9300, lat: 40.7460, zoom: 13 },
];

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1100 } });
const page = await ctx.newPage();
await page.goto("http://localhost:3000/?subway-visual=1&qa-map=1", {
  waitUntil: "domcontentloaded",
  timeout: 60000,
});
await page.waitForFunction(() => typeof window.__jarvisMap !== "undefined", {
  timeout: 60000,
  polling: 500,
});
await page.waitForTimeout(6000);

for (const scene of SCENES) {
  await page.evaluate(
    ({ lng, lat, zoom }) => {
      window.__jarvisMap.jumpTo({ center: [lng, lat], zoom, pitch: 0, bearing: 0 });
    },
    scene,
  );
  await page.waitForTimeout(2500);
  const path = `screenshots/bundle-zooms-${label}-${scene.name}.png`;
  await page.screenshot({ path, fullPage: false });
  console.log("wrote", path);
}
await browser.close();
