import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const publicDir = path.join(frontendRoot, "public");
const manifestPath = path.join(frontendRoot, "lib", "artifact-manifest.json");

function hashArtifact(name) {
  return createHash("sha256")
    .update(readFileSync(path.join(publicDir, name)))
    .digest("hex")
    .slice(0, 12);
}

test("artifact manifest hashes match runtime GeoJSON artifacts", () => {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));

  for (const name of Object.keys(manifest)) {
    assert.equal(
      manifest[name],
      hashArtifact(name),
      `${name} hash is stale; run node frontend/scripts/build-artifact-manifest.mjs`,
    );
  }
});
