import { writeFileSync } from "node:fs";
import { loadOpenDataSubwayLines, OPEN_DATA_SOURCE_DATASET_ID, OPEN_DATA_SOURCE_NAME } from "../opendata-subway-lines.ts";
import { buildOpenDataInputsStage } from "./opendata-inputs.ts";

type OpenDataVisualInputStageInput = {
  openDataLinesPath: string;
  expectedOpenDataRouteIds: string[];
  expectedEdges: number;
  topologyEdgeDiagnostics: any;
  openDataMinFragmentLengthM: number;
  paths: { opendataLinesGeoJson: string; edgesGeoJson: string; opendataOverlapsGeoJson: string };
  parameters: { overlapMinRatio: number; overlapSharedLenMinM: number; containmentAvgDistanceMaxM: number; tangentMaxDiffDeg: number };
};

export function buildOpenDataVisualInputStage({
  openDataLinesPath,
  expectedOpenDataRouteIds,
  expectedEdges,
  topologyEdgeDiagnostics,
  openDataMinFragmentLengthM,
  paths,
  parameters,
}: OpenDataVisualInputStageInput) {
  console.log("[visual-network] Gate 2B - loading NYC OpenData subway line geometry");

  const openDataLines = loadOpenDataSubwayLines(openDataLinesPath, {
    expectedRouteIds: expectedOpenDataRouteIds,
    minFragmentLengthM: openDataMinFragmentLengthM,
  });
  const opendataLineFeatures = openDataLines.features.map((feature, index) => ({
    ...feature,
    properties: {
      ...feature.properties,
      opendata_line_id: `opendata-${String(index + 1).padStart(5, "0")}`,
    },
  }));

  const edgesDoc = {
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2B OpenData normalized lines",
      parameters: {
        visual_geometry_source: OPEN_DATA_SOURCE_NAME,
        visual_geometry_source_dataset_id: OPEN_DATA_SOURCE_DATASET_ID,
        raw_opendata_path: "frontend/public/subway-lines-nyc-opendata.geojson",
        shape_selection_strategy: "nyc_opendata_full_lines",
      },
      diagnostics: {
        ...openDataLines.diagnostics,
        ...topologyEdgeDiagnostics,
      },
    },
    features: opendataLineFeatures,
  };
  writeFileSync(paths.opendataLinesGeoJson, `${JSON.stringify(edgesDoc)}\n`);
  writeFileSync(paths.edgesGeoJson, `${JSON.stringify(edgesDoc)}\n`);
  console.log(`[visual-network] wrote ${paths.opendataLinesGeoJson}`);
  console.log(`[visual-network] wrote ${paths.edgesGeoJson}`);
  console.log(
    `[visual-network] OpenData source features: ${openDataLines.diagnostics.source_feature_count}`,
  );
  console.log(
    `[visual-network] OpenData normalized line features: ${openDataLines.diagnostics.normalized_feature_count}`,
  );
  console.log(
    `[visual-network] OpenData represented routes: ${openDataLines.diagnostics.represented_route_ids.join(",")}`,
  );
  console.log(
    `[visual-network] OpenData missing expected routes: ${openDataLines.diagnostics.missing_expected_route_ids.join(",") || "none"}`,
  );
  console.log(
    `[visual-network] OpenData alias applications: ${JSON.stringify(openDataLines.diagnostics.alias_applications)}`,
  );
  console.log(
    `[visual-network] GTFS topology edges for validation: ${topologyEdgeDiagnostics.topology_edges_emitted}`,
  );

  console.log(
    `[visual-network] expected topology edges: ${expectedEdges} (emitted: ${topologyEdgeDiagnostics.topology_edges_emitted}, retention ${(topologyEdgeDiagnostics.topology_edges_emitted / Math.max(1, expectedEdges) * 100).toFixed(1)}%)`,
  );

  console.log("[visual-network] Gate 2C - OpenData corridor normalization");

  const {
    pairsConsidered,
    pairsMatched,
    matchedPairs,
    corridorFeatures,
    corridorRows,
    opendataOverlapWarnings,
  } = buildOpenDataInputsStage({
    opendataLineFeatures,
    geometrySourceName: OPEN_DATA_SOURCE_NAME,
    overlapMinRatio: parameters.overlapMinRatio,
    overlapSharedLenMinM: parameters.overlapSharedLenMinM,
    containmentAvgDistanceMaxM: parameters.containmentAvgDistanceMaxM,
    tangentMaxDiffDeg: parameters.tangentMaxDiffDeg,
  });

  writeFileSync(
    paths.opendataOverlapsGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs Gate 2C OpenData overlap sanity check",
        parameters: {
          overlap_min_ratio: parameters.overlapMinRatio,
          shared_length_min_m: parameters.overlapSharedLenMinM,
          avg_distance_max_m: parameters.containmentAvgDistanceMaxM,
          tangent_max_diff_deg: parameters.tangentMaxDiffDeg,
        },
        summary: {
          warning_count: opendataOverlapWarnings.length,
        },
      },
      features: opendataOverlapWarnings,
    })}\n`,
  );
  console.log(`[visual-network] wrote ${paths.opendataOverlapsGeoJson}`);
  console.log(`[visual-network] OpenData overlap warnings: ${opendataOverlapWarnings.length}`);

  return {
    pairsConsidered,
    pairsMatched,
    matchedPairs,
    corridorFeatures,
    corridorRows,
  };
}
