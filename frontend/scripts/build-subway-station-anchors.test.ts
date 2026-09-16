import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import type { JsonValue } from "./build/types.ts";

const frontendRoot = process.cwd();
const script = path.join(frontendRoot, "scripts", "build-subway-station-anchors.ts");
const tsxCli = path.join(frontendRoot, "node_modules", "tsx", "dist", "cli.mjs");

function withColorSource(cwd: string): void {
  mkdirSync(path.join(cwd, "lib"), { recursive: true });
  copyFileSync(
    path.join(frontendRoot, "lib", "mta-colors.json"),
    path.join(cwd, "lib", "mta-colors.json"),
  );
}

function writeCollection(filePath: string, features: JsonValue[]): void {
  writeFileSync(
    filePath,
    `${JSON.stringify({ type: "FeatureCollection", features })}\n`,
  );
}

function runWithVisualDocument(document: JsonValue, directoryLabel: string) {
  const cwd = mkdtempSync(path.join(tmpdir(), `station-anchors-cli-${directoryLabel}-`));
  withColorSource(cwd);
  mkdirSync(path.join(cwd, "public"), { recursive: true });
  writeFileSync(
    path.join(cwd, "public", "subway-network.visual.geojson"),
    `${JSON.stringify(document)}\n`,
  );
  writeCollection(path.join(cwd, "public", "subway-network.stations.geojson"), []);
  const result = spawnSync(process.execPath, [tsxCli, script], { cwd, encoding: "utf8" });
  rmSync(cwd, { recursive: true, force: true });
  return result;
}

test("build-subway-station-anchors writes runtime anchors under cwd public, not the repo public tree", () => {
  const cwd = mkdtempSync(path.join(tmpdir(), "station-anchors-cli-"));
  withColorSource(cwd);
  mkdirSync(path.join(cwd, "public"), { recursive: true });
  writeCollection(path.join(cwd, "public", "subway-network.visual.geojson"), [
    {
      type: "Feature",
      properties: {
        corridor_id: "red-main",
        physical_bundle_id: "red-main",
        route_ids: ["1"],
        color_route_ids: ["1"],
        color: "#EE352E",
      },
      geometry: {
        type: "LineString",
        coordinates: [
          [-73.001, 40],
          [-72.999, 40],
        ],
      },
    },
  ]);
  writeCollection(path.join(cwd, "public", "subway-network.stations.geojson"), [
    {
      type: "Feature",
      properties: { station_id: "101", name: "Simple", route_ids: ["1"] },
      geometry: { type: "Point", coordinates: [-73, 40.00008] },
    },
  ]);

  const first = spawnSync(process.execPath, [tsxCli, script], { cwd, encoding: "utf8" });
  assert.equal(first.status, 0, first.stderr || first.stdout);
  const output = path.join(cwd, "public", "subway-network.station-anchors.geojson");
  const firstDoc = JSON.parse(readFileSync(output, "utf8"));
  assert.equal(firstDoc.type, "FeatureCollection");
  assert.ok(firstDoc.features.length >= 3);

  const second = spawnSync(process.execPath, [tsxCli, script], { cwd, encoding: "utf8" });
  assert.equal(second.status, 0, second.stderr || second.stdout);
  const secondDoc = JSON.parse(readFileSync(output, "utf8"));
  assert.deepEqual(secondDoc.features, firstDoc.features);
  rmSync(cwd, { recursive: true, force: true });
});

test("build-subway-station-anchors fails when the stations artifact is missing", () => {
  const cwd = mkdtempSync(path.join(tmpdir(), "station-anchors-cli-missing-"));
  withColorSource(cwd);
  mkdirSync(path.join(cwd, "public"), { recursive: true });
  const result = spawnSync(process.execPath, [tsxCli, script], { cwd, encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /subway-network\.stations\.geojson is required input/);
  rmSync(cwd, { recursive: true, force: true });
});

test("build-subway-station-anchors rejects a visual file that is not a FeatureCollection", () => {
  const cwd = mkdtempSync(path.join(tmpdir(), "station-anchors-cli-bad-visual-"));
  withColorSource(cwd);
  mkdirSync(path.join(cwd, "public"), { recursive: true });
  writeFileSync(
    path.join(cwd, "public", "subway-network.visual.geojson"),
    `${JSON.stringify({ type: "Feature", geometry: { type: "Point", coordinates: [0, 0] } })}\n`,
  );
  writeCollection(path.join(cwd, "public", "subway-network.stations.geojson"), []);
  const result = spawnSync(process.execPath, [tsxCli, script], { cwd, encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /must be a FeatureCollection/);
  rmSync(cwd, { recursive: true, force: true });
});

for (const scenario of [
  { name: "primitive", document: 42 },
  { name: "null", document: null },
  { name: "array", document: [] },
]) {
  test(`build-subway-station-anchors rejects ${scenario.name} top-level JSON`, () => {
    const result = runWithVisualDocument(scenario.document, scenario.name);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /subway-network\.visual\.geojson must be a FeatureCollection/);
  });
}

test("build-subway-station-anchors rejects a stations file that is not a FeatureCollection", () => {
  const cwd = mkdtempSync(path.join(tmpdir(), "station-anchors-cli-bad-stations-"));
  withColorSource(cwd);
  mkdirSync(path.join(cwd, "public"), { recursive: true });
  writeCollection(path.join(cwd, "public", "subway-network.visual.geojson"), []);
  writeFileSync(
    path.join(cwd, "public", "subway-network.stations.geojson"),
    `${JSON.stringify({ type: "Point", coordinates: [0, 0] })}\n`,
  );
  const result = spawnSync(process.execPath, [tsxCli, script], { cwd, encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /must be a FeatureCollection/);
  rmSync(cwd, { recursive: true, force: true });
});
