import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { writeVisualArtifactStage } from "./artifact-writer-stage.ts";
import { REMAINING_UNBUNDLED_CORRIDORS } from "./artifact-metadata.ts";
import type { BundleArtifacts } from "../shared/types.ts";

const parameters = {
  minTripsPerBranch: 5,
  resampleIntervalM: 25,
  hausdorffMaxM: 15,
  overlapMinRatio: 0.6,
  tangentMaxDiffDeg: 30,
  containmentAvgDistanceMaxM: 15,
  containmentOverlapMinRatio: 0.85,
};

function emptyArtifacts(): BundleArtifacts {
  return {
    bundleFeatures: [],
    bundleLaneFeatures: [],
    bundleGapFeatures: [],
    visualFeatures: [],
  };
}

test("promotes an empty passing candidate and keeps remaining_unbundled_corridors at 0", () => {
  const dir = mkdtempSync(join(tmpdir(), "artifact-writer-"));
  const candidatePath = join(dir, "candidate.geojson");
  const finalPath = join(dir, "final.geojson");
  writeVisualArtifactStage({
    generatedAt: "2026-01-01T00:00:00.000Z",
    openDataSourceName: "test",
    openDataSourceDatasetId: "s692-irgq",
    perRouteStats: [],
    validationFailures: [],
    bundleArtifacts: emptyArtifacts(),
    candidatePath,
    finalPath,
    parameters,
  });
  const candidate = JSON.parse(readFileSync(candidatePath, "utf8"));
  const finalDoc = JSON.parse(readFileSync(finalPath, "utf8"));
  assert.equal(candidate.metadata.bundle_summary.remaining_unbundled_corridors, REMAINING_UNBUNDLED_CORRIDORS);
  assert.deepEqual(candidate, finalDoc);
});

test("refuses to promote when a route failed connectivity validation", () => {
  const dir = mkdtempSync(join(tmpdir(), "artifact-writer-fail-"));
  const candidatePath = join(dir, "candidate.geojson");
  const finalPath = join(dir, "final.geojson");
  const previousExit = process.exit;
  // SAFETY: tests intercept process.exit; the stub has the same call signature.
  process.exit = ((code?: number) => {
    throw new Error(`exit ${code}`);
  }) as typeof process.exit;
  try {
    assert.throws(
      () =>
        writeVisualArtifactStage({
          generatedAt: "2026-01-01T00:00:00.000Z",
          openDataSourceName: "test",
          openDataSourceDatasetId: "s692-irgq",
          perRouteStats: [{ route_id: "G" }],
          validationFailures: [{ route_id: "G" }],
          bundleArtifacts: emptyArtifacts(),
          candidatePath,
          finalPath,
          parameters,
        }),
      /exit 1/,
    );
    const candidate = JSON.parse(readFileSync(candidatePath, "utf8"));
    assert.equal(candidate.metadata.validation.passed, false);
    assert.throws(() => readFileSync(finalPath, "utf8"), /ENOENT/);
  } finally {
    process.exit = previousExit;
  }
});
