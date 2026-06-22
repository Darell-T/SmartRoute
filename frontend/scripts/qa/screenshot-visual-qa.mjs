// Visual QA screenshot script for the subway visual renderer.
// Launches Chromium, navigates to ?subway-visual=1, flies the map camera
// to each QA scene, and saves a screenshot per scene.
//
// Usage:  node scripts/qa/screenshot-visual-qa.mjs

import { chromium } from "playwright";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdirSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "../..");
const outDir = resolve(frontendRoot, "screenshots");
mkdirSync(outDir, { recursive: true });

const SCENES = [
  { name: "manhattan-trunks",   lat: 40.7549, lng: -73.9840, zoom: 13 },
  { name: "downtown-brooklyn",  lat: 40.6926, lng: -73.9877, zoom: 14 },
  { name: "atlantic-barclays",  lat: 40.6843, lng: -73.9772, zoom: 14 },
  { name: "eastern-parkway",    lat: 40.6712, lng: -73.9440, zoom: 14 },
  { name: "prospect-park",      lat: 40.6600, lng: -73.9700, zoom: 14 },
  { name: "roosevelt-island",   lat: 40.7570, lng: -73.9540, zoom: 13 },
  { name: "bronx-25-trunk",     lat: 40.8350, lng: -73.8980, zoom: 13 },
  { name: "lexington-456",      lat: 40.7580, lng: -73.9680, zoom: 13 },
];

const BASE_URL = process.env.QA_BASE_URL || "http://localhost:3000";

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
const page = await ctx.newPage();

page.on("pageerror", (e) => console.error("[page-error]", e.message));
page.on("console", (m) => {
  if (m.type() === "error") console.error("[page-console-error]", m.text());
});

// Use the standalone QA harness page (public/qa-visual-map.html) so we
// don't need to expose the React app's map instance globally.
const url = `${BASE_URL}/qa-visual-map.html`;
console.log(`[qa] navigating to ${url}`);
await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });

console.log("[qa] waiting for harness ready");
await page.waitForFunction(
  () => window.__qaReady === true && window.__qaMap,
  { timeout: 25000 },
);
// Give the source-add a tick to render
await page.waitForTimeout(2000);

for (const scene of SCENES) {
  console.log(`[qa] scene ${scene.name} → jumpTo(${scene.lng}, ${scene.lat}) z=${scene.zoom}`);
  await page.evaluate(({ lat, lng, zoom }) => {
    const map = window.__qaMap;
    map.jumpTo({ center: [lng, lat], zoom, pitch: 0, bearing: 0 });
  }, scene);
  await page.waitForTimeout(4500);
  const out = resolve(outDir, `qa-${scene.name}.png`);
  await page.screenshot({ path: out, fullPage: false });
  console.log(`[qa]   wrote ${out}`);
}

await browser.close();
console.log("[qa] done");
