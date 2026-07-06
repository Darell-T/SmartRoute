import { chromium } from "playwright";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdirSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "../..");
const outDir = resolve(frontendRoot, "screenshots");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => console.error("[page-error]", e.message));
page.on("console", (m) => {
  const t = m.type();
  if (t === "error" || t === "warning") console.log("[" + t + "]", m.text().slice(0, 200));
});

const url = "http://localhost:3000/?subway-visual=1";
console.log("[qa-real] navigating to", url);
await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });

// Wait for the MapLibre canvas to appear (means map mounted)
console.log("[qa-real] waiting for map canvas...");
await page.waitForSelector(".maplibregl-canvas", { timeout: 60000 });
console.log("[qa-real] canvas present, waiting for tiles/layers to settle (8s)...");
await page.waitForTimeout(8000);

// Default view (Midtown-ish per the app's initial centre)
await page.screenshot({ path: resolve(outDir, "qa-real-default.png"), fullPage: false });
console.log("[qa-real] wrote qa-real-default.png");

// Use mouse-drag to pan to specific scenes.
// MapLibre drag: mousedown, mousemove, mouseup. Pan amount in pixels.
// At zoom ~12, NYC, 1px ~= 0.000453 deg lon and ~= 0.000343 deg lat (Web Mercator).
// We'll pan around the default centre (-73.9857, 40.7484, z~12) toward each scene.
async function panTo(deltaLonDeg, deltaLatDeg, name) {
  // At app default zoom 12: 1 px = ~0.000453 deg lon, ~0.000343 deg lat
  const pxLon = -deltaLonDeg / 0.000453;   // east is +lon, drag map LEFT (negative px) to move view east
  const pxLat = +deltaLatDeg / 0.000343;   // north is +lat, drag map DOWN (positive py) to move view north
  const cx = 800, cy = 500;  // viewport centre
  // We need to MOVE THE VIEW to the target -- which is opposite to the drag direction.
  // Drag from cx,cy by (-pxLon, +pxLat) effectively pans the map view by (deltaLon, deltaLat).
  // Sign convention: if map needs to MOVE EAST (target lon > current lon), we drag the map WEST (negative pixel x).
  const dragDx = pxLon;
  const dragDy = pxLat;
  console.log("[qa-real] panTo " + name + ": drag(" + dragDx.toFixed(0) + "," + dragDy.toFixed(0) + "px)");
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  // MapLibre prefers a sequence of small moves for smooth panning. Skip; do one big move.
  await page.mouse.move(cx + dragDx, cy + dragDy, { steps: 20 });
  await page.mouse.up();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: resolve(outDir, `qa-real-${name}.png`), fullPage: false });
  console.log("[qa-real]   wrote qa-real-" + name + ".png");
}

// Default centre per components/smart-route/map/smart-route-map.tsx: roughly Midtown.
// Scene targets:
const DEFAULT_LON = -73.9857;
const DEFAULT_LAT = 40.7484;
const scenes = [
  { name: "atlantic-barclays", lon: -73.9772, lat: 40.6843 },
  { name: "downtown-brooklyn", lon: -73.9877, lat: 40.6926 },
  { name: "coney-island",      lon: -73.9586, lat: 40.5774 },
  { name: "dekalb-av",         lon: -73.9817, lat: 40.6905 },
];

// Pan deltas computed from default centre.
for (const s of scenes) {
  await panTo(s.lon - DEFAULT_LON, s.lat - DEFAULT_LAT, s.name);
  // Reset by reloading the page to ensure each scene starts from default centre
  // (otherwise deltas compound).
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector(".maplibregl-canvas", { timeout: 30000 });
  await page.waitForTimeout(4000);
}

await browser.close();
console.log("[qa-real] done");
