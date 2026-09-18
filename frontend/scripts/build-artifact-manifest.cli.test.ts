import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = process.cwd();
const script = path.join(frontendRoot, "scripts", "build-artifact-manifest.ts");
const tsxCli = path.join(frontendRoot, "node_modules", "tsx", "dist", "cli.mjs");

test("artifact-manifest CLI hashes cwd public GeoJSON and skips missing files", () => {
  const cwd = mkdtempSync(path.join(tmpdir(), "artifact-manifest-cli-"));
  mkdirSync(path.join(cwd, "public"), { recursive: true });
  mkdirSync(path.join(cwd, "lib"), { recursive: true });
  writeFileSync(
    path.join(cwd, "public", "subway-network.visual.geojson"),
    `${JSON.stringify({ type: "FeatureCollection", features: [] })}\n`,
  );
  writeFileSync(
    path.join(cwd, "public", "subway-network.stations.geojson"),
    `${JSON.stringify({ type: "FeatureCollection", features: [{ type: "Feature" }] })}\n`,
  );

  const first = spawnSync(process.execPath, [tsxCli, script], { cwd, encoding: "utf8" });
  assert.equal(first.status, 0, first.stderr || first.stdout);
  assert.match(first.stdout, /skipped missing subway-network\.station-anchors\.geojson/);
  const manifestPath = path.join(cwd, "lib", "artifact-manifest.json");
  const firstDoc = JSON.parse(readFileSync(manifestPath, "utf8"));
  assert.equal(Object.keys(firstDoc).length, 2);
  assert.match(firstDoc["subway-network.visual.geojson"], /^[0-9a-f]{12}$/);

  const second = spawnSync(process.execPath, [tsxCli, script], { cwd, encoding: "utf8" });
  assert.equal(second.status, 0, second.stderr || second.stdout);
  assert.deepEqual(JSON.parse(readFileSync(manifestPath, "utf8")), firstDoc);
  rmSync(cwd, { recursive: true, force: true });
});
