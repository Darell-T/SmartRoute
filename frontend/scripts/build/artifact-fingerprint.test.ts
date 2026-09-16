import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = process.cwd();
const script = path.join(frontendRoot, "scripts", "build", "artifact-fingerprint.ts");
const tsxCli = path.join(frontendRoot, "node_modules", "tsx", "dist", "cli.mjs");

test("artifact-fingerprint writes sha256 and feature counts for public GeoJSON", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "artifact-fingerprint-"));
  const outPath = path.join(dir, "fingerprints.json");
  const first = spawnSync(process.execPath, [tsxCli, script, "--out", outPath], {
    cwd: frontendRoot,
    encoding: "utf8",
  });
  assert.equal(first.status, 0, first.stderr || first.stdout);
  const firstDoc = JSON.parse(readFileSync(outPath, "utf8"));
  assert.ok(Array.isArray(firstDoc));
  assert.ok(firstDoc.length >= 1);
  const visual = firstDoc.find((entry: { file?: string }) =>
    String(entry.file).endsWith("subway-network.visual.geojson"),
  );
  assert.ok(visual);
  assert.match(visual.sha256, /^[0-9a-f]{64}$/);
  assert.ok(Number.isFinite(visual.featureCount));
  assert.ok(visual.featureCount > 0);

  const second = spawnSync(process.execPath, [tsxCli, script, "--out", outPath], {
    cwd: frontendRoot,
    encoding: "utf8",
  });
  assert.equal(second.status, 0, second.stderr || second.stdout);
  assert.deepEqual(JSON.parse(readFileSync(outPath, "utf8")), firstDoc);
  rmSync(dir, { recursive: true, force: true });
});

test("artifact-fingerprint refuses a missing --out value", () => {
  const missing = spawnSync(process.execPath, [tsxCli, script, "--out"], {
    cwd: frontendRoot,
    encoding: "utf8",
  });
  assert.notEqual(missing.status, 0);
  assert.match(`${missing.stderr}${missing.stdout}`, /Missing value after --out/);
});
