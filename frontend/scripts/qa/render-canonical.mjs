import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";
const __dirname = dirname(fileURLToPath(import.meta.url));
const sharp = (await import(pathToFileURL(resolve(__dirname, "../../node_modules/sharp/lib/index.js")).href)).default;
const can = JSON.parse(readFileSync(resolve(__dirname, "../../public/subway-network.canonical.geojson"), "utf8"));
const [minLon, maxLon, minLat, maxLat] = process.argv.slice(2, 6).map(Number);
const out = process.argv[6];
const W = 900, H = Math.round(W * (maxLat - minLat) / ((maxLon - minLon) * Math.cos(((minLat + maxLat) / 2) * Math.PI / 180)));
const sx = (x) => ((x - minLon) / (maxLon - minLon)) * W, sy = (y) => H - ((y - minLat) / (maxLat - minLat)) * H;
const inb = ([x, y]) => x >= minLon && x <= maxLon && y >= minLat && y <= maxLat;
const colorFor = (r) => r === "4" ? "#00ff00" : r === "5" ? "#00d0ff" : r === "2" ? "#ff5050" : null;
let paths = "";
for (const f of can.features) {
  const r = String(f.properties.route_id);
  const col = colorFor(r);
  if (!col) continue;
  const c = f.geometry.coordinates;
  if (!c.some(inb)) continue;
  const d = c.map((p, i) => (i ? "L" : "M") + sx(p[0]).toFixed(1) + " " + sy(p[1]).toFixed(1)).join(" ");
  paths += `<path d="${d}" fill="none" stroke="${col}" stroke-width="2" stroke-opacity="0.55" stroke-linecap="round"/>`;
}
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}"><rect width="${W}" height="${H}" fill="#111"/>${paths}</svg>`;
await sharp(Buffer.from(svg)).png().toFile(out);
console.log("wrote", out, "green=4 cyan=5 red=2");
