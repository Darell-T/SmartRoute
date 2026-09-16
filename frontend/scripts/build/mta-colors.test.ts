import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { darkenHexColor, routeColor } from "./mta-colors.ts";
import {
  bundleColorRank,
  colorRank,
  compareRouteIds,
  normalizeRouteId,
  routeColorFor,
} from "./visual-network/shared/route-config.ts";

test("routeColor and darkenHexColor use the MTA table", () => {
  assert.equal(routeColor("A"), "#0A84FF");
  assert.equal(routeColor("unknown-route"), "#808183");
  assert.match(darkenHexColor("#EE352E", 0.45), /^#[0-9A-F]{6}$/);
  assert.equal(darkenHexColor("not-a-color"), "not-a-color");
});

test("route-config aliases and ranks stay stable", () => {
  assert.equal(normalizeRouteId("6D"), "6X");
  assert.equal(normalizeRouteId("SIR"), "SI");
  assert.equal(normalizeRouteId("FS"), "FS");
  assert.equal(routeColorFor("A"), "#0A84FF");
  assert.equal(routeColorFor("missing"), "#808183");
  assert.ok(colorRank("#EE352E") < colorRank("#unknown"));
  assert.ok(bundleColorRank("#EE352E") < bundleColorRank("#unknown"));
  assert.equal(compareRouteIds("A", "B") < 0, true);
  const again = [normalizeRouteId("6D"), routeColorFor("A")];
  assert.deepEqual(again, ["6X", "#0A84FF"]);
});

test("routeColor uppercases ids, uses a custom fallback, and darkens with and without a hash", () => {
  assert.equal(routeColor("a"), "#0A84FF");
  assert.equal(routeColor("", "#111111"), "#111111");
  assert.equal(darkenHexColor("EE352E", 0), "#EE352E");
  assert.equal(darkenHexColor("#EE352E", 2), "#000000");
  assert.equal(darkenHexColor("#EE352E", -1), "#EE352E");
});

test("readMtaRouteColors rejects a missing file and a non-object JSON payload", () => {
  const frontendRoot = process.cwd();
  const tsxCli = path.join(frontendRoot, "node_modules", "tsx", "dist", "cli.mjs");
  const script = path.join(frontendRoot, "scripts", "build", "mta-colors.ts");
  const missingDir = mkdtempSync(path.join(tmpdir(), "mta-colors-missing-"));
  const missing = spawnSync(process.execPath, [tsxCli, script], { cwd: missingDir, encoding: "utf8" });
  assert.notEqual(missing.status, 0);
  assert.match(`${missing.stderr}${missing.stdout}`, /Could not locate frontend\/lib\/mta-colors\.json/);

  const badDir = mkdtempSync(path.join(tmpdir(), "mta-colors-bad-"));
  mkdirSync(path.join(badDir, "lib"), { recursive: true });
  writeFileSync(path.join(badDir, "lib", "mta-colors.json"), "[1,2,3]\n");
  const bad = spawnSync(process.execPath, [tsxCli, script], { cwd: badDir, encoding: "utf8" });
  assert.notEqual(bad.status, 0);
  assert.match(`${bad.stderr}${bad.stdout}`, /must be a JSON object/);
  writeFileSync(path.join(badDir, "lib", "mta-colors.json"), "null\n");
  const nullDoc = spawnSync(process.execPath, [tsxCli, script], { cwd: badDir, encoding: "utf8" });
  assert.notEqual(nullDoc.status, 0);
  writeFileSync(path.join(badDir, "lib", "mta-colors.json"), '{"A": 1}\n');
  const nonString = spawnSync(process.execPath, [tsxCli, script], { cwd: badDir, encoding: "utf8" });
  assert.notEqual(nonString.status, 0);
  assert.match(`${nonString.stderr}${nonString.stdout}`, /must be a string/);
  rmSync(missingDir, { recursive: true, force: true });
  rmSync(badDir, { recursive: true, force: true });
});
