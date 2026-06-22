import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";
const __dirname = dirname(fileURLToPath(import.meta.url));
const sharp = (await import(pathToFileURL(resolve(__dirname, "../../node_modules/sharp/lib/index.js")).href)).default;

const v = JSON.parse(readFileSync(resolve(__dirname, "../../public/subway-network.visual.geojson"), "utf8"));
const [minLon, maxLon, minLat, maxLat] = process.argv.slice(2, 6).map(Number);
const out = process.argv[6];
const W = 760;
const H = Math.round(W * (maxLat - minLat) / ((maxLon - minLon) * Math.cos(((minLat + maxLat) / 2) * Math.PI / 180)));
const sx = (x) => ((x - minLon) / (maxLon - minLon)) * W;
const sy = (y) => H - ((y - minLat) / (maxLat - minLat)) * H;
const inb = ([x, y]) => x >= minLon && x <= maxLon && y >= minLat && y <= maxLat;
let casing = "", fill = "";
for (const f of v.features) {
  if (f.geometry?.type !== "LineString") continue;
  const c = f.geometry.coordinates;
  if (!c.some(inb)) continue;
  const col = f.properties.color || "#888";
  const d = c.map((p, i) => (i ? "L" : "M") + sx(p[0]).toFixed(1) + " " + sy(p[1]).toFixed(1)).join(" ");
  casing += `<path d="${d}" fill="none" stroke="#000" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/>`;
  fill += `<path d="${d}" fill="none" stroke="${col}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
}
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}"><rect width="${W}" height="${H}" fill="#111"/>${casing}${fill}</svg>`;
await sharp(Buffer.from(svg)).png().toFile(out);
console.log("wrote", out, W + "x" + H);
