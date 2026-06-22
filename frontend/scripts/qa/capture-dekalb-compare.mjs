// One-off: capture the DeKalb / Nevins / Atlantic-Barclays junction from the QA
// harness at framings matched to docs/assets/apple-maps-refs/ref2.png so the
// render can be diffed side-by-side against Apple Maps. Portrait aspect to match
// the phone screenshots. Output -> frontend/screenshots/cmp-*.png
import { chromium } from "playwright";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdirSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "../..");
const outDir = resolve(frontendRoot, "screenshots");
mkdirSync(outDir, { recursive: true });

const BASE_URL = process.env.QA_BASE_URL || "http://localhost:3000";

// ref2.png: Nevins St (top) -> Atlantic-Barclays -> split (bottom). Portrait.
const SCENES = [
  { name: "v-bronx-green",     lat: 40.8340, lng: -73.8580, zoom: 14.6 },
  { name: "v-nostrand-branch", lat: 40.6650, lng: -73.9500, zoom: 14.8 },
  { name: "v-queensboro",      lat: 40.7510, lng: -73.9410, zoom: 15.2 },
  { name: "v-brighton-bq",     lat: 40.5950, lng: -73.9610, zoom: 14.6 },
  { name: "v-6thave-orange",   lat: 40.7575, lng: -73.9820, zoom: 14.9 },
  { name: "v-schermerhorn",    lat: 40.6880, lng: -73.9840, zoom: 15.4 },
  { name: "v-dekalb",          lat: 40.6850, lng: -73.9785, zoom: 14.8 },
  { name: "dekalb-ref2",       lat: 40.6852, lng: -73.9800, zoom: 14.6 },
  { name: "dekalb-ref2-tight", lat: 40.6845, lng: -73.9788, zoom: 15.2 },
  { name: "dekalb-wide",       lat: 40.6880, lng: -73.9820, zoom: 14.0 },
  { name: "junction-node",     lat: 40.6845, lng: -73.9785, zoom: 16.0 },
  { name: "south-bundle",      lat: 40.6805, lng: -73.9775, zoom: 16.0 },
  { name: "north-approach",    lat: 40.6895, lng: -73.9818, zoom: 16.0 },
  { name: "teardrop-zoom",     lat: 40.68265, lng: -73.97645, zoom: 17.6 },
  { name: "fanout-456-crown",  lat: 40.66990, lng: -73.95189, zoom: 17.4 },
  { name: "fanout-nqrw-dekalb",lat: 40.69238, lng: -73.98278, zoom: 17.4 },
  { name: "fanout-f-jamaica",  lat: 40.70677, lng: -73.81905, zoom: 17.4 },
  { name: "g-culver",          lat: 40.6770, lng: -73.9890, zoom: 14.2 },
  { name: "eastern-pkwy-nostrand", lat: 40.6685, lng: -73.9420, zoom: 14.4 },
];

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 620, height: 1340 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();
page.on("pageerror", (e) => console.error("[page-error]", e.message));
page.on("console", (m) => { if (m.type() === "error") console.error("[console-error]", m.text()); });

const url = `${BASE_URL}/qa-visual-map.html`;
console.log(`[cmp] navigating to ${url}`);
await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
await page.waitForFunction(() => window.__qaReady === true && window.__qaMap, { timeout: 25000 });
await page.waitForTimeout(2000);

for (const scene of SCENES) {
  await page.evaluate(({ lat, lng, zoom }) => {
    window.__qaMap.jumpTo({ center: [lng, lat], zoom, pitch: 0, bearing: 0 });
  }, scene);
  await page.waitForTimeout(3500);
  const out = resolve(outDir, `cmp-${scene.name}.png`);
  await page.screenshot({ path: out, fullPage: false });
  console.log(`[cmp] wrote ${out}`);
}

await browser.close();
console.log("[cmp] done");
