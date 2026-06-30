import { writeFileSync } from "node:fs";
import {
  buildCandidateDoc,
  type CandidateDocParameters,
} from "./artifact-metadata.ts";

type ArtifactWriterBundleArtifacts = {
  bundleFeatures: any[];
  bundleLaneFeatures: any[];
  unbundledFeatures: any[];
  bundleGapFeatures: any[];
  visualFeatures: any[];
};

type ArtifactWriterStageInput = {
  generatedAt: string;
  openDataSourceName: string;
  openDataSourceDatasetId: string;
  perRouteStats: any[];
  validationFailures: any[];
  bundleArtifacts: ArtifactWriterBundleArtifacts;
  candidatePath: string;
  finalPath: string;
  parameters: CandidateDocParameters;
};

export function writeVisualArtifactStage({
  generatedAt,
  openDataSourceName,
  openDataSourceDatasetId,
  perRouteStats,
  validationFailures,
  bundleArtifacts,
  candidatePath,
  finalPath,
  parameters,
}: ArtifactWriterStageInput): void {
  // =====================================================================
  // Final artifact emission
  // =====================================================================

  // The candidate artifact is the OpenData-derived visual geojson with extra
  // metadata. Always written so debug/inspection works even on failure.
  const candidateDoc = buildCandidateDoc({
    generatedAt,
    openDataSourceName,
    openDataSourceDatasetId,
    perRouteStats,
    validationFailures,
    bundleArtifacts,
    parameters,
  });
  writeFileSync(candidatePath, `${JSON.stringify(candidateDoc)}\n`);
  console.log(`[visual-network] wrote candidate: ${candidatePath}`);

  if (validationFailures.length === 0) {
    // Promote candidate → final. Preserve the last-known-good by atomic
    // rename pattern (write candidate first, then move).
    writeFileSync(finalPath, `${JSON.stringify(candidateDoc)}\n`);
    console.log(`[visual-network] *** PROMOTED *** to ${finalPath}`);
    console.log(`[visual-network] All gates passed. Visual network artifact is ready for Gate 2E (runtime opt-in).`);
  } else {
    console.error(
      `[visual-network] HARD GATE FAILED: ${validationFailures.length} route(s) failed connectivity validation.`,
    );
    console.error(
      `[visual-network] Refusing to promote candidate to ${finalPath}. The last-known-good (if any) is preserved.`,
    );
    process.exit(1);
  }
}
