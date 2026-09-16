import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { resolve } from "node:path";

test("visual-network builder refuses to run without the GTFS cache zip", () => {
  const result = spawnSync(
    process.execPath,
    ["--import", "tsx", "scripts/build-subway-visual-network.ts"],
    {
      cwd: resolve(process.cwd()),
      encoding: "utf8",
      env: { ...process.env, NODE_OPTIONS: "" },
    },
  );
  assert.notEqual(result.status, 0);
  assert.match(`${result.stderr}${result.stdout}`, /GTFS cache missing/);
});
