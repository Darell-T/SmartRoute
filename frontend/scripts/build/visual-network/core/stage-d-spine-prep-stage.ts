import { writeFileSync } from "node:fs";
import { buildSpineFromCorridor } from "../../spine.ts";
import { groupSpinesIntoPhysicalBundles, selectPhysicalBundleSpine, computePhysicalBundleSpineHash, clipPolylineToExtent } from "../../physical-bundle.ts";
import { orderColorsForBundle, BUNDLE_COLOR_ORDER } from "../../lane-order.ts";
import { dedupeDuplicateCorridors } from "../../dedupe-duplicate-corridors.ts";
import { materializePhysicalBundles } from "../../physical-bundle-materialization.ts";
import { densifyLongSegments } from "../../smooth-polyline.ts";
import { assertSpineHashConsistency } from "../../spine-validation.ts";
import { buildBundleArtifacts } from "./bundle-stage.ts";
import { geometryStats } from "../shared/geometry-utils.ts";
import { compareRouteIds, routeColorFor } from "../shared/route-config.ts";
import type { LineFeature } from "../shared/types.ts";
type StageDSpinePrepStageInput = {
  corridorFeatures: LineFeature[];
  paths: {
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
  parameters: {
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
};

export function buildStageDSpinePrepStage({
  corridorFeatures,
  paths,
  parameters,
}: StageDSpinePrepStageInput): { bundleArtifacts: ReturnType<typeof buildBundleArtifacts> } {
  let postSnapDegeneratePruned = 0;
  for (let index = corridorFeatures.length - 1; index >= 0; index -= 1) {
    const feature = corridorFeatures[index];
    if (feature.properties?.visual_feature_type === "same_color_branch_connector") {
      continue;
    }
    if (geometryStats(feature.geometry.coordinates).length_m >= parameters.openDataMinFragmentLengthM) {
      continue;
    }
    corridorFeatures.splice(index, 1);
    postSnapDegeneratePruned += 1;
  }
  console.log(
    `[visual-network] post-snap degenerate corridors pruned: ${postSnapDegeneratePruned}`,
  );

  // Drop duplicate same-route corridors (a shorter corridor running parallel within
  // ~25 m of, and contained by, a longer corridor that carries all its routes) before
  // spine/bundle assignment, so they never become a second parallel lane.
  {
    const dedup = dedupeDuplicateCorridors(corridorFeatures, { parallelDistM: 25, overlapRatioMin: 0.8 });
    if (dedup.removedIds.length) {
      corridorFeatures.length = 0;
      corridorFeatures.push(...(dedup.features as LineFeature[]));
      console.log(`[visual-network] duplicate corridors deduped:           ${dedup.removedIds.length}`);
    }
  }

  // Densify coarse source chords (some OpenData corridors have km-scale straight
  // segments that no offset/smoothing can round) BEFORE spine assignment + lane
  // offsetting, so downstream geometry has the intermediate vertices it needs.
  {
    let densified = 0;
    for (const f of corridorFeatures) {
      if (f.geometry?.type !== "LineString") continue;
      const before = f.geometry.coordinates;
      const after = densifyLongSegments(before, parameters.densifyMaxSegmentM, parameters.densifyStepM);
      if (after !== before) { f.geometry.coordinates = after; densified += 1; }
    }
    console.log(`[visual-network] coarse corridors densified:            ${densified}`);
  }

  // ----- Stage D: spine assignment -----
  // Each Gate 2C corridor maps 1:1 to a spine. The spine carries a deterministic
  // base_spine_hash that buildBundleArtifacts will stamp onto every bundle_lane
  // derived from this corridor. Hard validation downstream asserts that all
  // lanes sharing a spine_id share an identical hash.
  const spinesByCorridorId = new Map();
  const spineFeatures: any[] = [];

  function rebuildSpineArtifactsForCurrentCorridors() {
    spinesByCorridorId.clear();
    spineFeatures.length = 0;
    for (const f of corridorFeatures) {
      const spine = buildSpineFromCorridor(f);
      spinesByCorridorId.set(f.properties.corridor_id, spine);
      spineFeatures.push({
        type: "Feature",
        geometry: spine.geometry,
        properties: {
          visual_feature_type: "spine",
          spine_id: spine.spine_id,
          base_corridor_id: spine.base_corridor_id,
          base_spine_hash: spine.base_spine_hash,
          base_geometry_selection: spine.method,
          route_ids: spine.route_ids,
          source_edge_ids: spine.source_edge_ids,
          source_shape_ids: spine.source_shape_ids,
          length_m: spine.length_m,
        },
      });
    }
    spineFeatures.sort((a, b) => a.properties.spine_id.localeCompare(b.properties.spine_id));
    writeFileSync(paths.spinesGeoJson, `${JSON.stringify({ type: "FeatureCollection", features: spineFeatures })}\n`);
  }

  rebuildSpineArtifactsForCurrentCorridors();

  const CORRIDOR_GROUPS_COUNT = corridorFeatures.length;
  const SPINES_CREATED = spineFeatures.length;
  console.log(`[visual-network] corridor groups:           ${CORRIDOR_GROUPS_COUNT}`);
  console.log(`[visual-network] spines created:            ${SPINES_CREATED}`);

  // ----- Phase 1.5: cross-corridor physical bundle grouping -----
  // Detect when two or more Stage-D spines (from separate Gate 2C corridors)
  // physically share track for a meaningful stretch, substitute one shared
  // geometry, and stamp physical_bundle_* metadata onto member lanes.
  const SUBSTITUTE_CONFIDENCE_MIN = 0.75;
  const allSpinesForGrouping = [];
  for (const f of corridorFeatures) {
    const spine = spinesByCorridorId.get(f.properties.corridor_id);
    if (!spine) continue;
    allSpinesForGrouping.push({
      spine_id: spine.spine_id,
      geometry: spine.geometry,
      length_m: spine.length_m ?? 0,
      route_ids: spine.route_ids,
    });
  }

  const {
    groups: physicalBundles,
    rejects: physicalBundleRejects,
    transitiveDiagnostics: physicalBundleTransitiveDiagnostics = [],
  } =
    groupSpinesIntoPhysicalBundles(allSpinesForGrouping, {
      avgDistMaxM: 15,
      sharedFractionMin: 0.6,
      sharedLenMinM: 250,
      tangentMaxDeg: 30,
      resampleM: 25,
    });

  // Build maps
  const physicalBundleSpines: any[] = []; // FeatureCollection content
  const physicalBundleLaneFeatures: any[] = []; // debug per (bundle, member corridor)

  // spinesById for lookups inside the loop
  const spinesById = new Map();
  for (const s of allSpinesForGrouping) spinesById.set(s.spine_id, s);

  writeFileSync(
    paths.transitiveBundlesGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs physical bundle scoped-run diagnostics",
        summary: {
          transitive_disjoint_overlap_count: physicalBundleTransitiveDiagnostics.length,
        },
      },
      features: physicalBundleTransitiveDiagnostics.map((diagnostic) => {
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

  // Pre-build spine_id -> corridor feature map for O(1) lookup.
  // This avoids an O(n) .find() inside the bundle loop and makes unknown
  // spine_id references visible via a warn log.
  const spineIdToCorridorFeature = new Map();
  for (const f of corridorFeatures) {
    const spine = spinesByCorridorId.get(f.properties.corridor_id);
    if (spine) spineIdToCorridorFeature.set(spine.spine_id, f);
  }

  let bundlesSubstituted = 0;
  for (const group of physicalBundles) {
    const bundleSpine = selectPhysicalBundleSpine(group, spinesById);
    const bundleHash = computePhysicalBundleSpineHash(bundleSpine.geometry.coordinates);
    (group as any).physical_bundle_spine_hash = bundleHash;
    const shouldSubstitute = false;
    if (shouldSubstitute) bundlesSubstituted++;

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
        substituted: shouldSubstitute,
      },
    });

    // For each member corridor: substitute geometry (if confidence high enough) and stamp bundle metadata.
    for (const memberSpineId of bundleSpine.member_spine_ids) {
      const f = spineIdToCorridorFeature.get(memberSpineId);
      if (!f) {
        console.warn(`[visual-network] WARN: physical bundle ${group.physical_bundle_id} references unknown spine_id ${memberSpineId}`);
        continue;
      }

      if (shouldSubstitute) {
        // Clip the bundle spine to this corridor's actual extent.
        const memberCoords = f.geometry.coordinates;
        if (memberCoords.length >= 2) {
          const fromCoord = memberCoords[0];
          const toCoord = memberCoords[memberCoords.length - 1];
          const clipped = clipPolylineToExtent(bundleSpine.geometry.coordinates, fromCoord, toCoord, { resampleM: 25 });
          if (clipped && clipped.length >= 2) {
            // Substitute and emit a debug lane feature recording the substitution.
            physicalBundleLaneFeatures.push({
              type: "Feature",
              geometry: { type: "LineString", coordinates: clipped },
              properties: {
                visual_feature_type: "physical_bundle_lane",
                physical_bundle_id: group.physical_bundle_id,
                corridor_id: f.properties.corridor_id,
                spine_id: memberSpineId,
                base_spine_hash: spinesByCorridorId.get(f.properties.corridor_id).base_spine_hash,
                physical_bundle_spine_hash: bundleHash,
                substituted: true,
                route_ids: f.properties.route_ids,
              },
            });
            // Substituting the corridor's geometry with the bundle spine clip. The
            // corridor's `length_m` property is intentionally NOT recomputed -- it
            // remains the corridor's original logical length, not the substituted
            // geometry's arc length. Downstream uses `length_m` as a corridor
            // identity, not a render length.
            f.geometry = { type: "LineString", coordinates: clipped };
          }
        }
      }

      // Stamp metadata regardless of substitution decision.
      f.properties.physical_bundle_id = group.physical_bundle_id;
      f.properties.physical_bundle_spine_hash = bundleHash;
      f.properties.physical_bundle_member_count = group.member_count;
      f.properties.physical_bundle_confidence = group.confidence;
      f.properties.physical_bundle_substituted = shouldSubstitute;
    }
  }

  const physicalBundleMaterialization = materializePhysicalBundles(
    corridorFeatures as any,
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
      colorOrder: BUNDLE_COLOR_ORDER,
      routeColorFor,
      compareRouteIds,
      orderColorsForBundle,
    } as any,
  );

  if (physicalBundleMaterialization.consumed_corridor_count > 0) {
    corridorFeatures.length = 0;
    corridorFeatures.push(...physicalBundleMaterialization.features);
    rebuildSpineArtifactsForCurrentCorridors();
  }

  writeFileSync(
    paths.materializedBundlesGeoJson,
    `${JSON.stringify({
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
          materialized_bundle_count: physicalBundleMaterialization.materialized_bundle_count,
          consumed_corridor_count: physicalBundleMaterialization.consumed_corridor_count,
        },
      },
      features: physicalBundleMaterialization.debug.materializedBundleFeatures,
    })}\n`,
  );
  writeFileSync(
    paths.materializedBundleFanoutsGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs physical bundle materialization",
        summary: {
          fanout_count: physicalBundleMaterialization.fanout_count,
        },
      },
      features: physicalBundleMaterialization.debug.fanoutFeatures,
    })}\n`,
  );
  writeFileSync(
    paths.materializedBundleSplitsGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs physical bundle materialization",
      },
      features: physicalBundleMaterialization.debug.splitFeatures,
    })}\n`,
  );
  writeFileSync(
    paths.materializedBundleDefectsGeoJson,
    `${JSON.stringify({
      type: "FeatureCollection",
      metadata: {
        generated_at: new Date().toISOString(),
        source: "build-subway-visual-network.mjs physical bundle materialization",
      },
      features: physicalBundleMaterialization.debug.defectFeatures,
    })}\n`,
  );

  // Sort outputs deterministically.
  physicalBundleSpines.sort((a, b) => a.properties.physical_bundle_id.localeCompare(b.properties.physical_bundle_id));
  physicalBundleLaneFeatures.sort((a, b) =>
    a.properties.physical_bundle_id.localeCompare(b.properties.physical_bundle_id) ||
    a.properties.corridor_id.localeCompare(b.properties.corridor_id),
  );

  writeFileSync(paths.physicalBundlesGeoJson, `${JSON.stringify({ type: "FeatureCollection", features: physicalBundleSpines })}\n`);
  writeFileSync(paths.physicalBundleLanesGeoJson, `${JSON.stringify({ type: "FeatureCollection", features: physicalBundleLaneFeatures })}\n`);
  writeFileSync(paths.physicalBundleRejectsGeoJson, `${JSON.stringify({
    type: "FeatureCollection",
    features: physicalBundleRejects.map((r) => ({
      type: "Feature",
      geometry: null,
      properties: { ...r, visual_feature_type: "physical_bundle_reject" },
    })),
  })}\n`);

  const groupedCorridorCount = physicalBundles.reduce((acc, g) => acc + g.member_count, 0);
  console.log(`[visual-network] physical bundles:           ${physicalBundles.length}`);
  console.log(`[visual-network] grouped corridors:          ${groupedCorridorCount}`);
  console.log(`[visual-network] substituted bundles:        ${bundlesSubstituted}`);
  console.log(`[visual-network] materialized bundles:       ${physicalBundleMaterialization.materialized_bundle_count}`);
  console.log(`[visual-network] materialized corridors used: ${physicalBundleMaterialization.consumed_corridor_count}`);
  console.log(`[visual-network] materialized fanouts:       ${physicalBundleMaterialization.fanout_count}`);
  console.log(`[visual-network] reject candidates:          ${physicalBundleRejects.length}`);
  console.log(`[visual-network] transitive bundle splits:   ${physicalBundleTransitiveDiagnostics.length}`);

  const bundleArtifacts = buildBundleArtifacts(corridorFeatures, spinesByCorridorId);

  // ----- Stage D validation: same spine_id implies same base_spine_hash -----
  {
    const result = assertSpineHashConsistency(bundleArtifacts);
    console.log(`[visual-network] bundle_lanes created:       ${result.bundleLaneCount}`);
    console.log(`[visual-network] lanes missing spine_id:     ${result.lanesWithMissingSpineId.length}`);
    console.log(`[visual-network] lanes missing hash:         ${result.lanesWithMissingHash.length}`);
    console.log(`[visual-network] inconsistent spine groups:  ${result.inconsistentGroups.length}`);
    console.log(`[visual-network] inconsistent pb groups:     ${result.inconsistentPhysicalBundleGroups.length}`);
    if (
      result.inconsistentGroups.length > 0 ||
      result.lanesWithMissingSpineId.length > 0 ||
      result.lanesWithMissingHash.length > 0 ||
      result.inconsistentPhysicalBundleGroups.length > 0
    ) {
      console.error("[visual-network] *** Stage D validation FAILED -- refusing to promote. ***");
      for (const g of result.inconsistentGroups.slice(0, 10)) {
        console.error(`  spine ${g.spine_id}: expected hash ${g.expected}, got ${g.got}`);
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
      for (const g of result.inconsistentPhysicalBundleGroups.slice(0, 10)) {
        console.error(`  physical bundle ${g.physical_bundle_id}: expected hash ${g.expected}, got ${g.got}`);
      }
      if (result.inconsistentPhysicalBundleGroups.length > 10) {
        console.error(`  (showing first 10 of ${result.inconsistentPhysicalBundleGroups.length} physical bundle inconsistencies)`);
      }
      process.exit(1);
    }
    console.log(`[visual-network] Stage D validation:         PASS`);

    // ---- Phase 3c validation gates (run after spine-hash passes) ----
    // Gate D2: no bogus transitions (color absent from both corridors).
    // Note: Phase 3b transitions have not been promoted yet at this stage —
    // Stage D runs on the pre-promotion bundleLaneFeatures.
    // We run the transition gates AFTER Phase 3b promotes, so we defer to
    // a post-promotion check in the Phase 3c block below. This sentinel
    // just logs that the gates will run.
    console.log(`[visual-network] Phase 3c gates:             scheduled after Phase 3b promotion`);
  }

  return { bundleArtifacts };
}
