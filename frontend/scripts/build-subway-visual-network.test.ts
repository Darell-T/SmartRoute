import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { resolve } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";

test("visual-network builder refuses to run without the GTFS cache zip", (t) => {
  const cacheDir = mkdtempSync(resolve(tmpdir(), "smartroute-gtfs-missing-"));
  t.after(() => rmSync(cacheDir, { recursive: true, force: true }));
  const result = spawnSync(
    process.execPath,
    ["--import", "tsx", "scripts/build-subway-visual-network.ts"],
    {
      cwd: resolve(process.cwd()),
      encoding: "utf8",
      env: { ...process.env, NODE_OPTIONS: "", SMARTROUTE_GTFS_CACHE_DIR: cacheDir },
      timeout: 15_000,
    },
  );
  assert.notEqual(result.status, 0);
  assert.match(`${result.stderr}${result.stdout}`, /GTFS cache missing/);
});
