// Generates lib/artifact-manifest.json: a map of runtime artifact filename ->
// short content hash. The map runtime appends the hash as a `?v=` query so the
// large GeoJSON assets are cached across page loads and only re-fetched when
// their content actually changes (replacing per-load `Date.now()` busting).
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const publicDir = path.join(frontendRoot, "public");

const ARTIFACTS = [
  "subway-network.visual.geojson",
  "subway-network.station-anchors.geojson",
  "subway-network.stations.geojson",
];

const manifest = {};
for (const name of ARTIFACTS) {
  try {
    const buf = readFileSync(path.join(publicDir, name));
    manifest[name] = createHash("sha256").update(buf).digest("hex").slice(0, 12);
  } catch {
    // Artifact not present in this checkout; runtime falls back to an
    // unversioned URL for it.
  }
}

const outPath = path.join(frontendRoot, "lib", "artifact-manifest.json");
writeFileSync(outPath, `${JSON.stringify(manifest, null, 2)}\n`);
console.log(
  `[artifact-manifest] wrote ${Object.keys(manifest).length} hashes -> lib/artifact-manifest.json`,
);
