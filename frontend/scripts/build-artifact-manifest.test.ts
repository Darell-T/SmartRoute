import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

type ArtifactManifest = Record<string, string>;

const frontendRoot = process.cwd();
const publicDir = path.join(frontendRoot, "public");
const manifestPath = path.join(frontendRoot, "lib", "artifact-manifest.json");

function hashGitIndexLf(source: string): string {
  return createHash("sha256")
    .update(source.replaceAll("\r\n", "\n"))
    .digest("hex")
    .slice(0, 12);
}

function hashArtifact(name: string): string {
  return hashGitIndexLf(readFileSync(path.join(publicDir, name), "utf8"));
}

test("artifact manifest hashes match runtime GeoJSON artifacts", () => {
  const parsed: unknown = JSON.parse(readFileSync(manifestPath, "utf8"));
  if (parsed === null || Array.isArray(parsed) || parsed !== Object(parsed)) {
    throw new Error("artifact-manifest.json must be a JSON object");
  }
  // SAFETY: the manifest is a JSON object of artifact name to hash after the predicates above.
  const manifest = parsed as ArtifactManifest;

  for (const name of Object.keys(manifest)) {
    assert.equal(
      manifest[name],
      hashArtifact(name),
      `${name} hash is stale; run npm run build:artifact-manifest`,
    );
  }
});
