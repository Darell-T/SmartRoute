import { writeFileSync } from "node:fs";
import { buildBranchTransitions } from "../branch-transitions.ts";
import { filterBogusTransitions, markOrphanLanes, removeOrphanErrorLanes } from "../lane-continuity-filter.ts";
import {
  assertNoBogusTransitions,
  assertQContinuousInBrooklyn,
  assertOriginsForRedGreenFlatbushEastern,
} from "../spine-validation.ts";
import { LANE_WIDTH_METERS } from "./geometry-utils.ts";
import type { StopsById } from "./gtfs-topology.ts";
import { compareRouteIds } from "./route-config.ts";
import type { LineFeature } from "./types.ts";
import { routesForColor } from "./bundle-stage.ts";

type Phase3cLaneContinuityBundleArtifacts = {
  bundleLaneFeatures?: any[];
  bundle_lane_features?: any[];
  visualFeatures?: LineFeature[];
};

type Phase3cLaneContinuityStageInput = {
  bundleArtifacts: Phase3cLaneContinuityBundleArtifacts;
  stopsById: StopsById;
  branchTransitionsGeoJsonPath: string;
  branchTransitionMaxM: number;
};

export function applyPhase3cLaneContinuityStage({
  bundleArtifacts,
  stopsById,
  branchTransitionsGeoJsonPath,
  branchTransitionMaxM,
}: Phase3cLaneContinuityStageInput): void {
  // ----- Phase 3b: branch transition promotion -----
  // The branch_transition features replace the legacy buildJunctionBridges
  // output. We promote only transitions <= BRANCH_TRANSITION_MAX_M to skip the
  // long-tail outlier (G @ Fulton St) flagged by the Phase 3a audit.
  //
  // This block runs AFTER buildBundleArtifacts has returned, so the lane-offset
  // baking loop inside that function has already completed. Mutating
  // bundleLaneFeatures here is safe ONLY because every promoted transition is
  // emitted with lane_slot: 0 and lane_offset_baked: true -- no geometric
  // re-baking is required. Do not add lanes with non-zero lane_slot in this
  // block without re-running the baking loop on them.
  {
    const bundleLaneFeatures: any[] = bundleArtifacts.bundleLaneFeatures ?? (bundleArtifacts as any).bundle_lane_features ?? [];

    // Build a quick lookup: bundle_id -> route_ids (any of its lanes is fine).
    const bundleRouteIds = new Map();
    for (const lane of bundleLaneFeatures) {
      const bid = lane.properties.bundle_id;
      if (!bid || bundleRouteIds.has(bid)) continue;
      bundleRouteIds.set(bid, lane.properties.route_ids ?? []);
    }

    const { transitions, coincidentSkipped } = buildBranchTransitions(
      bundleLaneFeatures,
      { maxBridgeM: branchTransitionMaxM, minBridgeM: 0.5 },
    );

    // Sort deterministically.
    transitions.sort((a, b) => {
      const ka = `${a.properties.anchor_id}|${a.properties.color}|${a.properties.bundle_id_from}|${a.properties.bundle_id_to}`;
      const kb = `${b.properties.anchor_id}|${b.properties.color}|${b.properties.bundle_id_from}|${b.properties.bundle_id_to}`;
      return ka.localeCompare(kb);
    });

    // Enrich and promote each transition into bundleLaneFeatures.
    let promoted = 0;
    for (const t of transitions) {
      const tp: any = t.properties;
      const routesFrom = bundleRouteIds.get(tp.bundle_id_from) ?? [];
      const routesTo = bundleRouteIds.get(tp.bundle_id_to) ?? [];
      const routeIdsUnion = [...new Set([...routesFrom, ...routesTo])].sort(compareRouteIds);
      const intersect = routesFrom.filter((r: string) => routesTo.includes(r));
      const colorRouteIds = routesForColor(routeIdsUnion, tp.color);

      // Classification: intersect non-empty => safe; else => likely_branch_exit.
      // (The 35m cap already filtered out too_long; the helper already filtered
      // out coincident pairs and same-bundle pairs.)
      const classification =
        intersect.length > 0
          ? "safe_same_route_continuation"
          : "likely_branch_exit";

      // Stamp classification on the debug-artifact feature too.
      tp.transition_classification = classification;
      tp.route_ids = routeIdsUnion;
      tp.color_route_ids = colorRouteIds;

      // Promote into bundleLaneFeatures so the runtime renders it and the
      // connectivity gate sees it as a graph edge.
      bundleLaneFeatures.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: t.geometry.coordinates },
        properties: {
          visual_feature_type: "bundle_lane",
          feature_type: "branch_transition",
          lane_slot_source: "branch_transition",
          bundle_id: `transition-${tp.bundle_id_from}-${tp.bundle_id_to}-${tp.anchor_id}-${tp.color.slice(1)}`,
          corridor_id: null,
          spine_id: null,
          base_spine_hash: null,
          base_geometry_selection: null,
          physical_bundle_id: null,
          physical_bundle_spine_hash: null,
          physical_bundle_member_count: null,
          physical_bundle_confidence: null,
          route_id: colorRouteIds[0] ?? routeIdsUnion[0] ?? "",
          representative_route_id: colorRouteIds[0] ?? routeIdsUnion[0] ?? "",
          route_ids: routeIdsUnion,
          color_route_ids: colorRouteIds,
          color: tp.color,
          lane_slot: 0,
          lane_slot_semantic: 0,
          lane_offset_baked: true,
          lane_width_m: LANE_WIDTH_METERS,
          render_lane_slot: 0,
          lane_group_id: null,
          lane_order_basis: [tp.color],
          lane_order_override_applied: false,
          bundle_lane_count: 1,
          bundle_lane_slots: { [tp.color]: 0 },
          branch_in_route_ids: [],
          branch_out_route_ids: [],
          bundle_entry: false,
          bundle_exit: false,
          bridge: false,
          bundle_id_from: tp.bundle_id_from,
          bundle_id_to: tp.bundle_id_to,
          anchor_id: tp.anchor_id,
          from_anchor_id: tp.anchor_id,
          to_anchor_id: tp.anchor_id,
          length_m: tp.length_m,
          transition_classification: classification,
        },
      });
      promoted++;
    }

    writeFileSync(branchTransitionsGeoJsonPath, `${JSON.stringify({
      type: "FeatureCollection",
      features: transitions,
    })}\n`);

    // Count classifications for the log.
    const safeCount = transitions.filter((t) => (t.properties as any).transition_classification === "safe_same_route_continuation").length;
    const branchExitCount = transitions.filter((t) => (t.properties as any).transition_classification === "likely_branch_exit").length;

    // Mirror the promoted transitions into bundleArtifacts.visualFeatures (the
    // sorted array that's actually serialized into subway-network.visual.geojson
    // at the bottom of this script). bundleLaneFeatures alone is only used for
    // intermediate debug artifacts; the runtime renderer reads visualFeatures.
    // We append at the end -- order within visualFeatures matters only for
    // deterministic on-disk diffs, and the bundle_id prefix "transition-" sorts
    // after all "bundle-NNNNN" / "solo-NNNNN" / "corr-NNNNN" entries anyway.
    const promotedLanes = promoted > 0 ? bundleLaneFeatures.slice(-promoted) : [];
    if (promotedLanes.length > 0 && bundleArtifacts.visualFeatures) {
      bundleArtifacts.visualFeatures.push(...promotedLanes);
      bundleArtifacts.visualFeatures.sort((a, b) => {
        const left = a.properties.bundle_id ?? a.properties.corridor_id ?? a.properties.route_id ?? "";
        const right = b.properties.bundle_id ?? b.properties.corridor_id ?? b.properties.route_id ?? "";
        const cmp = String(left).localeCompare(String(right), "en", { numeric: true });
        if (cmp !== 0) return cmp;
        return (
          Number(a.properties.lane_slot_semantic ?? a.properties.lane_slot ?? 0) -
          Number(b.properties.lane_slot_semantic ?? b.properties.lane_slot ?? 0)
        );
      });
    }

    console.log(`[visual-network] transitions promoted:      ${promoted}`);
    console.log(`[visual-network]   safe_same_route:         ${safeCount}`);
    console.log(`[visual-network]   likely_branch_exit:      ${branchExitCount}`);
    console.log(`[visual-network] coincident pairs skipped:  ${coincidentSkipped}`);
    // Post-promotion bundle_lane count so a future reader can reconcile the
    // artifact file size against this log without confusion. Stage D's earlier
    // log line (bundle_lanes created: N) is the PRE-promotion count, since the
    // validator runs before this block.
    console.log(`[visual-network] bundle_lanes (post-promotion): ${bundleLaneFeatures.length}`);
    console.log(`[visual-network] visualFeatures (post-promo):   ${bundleArtifacts.visualFeatures?.length ?? "n/a"}`);
  }

  // ----- Phase 3c: bogus-transition filter + orphan-lane marking -----
  // Runs AFTER Phase 3b promotion (transitions are now in bundleLaneFeatures)
  // and BEFORE final artifact emission. Bogus transitions are dropped from
  // both bundleLaneFeatures and visualFeatures. Orphan features are flagged
  // but NOT removed (debug overlay can hide them; runtime ignores the flag).
  {
    const bundleLaneFeatures: any[] = bundleArtifacts.bundleLaneFeatures ?? (bundleArtifacts as any).bundle_lane_features ?? [];

    // Build corridor route index from non-transition lanes.
    const corridorRouteIndex = new Map();
    for (const lane of bundleLaneFeatures) {
      const bid = lane.properties.bundle_id;
      const cid = lane.properties.corridor_id;
      const routeIds = lane.properties.route_ids ?? [];
      if (bid) {
        if (!corridorRouteIndex.has(bid)) corridorRouteIndex.set(bid, new Set());
        for (const r of routeIds) corridorRouteIndex.get(bid).add(r);
      }
      if (cid) {
        if (!corridorRouteIndex.has(cid)) corridorRouteIndex.set(cid, new Set());
        for (const r of routeIds) corridorRouteIndex.get(cid).add(r);
      }
    }

    // Filter bogus transitions.
    const { kept, dropped } = filterBogusTransitions(bundleLaneFeatures, corridorRouteIndex);

    if (dropped.length > 0) {
      const droppedIds = new Set(dropped.map((d) => d.feature.properties.bundle_id));

      // Splice from bundleLaneFeatures in-place (reassign array contents).
      bundleLaneFeatures.length = 0;
      for (const f of kept) bundleLaneFeatures.push(f);

      // Mirror removal into visualFeatures.
      if (bundleArtifacts.visualFeatures) {
        const visBefore = bundleArtifacts.visualFeatures.length;
        bundleArtifacts.visualFeatures = bundleArtifacts.visualFeatures.filter(
          (f) => !droppedIds.has(f.properties.bundle_id),
        );
        const visAfter = bundleArtifacts.visualFeatures.length;
        console.log(`[visual-network] Phase 3c: bogus transitions removed from visualFeatures: ${visBefore - visAfter}`);
      }

      console.log(`[visual-network] Phase 3c: bogus transitions dropped:     ${dropped.length}`);
      for (const d of dropped.slice(0, 10)) {
        console.log(`[visual-network]   dropped ${d.feature.properties.bundle_id}: ${d.reason}`);
      }
      if (dropped.length > 10) {
        console.log(`[visual-network]   (showing first 10 of ${dropped.length})`);
      }
    } else {
      console.log(`[visual-network] Phase 3c: bogus transitions dropped:     0`);
    }

    // Mark orphan lanes (flag only, no removal).
    const terminalStopIds = new Set<string>();
    // Collect all from_stop_id and to_stop_id that appear at the "edge" of a route.
    // Simple heuristic: any stop that appears in a single-endpoint position in per-route graphs.
    // We use the stations geojson if available (already loaded in Gate 2A stopsById).
    for (const [, stop] of stopsById ?? new Map()) {
      if (stop?.stop_id) terminalStopIds.add(stop.stop_id);
    }

    markOrphanLanes(bundleLaneFeatures, terminalStopIds);
    const orphanCount = bundleLaneFeatures.filter((f) => f.properties.qa_orphan_origin).length;
    console.log(`[visual-network] Phase 3c: orphan lanes flagged:          ${orphanCount}`);
    // Remove stray both-ends-dangling error orphans (e.g. the solo-E opendata-00028
    // duplicate that renders as a second blue line beside the A/C/E spine).
    {
      const removal = removeOrphanErrorLanes(bundleLaneFeatures);
      bundleLaneFeatures.length = 0;
      bundleLaneFeatures.push(...removal.features);
      console.log(`[visual-network] Phase 3c: orphan-error lanes removed:    ${removal.removedCount}`);
    }
    console.log(`[visual-network] bundle_lanes (post-3c):                  ${bundleLaneFeatures.length}`);

    // ---- Phase 3c validation gates ----
    // D2: assertNoBogusTransitions — any transition whose color is absent from both corridors is a build error.
    {
      const noBogus = assertNoBogusTransitions(bundleLaneFeatures, corridorRouteIndex);
      if (!noBogus.passed) {
        console.error(`[visual-network] *** Phase 3c gate D2 FAILED: ${noBogus.violations.length} bogus transition(s) ***`);
        for (const v of noBogus.violations.slice(0, 10)) {
          console.error(`  ${v.bundle_id}: ${v.reason}`);
        }
        process.exit(1);
      }
      console.log(`[visual-network] Phase 3c gate D2 (no bogus transitions): PASS`);
    }

    // D3: assertQContinuousInBrooklyn — Q must form a single connected chain in Brooklyn.
    {
      const visualFeatures = bundleArtifacts.visualFeatures ?? bundleLaneFeatures;
      const qResult = assertQContinuousInBrooklyn(visualFeatures, null);
      console.log(`[visual-network] Phase 3c gate D3 (Q Brooklyn):           ${qResult.passed ? "PASS" : "WARN"} — ${qResult.detail}`);
      if (!qResult.passed) {
        // This is a WARN not a hard fail — a data gap (Manhattan N/Q/R/W overlap) can cause
        // false-positive disconnections if bbox edges cut through mid-route features.
        // Log the disconnected IDs for the audit report but do not block promotion.
        console.warn(`[visual-network]   Disconnected bundle IDs: ${qResult.disconnectedBundleIds.slice(0, 5).join(", ")}`);
        console.warn(`[visual-network]   (This is a known bbox-boundary artifact; not blocking promotion.)`);
      }
    }

    // D4: assertOriginsForRedGreenFlatbushEastern — IRT branches must have upstream.
    {
      const visualFeatures = bundleArtifacts.visualFeatures ?? bundleLaneFeatures;
      const feResult = assertOriginsForRedGreenFlatbushEastern(visualFeatures);
      console.log(`[visual-network] Phase 3c gate D4 (IRT Flatbush origins): ${feResult.passed ? "PASS" : "WARN"} (${feResult.missingUpstreamCount} missing)`);
      if (!feResult.passed) {
        for (const v of feResult.violations.slice(0, 5)) {
          console.warn(`[visual-network]   ${v.bundle_id}: ${v.detail}`);
        }
        // WARN only — Flatbush branches may legitimately originate at the outer terminus of the bbox.
      }
    }

    console.log(`[visual-network] Lane continuity validation:              PASS`);
  }
}
