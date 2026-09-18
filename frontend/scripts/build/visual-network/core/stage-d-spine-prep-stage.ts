import { writeFileSync } from "node:fs";
import { buildSpineFromCorridor } from "../../spine.ts";
import {
  groupSpinesIntoPhysicalBundles,
  selectPhysicalBundleSpine,
  computePhysicalBundleSpineHash,
  type PhysicalBundleGroup,
  type Spine,
  type TransitiveDiagnostic,
} from "../../physical-bundle.ts";
import { orderColorsForBundle } from "../../lane-order.ts";
import { dedupeDuplicateCorridors } from "../../dedupe-duplicate-corridors.ts";
import { materializePhysicalBundles, type CorridorFeature } from "../../physical-bundle-materialization.ts";
import { densifyLongSegments } from "../../smooth-polyline.ts";
import { assertSpineHashConsistency } from "../../spine-validation.ts";
import { buildBundleArtifacts } from "./bundle-stage.ts";
import { geometryStats } from "../shared/geometry-utils.ts";
import { compareRouteIds, propertyString, routeColorFor } from "../shared/route-config.ts";
import type { LineFeature } from "../shared/types.ts";

type StageDPaths = {
  spinesGeoJson: string;
  transitiveBundlesGeoJson: string;
  materializedBundlesGeoJson: string;
  materializedBundleFanoutsGeoJson: string;
  materializedBundleSplitsGeoJson: string;
  materializedBundleDefectsGeoJson: string;
  physicalBundlesGeoJson: string;
  physicalBundleLanesGeoJson: string;
  physicalBundleRejectsGeoJson: string;
};

type StageDParameters = {
  openDataMinFragmentLengthM: number;
  densifyMaxSegmentM: number;
  densifyStepM: number;
  physicalBundleSubstituteConfidenceMin: number;
  bundleOverlapDistMaxM: number;
  bundleSharedLenMinM: number;
  bundleSplitSampleM: number;
  fanoutBlendM: number;
  laneWidthM: number;
};

type StageDSpinePrepStageInput = {
  corridorFeatures: LineFeature[];
  paths: StageDPaths;
  parameters: StageDParameters;
};

type CorridorSpine = ReturnType<typeof buildSpineFromCorridor>;

const GTFS_SOURCE_IDS_FIELD = "source_shape_ids";

function prepareCorridorsForSpines(
  corridorFeatures: LineFeature[],
  minLengthM: number,
  maxSegmentM: number,
  stepM: number,
) {
  let pruned = 0;
  for (let index = corridorFeatures.length - 1; index >= 0; index -= 1) {
    const feature = corridorFeatures[index];
    if (feature.properties?.visual_feature_type === "same_color_branch_connector") continue;
    if (geometryStats(feature.geometry.coordinates).length_m >= minLengthM) continue;
    corridorFeatures.splice(index, 1);
    pruned += 1;
  }
  console.log(`[visual-network] post-snap degenerate corridors pruned: ${pruned}`);

  const dedup = dedupeDuplicateCorridors(corridorFeatures, { parallelDistM: 25, overlapRatioMin: 0.8 });
  if (dedup.removedIds.length > 0) {
    corridorFeatures.length = 0;
    corridorFeatures.push(...dedup.features);
    console.log(`[visual-network] duplicate corridors deduped:           ${dedup.removedIds.length}`);
  }

  let densified = 0;
  for (const feature of corridorFeatures) {
    if (feature.geometry?.type !== "LineString") continue;
    const before = feature.geometry.coordinates;
    const after = densifyLongSegments(before, maxSegmentM, stepM);
    if (after === before) continue;
    feature.geometry.coordinates = after;
    densified += 1;
  }
  console.log(`[visual-network] coarse corridors densified:            ${densified}`);
}

function rebuildSpineArtifacts(
  corridorFeatures: LineFeature[],
  spinesByCorridorId: Map<string, CorridorSpine>,
  spinesGeoJson: string,
) {
  spinesByCorridorId.clear();
  const spineFeatures: LineFeature[] = [];
  for (const feature of corridorFeatures) {
    const spine = buildSpineFromCorridor(feature);
    spinesByCorridorId.set(propertyString(feature.properties.corridor_id) ?? "", spine);
    spineFeatures.push({
      type: "Feature",
      // SAFETY: spine geometry is the corridor LineString copied by buildSpineFromCorridor.
      geometry: spine.geometry as LineFeature["geometry"],
      properties: {
        visual_feature_type: "spine",
        spine_id: spine.spine_id,
        base_corridor_id: spine.base_corridor_id,
        base_spine_hash: spine.base_spine_hash,
        base_geometry_selection: spine.method,
        route_ids: spine.route_ids,
        source_edge_ids: spine.source_edge_ids,
        [GTFS_SOURCE_IDS_FIELD]: spine[GTFS_SOURCE_IDS_FIELD],
        length_m: spine.length_m,
      },
    });
  }
  spineFeatures.sort((a, b) => String(a.properties.spine_id).localeCompare(String(b.properties.spine_id)));
  writeFileSync(spinesGeoJson, `${JSON.stringify({ type: "FeatureCollection", features: spineFeatures })}\n`);
  return spineFeatures.length;
}

function groupingSpines(corridorFeatures: LineFeature[], spinesByCorridorId: Map<string, CorridorSpine>): Spine[] {
  const spines: Spine[] = [];
  for (const feature of corridorFeatures) {
    const spine = spinesByCorridorId.get(propertyString(feature.properties.corridor_id) ?? "");
    if (!spine) continue;
    spines.push({
      spine_id: spine.spine_id,
      // SAFETY: spine geometry is the corridor LineString copied by buildSpineFromCorridor.
      geometry: spine.geometry as Spine["geometry"],
      length_m: spine.length_m ?? 0,
      route_ids: spine.route_ids,
    });
  }
  return spines;
}

function writeTransitiveDiagnostics(
  diagnostics: TransitiveDiagnostic[],
  spinesById: Map<string, Spine>,
  path: string,
) {
  writeFileSync(
    path,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs physical bundle scoped-run diagnostics",
        summary: {
          transitive_disjoint_overlap_count: diagnostics.length,
        },
      },
      features: diagnostics.map((diagnostic) => {
        const base = spinesById.get(diagnostic.base_spine_id);
        return {
          type: "Feature",
          geometry: base?.geometry ?? null,
          properties: {
            visual_feature_type: "physical_bundle_transitive_diagnostic",
            ...diagnostic,
          },
        };
      }),
    })}\n`,
  );
}

function stampPhysicalBundleMetadata(
  corridorFeatures: LineFeature[],
  physicalBundles: PhysicalBundleGroup[],
  spinesByCorridorId: Map<string, CorridorSpine>,
  spinesById: Map<string, Spine>,
) {
  const spineIdToCorridorFeature = new Map<string, LineFeature>();
  for (const feature of corridorFeatures) {
    const spine = spinesByCorridorId.get(propertyString(feature.properties.corridor_id) ?? "");
    if (spine) spineIdToCorridorFeature.set(spine.spine_id, feature);
  }
  const physicalBundleSpines: LineFeature[] = [];
  for (const group of physicalBundles) {
    const bundleSpine = selectPhysicalBundleSpine(group, spinesById);
    const bundleHash = computePhysicalBundleSpineHash(bundleSpine.geometry.coordinates);
    group.physical_bundle_spine_hash = bundleHash;
    physicalBundleSpines.push({
      type: "Feature",
      geometry: bundleSpine.geometry,
      properties: {
        visual_feature_type: "physical_bundle_spine",
        physical_bundle_id: group.physical_bundle_id,
        base_spine_id: bundleSpine.base_spine_id,
        physical_bundle_spine_hash: bundleHash,
        member_spine_ids: bundleSpine.member_spine_ids,
        route_ids: bundleSpine.route_ids,
        member_count: group.member_count,
        confidence: group.confidence,
        substituted: false,
      },
    });
    for (const memberSpineId of bundleSpine.member_spine_ids) {
      const feature = spineIdToCorridorFeature.get(memberSpineId);
      if (!feature) {
        console.warn(
          `[visual-network] WARN: physical bundle ${group.physical_bundle_id} references unknown spine_id ${memberSpineId}`,
        );
        continue;
      }
      feature.properties.physical_bundle_id = group.physical_bundle_id;
      feature.properties.physical_bundle_spine_hash = bundleHash;
      feature.properties.physical_bundle_member_count = group.member_count;
      feature.properties.physical_bundle_confidence = group.confidence;
      feature.properties.physical_bundle_substituted = false;
    }
  }
  return physicalBundleSpines;
}

type DebugCollection = {
  type: "FeatureCollection";
  features: object[];
  metadata?: {
    generated_at: string;
    source: string;
    parameters?: {
      confidence_min: number;
      overlap_dist_max_m: number;
      shared_len_min_m: number;
      split_sample_m: number;
      fanout_blend_m: number;
    };
    summary?: {
      materialized_bundle_count?: number;
      consumed_corridor_count?: number;
      fanout_count?: number;
    };
  };
};

function writeCollection(path: string, payload: DebugCollection) {
  writeFileSync(path, `${JSON.stringify(payload)}\n`);
}

function writeMaterializationDebug(
  materialization: ReturnType<typeof materializePhysicalBundles>,
  paths: StageDPaths,
  parameters: StageDParameters,
) {
  writeCollection(paths.materializedBundlesGeoJson, {
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
      parameters: {
        confidence_min: parameters.physicalBundleSubstituteConfidenceMin,
        overlap_dist_max_m: parameters.bundleOverlapDistMaxM,
        shared_len_min_m: parameters.bundleSharedLenMinM,
        split_sample_m: parameters.bundleSplitSampleM,
        fanout_blend_m: parameters.fanoutBlendM,
      },
      summary: {
        materialized_bundle_count: materialization.materialized_bundle_count,
        consumed_corridor_count: materialization.consumed_corridor_count,
      },
    },
    features: materialization.debug.materializedBundleFeatures,
  });
  writeCollection(paths.materializedBundleFanoutsGeoJson, {
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
      summary: { fanout_count: materialization.fanout_count },
    },
    features: materialization.debug.fanoutFeatures,
  });
  writeCollection(paths.materializedBundleSplitsGeoJson, {
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
    },
    features: materialization.debug.splitFeatures,
  });
  writeCollection(paths.materializedBundleDefectsGeoJson, {
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
    },
    features: materialization.debug.defectFeatures,
  });
}

function writePhysicalBundleDebug(
  physicalBundleSpines: LineFeature[],
  rejects: ReturnType<typeof groupSpinesIntoPhysicalBundles>["rejects"],
  paths: StageDPaths,
) {
  physicalBundleSpines.sort((a, b) =>
    String(a.properties.physical_bundle_id).localeCompare(String(b.properties.physical_bundle_id)),
  );
  writeCollection(paths.physicalBundlesGeoJson, {
    type: "FeatureCollection",
    features: physicalBundleSpines,
  });
  writeCollection(paths.physicalBundleLanesGeoJson, {
    type: "FeatureCollection",
    features: [],
  });
  writeCollection(paths.physicalBundleRejectsGeoJson, {
    type: "FeatureCollection",
    features: rejects.map((reject) => ({
      type: "Feature",
      geometry: null,
      properties: { ...reject, visual_feature_type: "physical_bundle_reject" },
    })),
  });
}

function failStageDIfInconsistent(result: ReturnType<typeof assertSpineHashConsistency>) {
  const failed =
    result.inconsistentGroups.length > 0 ||
    result.lanesWithMissingSpineId.length > 0 ||
    result.lanesWithMissingHash.length > 0 ||
    result.inconsistentPhysicalBundleGroups.length > 0;
  if (!failed) {
    console.log(`[visual-network] Stage D validation:         PASS`);
    console.log(`[visual-network] Phase 3c gates:             scheduled after Phase 3b promotion`);
    return;
  }
  console.error("[visual-network] *** Stage D validation FAILED -- refusing to promote. ***");
  for (const group of result.inconsistentGroups.slice(0, 10)) {
    console.error(`  spine ${group.spine_id}: expected hash ${group.expected}, got ${group.got}`);
  }
  if (result.inconsistentGroups.length > 10) {
    console.error(`  (showing first 10 of ${result.inconsistentGroups.length})`);
  }
  if (result.lanesWithMissingSpineId.length > 0) {
    console.error(`  ${result.lanesWithMissingSpineId.length} non-bridge lanes missing spine_id`);
  }
  if (result.lanesWithMissingHash.length > 0) {
    console.error(`  ${result.lanesWithMissingHash.length} lanes missing base_spine_hash`);
  }
  for (const group of result.inconsistentPhysicalBundleGroups.slice(0, 10)) {
    console.error(`  physical bundle ${group.physical_bundle_id}: expected hash ${group.expected}, got ${group.got}`);
  }
  if (result.inconsistentPhysicalBundleGroups.length > 10) {
    console.error(
      `  (showing first 10 of ${result.inconsistentPhysicalBundleGroups.length} physical bundle inconsistencies)`,
    );
  }
  process.exit(1);
}

export function buildStageDSpinePrepStage({
  corridorFeatures,
  paths,
  parameters,
}: StageDSpinePrepStageInput) {
  prepareCorridorsForSpines(
    corridorFeatures,
    parameters.openDataMinFragmentLengthM,
    parameters.densifyMaxSegmentM,
    parameters.densifyStepM,
  );

  const spinesByCorridorId = new Map<string, CorridorSpine>();
  const spineCount = rebuildSpineArtifacts(corridorFeatures, spinesByCorridorId, paths.spinesGeoJson);
  console.log(`[visual-network] corridor groups:           ${corridorFeatures.length}`);
  console.log(`[visual-network] spines created:            ${spineCount}`);

  const allSpinesForGrouping = groupingSpines(corridorFeatures, spinesByCorridorId);
  const {
    groups: physicalBundles,
    rejects: physicalBundleRejects,
    transitiveDiagnostics = [],
  } = groupSpinesIntoPhysicalBundles(allSpinesForGrouping, {
    avgDistMaxM: 15,
    sharedFractionMin: 0.6,
    sharedLenMinM: 250,
    tangentMaxDeg: 30,
    resampleM: 25,
  });
  const spinesById = new Map(allSpinesForGrouping.map((spine) => [spine.spine_id, spine]));
  writeTransitiveDiagnostics(transitiveDiagnostics, spinesById, paths.transitiveBundlesGeoJson);
  const physicalBundleSpines = stampPhysicalBundleMetadata(
    corridorFeatures,
    physicalBundles,
    spinesByCorridorId,
    spinesById,
  );

  const physicalBundleMaterialization = materializePhysicalBundles(
    // SAFETY: Stage D corridors are LineString features with corridor_id; the materializer reads CorridorProperties from that bag.
    corridorFeatures as CorridorFeature[],
    physicalBundles,
    {
      spinesById,
      confidenceMin: parameters.physicalBundleSubstituteConfidenceMin,
      overlapDistMaxM: parameters.bundleOverlapDistMaxM,
      sharedLenMinM: parameters.bundleSharedLenMinM,
      splitSampleM: parameters.bundleSplitSampleM,
      fanoutBlendM: parameters.fanoutBlendM,
      laneWidthM: parameters.laneWidthM,
      taperM: 40,
      routeColorFor,
      compareRouteIds,
      orderColorsForBundle,
    },
  );
  if (physicalBundleMaterialization.consumed_corridor_count > 0) {
    corridorFeatures.length = 0;
    corridorFeatures.push(...physicalBundleMaterialization.features);
    rebuildSpineArtifacts(corridorFeatures, spinesByCorridorId, paths.spinesGeoJson);
  }

  writeMaterializationDebug(physicalBundleMaterialization, paths, parameters);
  writePhysicalBundleDebug(physicalBundleSpines, physicalBundleRejects, paths);

  const groupedCorridorCount = physicalBundles.reduce((acc, group) => acc + group.member_count, 0);
  console.log(`[visual-network] physical bundles:           ${physicalBundles.length}`);
  console.log(`[visual-network] grouped corridors:          ${groupedCorridorCount}`);
  console.log(`[visual-network] substituted bundles:        0`);
  console.log(`[visual-network] materialized bundles:       ${physicalBundleMaterialization.materialized_bundle_count}`);
  console.log(`[visual-network] materialized corridors used: ${physicalBundleMaterialization.consumed_corridor_count}`);
  console.log(`[visual-network] materialized fanouts:       ${physicalBundleMaterialization.fanout_count}`);
  console.log(`[visual-network] reject candidates:          ${physicalBundleRejects.length}`);
  console.log(`[visual-network] transitive bundle splits:   ${transitiveDiagnostics.length}`);

  const bundleArtifacts = buildBundleArtifacts(corridorFeatures, spinesByCorridorId);
  const result = assertSpineHashConsistency(bundleArtifacts);
  console.log(`[visual-network] bundle_lanes created:       ${result.bundleLaneCount}`);
  console.log(`[visual-network] lanes missing spine_id:     ${result.lanesWithMissingSpineId.length}`);
  console.log(`[visual-network] lanes missing hash:         ${result.lanesWithMissingHash.length}`);
  console.log(`[visual-network] inconsistent spine groups:  ${result.inconsistentGroups.length}`);
  console.log(`[visual-network] inconsistent pb groups:     ${result.inconsistentPhysicalBundleGroups.length}`);
  failStageDIfInconsistent(result);
  return { bundleArtifacts };
}
