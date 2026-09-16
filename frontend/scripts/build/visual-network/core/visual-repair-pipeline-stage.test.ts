import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { applyVisualRepairPipelineStage } from "./visual-repair-pipeline-stage.ts";

test("empty visual features run the repair pipeline and fail Mott Haven schematic QA", () => {
  const dir = mkdtempSync(join(tmpdir(), "visual-repair-"));
  const canonicalGeoJsonPath = join(dir, "canonical.geojson");
  const stationsGeoJsonPath = join(dir, "stations.geojson");
  writeFileSync(canonicalGeoJsonPath, `${JSON.stringify({ type: "FeatureCollection", features: [] })}\n`);
  writeFileSync(stationsGeoJsonPath, `${JSON.stringify({ type: "FeatureCollection", features: [] })}\n`);
  const previousExit = process.exit;
  // SAFETY: tests intercept process.exit; the stub has the same call signature.
  process.exit = ((code?: number) => {
    throw new Error(`exit ${code}`);
  }) as typeof process.exit;
  try {
    assert.throws(
      () =>
        applyVisualRepairPipelineStage({
          bundleArtifacts: { visualFeatures: [] },
          canonicalGeoJsonPath,
          stationsGeoJsonPath,
          branchesByRoute: new Map(),
          stopsById: new Map(),
          sameColorCollapseDistM: 12,
          smoothAngleThresholdDeg: 35,
          smoothIterations: 3,
          smoothRatio: 0.22,
          smoothMaxFilletM: 18,
          tightCurveTurnDeg: 65,
          tightCurveWindowM: 50,
          tightCurveIterations: 45,
          tightCurveLambda: 0.5,
          sameColorSnapDistM: 14,
          fanoutBlendM: 100,
          bridgeMinGapM: 6,
          bridgeMaxGapM: 28,
          bridgeSubsetConnectorMaxGapM: 90,
          offRevenueMaxM: 55,
        }),
      /exit 1/,
    );
  } finally {
    process.exit = previousExit;
  }
});
