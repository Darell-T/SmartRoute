import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

type ArtifactManifest = Record<string, string>;

const frontendRoot = process.cwd();
const publicDir = path.join(frontendRoot, "public");

const ARTIFACTS = [
  "subway-network.visual.geojson",
  "subway-network.station-anchors.geojson",
  "subway-network.stations.geojson",
] as const;

function hashGitIndexLf(source: string): string {
  return createHash("sha256")
    .update(source.replaceAll("\r\n", "\n"))
    .digest("hex")
    .slice(0, 12);
}

const manifest: ArtifactManifest = {};
const skipped: string[] = [];
for (const name of ARTIFACTS) {
  try {
    manifest[name] = hashGitIndexLf(
      readFileSync(path.join(publicDir, name), "utf8"),
    );
  } catch {
    skipped.push(name);
  }
}

const outPath = path.join(frontendRoot, "lib", "artifact-manifest.json");
writeFileSync(outPath, `${JSON.stringify(manifest, null, 2)}\n`);
console.log(
  `[artifact-manifest] wrote ${Object.keys(manifest).length} hashes -> lib/artifact-manifest.json`,
);
if (skipped.length) {
  console.log(`[artifact-manifest] skipped missing ${skipped.join(", ")}`);
}
