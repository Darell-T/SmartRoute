import { writeFileSync } from "node:fs";
import {
  detectCrossColorAdjacency,
  findSharedArcExtent,
  offsetPolylineOverExtent,
} from "../../cross-color-spread.ts";
import { BUNDLE_COLOR_ORDER } from "../../lane-order.ts";
import {
  LANE_WIDTH_METERS,
  offsetPolylineByLaneSlot,
} from "../shared/geometry-utils.ts";
import type { LineFeature } from "../shared/types.ts";

type LaneOffsetFinalizationBundleArtifacts = {
  visualFeatures: LineFeature[];
  bundleLaneFeatures?: any[];
  bundle_lane_features?: any[];
};

type LaneOffsetFinalizationStageInput = {
  bundleArtifacts: LaneOffsetFinalizationBundleArtifacts;
  crossColorSpreadGeoJsonPath: string;
  crossColorSegmentsGeoJsonPath: string;
  laneOrdersJsonPath: string;
};

export function applyLaneOffsetFinalizationStage({
  bundleArtifacts,
  crossColorSpreadGeoJsonPath,
  crossColorSegmentsGeoJsonPath,
  laneOrdersJsonPath,
}: LaneOffsetFinalizationStageInput): void {
  // ----- Cross-color parallel spread -----
  // Different-colored lines that share a physical corridor but render at
  // lane_slot 0 (solos / non-materialized members) overlap; the higher z-order
  // color paints over the lower. Detect those adjacency clusters and bake a
  // centered perpendicular offset per color so they stay visually parallel.
  // Skips features already offset by materialization (lane_slot_semantic != 0).
  {
    const targetLaneFeatures = bundleArtifacts.visualFeatures;
    const { groups } = detectCrossColorAdjacency(targetLaneFeatures, {
      sharedFractionMin: 0.6,
      sharedLenMinM: 250,
      avgDistMaxM: 18,
      tangentMaxDeg: 30,
      resampleM: 25,
    });

    let spreadFeaturesOffset = 0;
    const debugFeatures = [];

    for (const group of groups) {
      for (const member of group.members) {
        if (member.lane_slot === 0) continue; // centered middle lane stays put
        const f = member._featureRef;
        if (!f?.geometry?.coordinates) continue;
        const baked = offsetPolylineByLaneSlot(f.geometry.coordinates, member.lane_slot!);
        f.geometry = { type: "LineString", coordinates: baked };
        f.properties.cross_color_spread_slot = member.lane_slot;
        f.properties.lane_offset_baked = true;
        f.properties.lane_width_m = LANE_WIDTH_METERS;
        // This offset is constant across the WHOLE feature (unlike v2 below,
        // which only tapers a sub-extent), so it's safe to record it as the
        // feature's semantic slot -- the renderer's fill line-sort-key reads
        // lane_slot_semantic ahead of the color-rank fallback.
        f.properties.lane_slot_semantic = member.lane_slot;
        spreadFeaturesOffset += 1;
      }
      debugFeatures.push({
        type: "Feature",
        geometry: null,
        properties: {
          visual_feature_type: "cross_color_spread_group",
          member_count: group.members.length,
          members: group.members.map((m) => ({
            bundle_id: m.bundle_id,
            color: m.color,
            route_ids: m.route_ids,
            lane_slot: m.lane_slot,
          })),
        },
      });
    }

    writeFileSync(
      crossColorSpreadGeoJsonPath,
      `${JSON.stringify({ type: "FeatureCollection", features: debugFeatures })}\n`,
    );
    console.log(`[visual-network] cross-color spread groups:  ${groups.length}`);
    console.log(`[visual-network] cross-color features offset: ${spreadFeaturesOffset}`);
  }

  // ----- Cross-color parallel spread v2: segment-level for short shared stretches -----
  // v1 above offsets WHOLE features that share most of their length. But two long
  // lines that share only a short stretch (e.g. the A/C/E 31km line and the G
  // near Hoyt-Schermerhorn ~1km) can't be whole-offset without misplacing the
  // rest of the line. v2 finds the shared sub-extent and offsets ONLY that
  // stretch (tapered), keeping each feature as one continuous polyline. It only
  // touches features still at lane_slot 0 that v1 / materialization left alone.
  {
    const HALF_SLOT_M = 0.5 * LANE_WIDTH_METERS;
    const TAPER_M = 40;
    const DIST_MAX_M = 18;
    const MIN_SHARED_LEN_M = 250;
    const target = bundleArtifacts.visualFeatures;

    const candidates = target.filter((f) => {
      const sem = Number(f.properties?.lane_slot_semantic ?? f.properties?.lane_slot ?? 0);
      return (
        sem === 0 &&
        f.properties?.cross_color_spread_slot === undefined &&
        f.properties?.color &&
        f.geometry?.type === "LineString" &&
        Array.isArray(f.geometry.coordinates) &&
        f.geometry.coordinates.length >= 2
      );
    });

    // Cheap bbox prefilter (expand by ~DIST_MAX_M in degrees) so the O(N^2) pair
    // scan only runs findSharedArcExtent on geographically-plausible pairs.
    const latPad = DIST_MAX_M / 111320;
    const bboxes = candidates.map((f) => {
      let mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity;
      for (const [x, y] of f.geometry.coordinates) {
        if (x < mnx) mnx = x;
        if (y < mny) mny = y;
        if (x > mxx) mxx = x;
        if (y > mxy) mxy = y;
      }
      const lonPad = DIST_MAX_M / Math.max(1, Math.cos(((mny + mxy) / 2) * Math.PI / 180) * 111320);
      return [mnx - lonPad, mny - latPad, mxx + lonPad, mxy + latPad];
    });
    const bboxOverlap = (a: number[], b: number[]) => !(a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1]);

    const rankOf = (color: string) => {
      const i = BUNDLE_COLOR_ORDER.indexOf(color);
      return i === -1 ? 999 : i;
    };

    const pairs = [];
    for (let i = 0; i < candidates.length; i += 1) {
      for (let j = i + 1; j < candidates.length; j += 1) {
        const a = candidates[i];
        const b = candidates[j];
        if (a.properties.color === b.properties.color) continue;
        // Continuous-materialization members already have their lane offset baked in;
        // re-spreading them would double-offset.
        if (
          a.properties.lane_slot_source === "physical_bundle_continuous" ||
          b.properties.lane_slot_source === "physical_bundle_continuous"
        ) continue;
        if (!bboxOverlap(bboxes[i], bboxes[j])) continue;
        const ext = findSharedArcExtent(a.geometry.coordinates, b.geometry.coordinates, {
          resampleM: 25,
          distMaxM: DIST_MAX_M,
          minSharedLenM: MIN_SHARED_LEN_M,
        });
        if (!ext) continue;
        pairs.push({ a, b, ext });
      }
    }
    // Greedy: longest shared stretch first. A feature may be offset on MULTIPLE
    // disjoint sub-extents (e.g. A/C/E offsets vs B/D on Central Park West AND vs
    // G near Hoyt-Schermerhorn -- different stretches). We track claimed arc
    // ranges PER feature and only skip a pair if its sub-extent on either member
    // overlaps (within a taper margin) a range already claimed on that member,
    // which would compound offsets. Offsets on disjoint ranges compose cleanly
    // because each taper ramps back to the centerline between stretches.
    pairs.sort((p, q) => q.ext.sharedLenM - p.ext.sharedLenM);

    const claimedRanges = new Map(); // featureRef -> [[startArc, endArc], ...]
    const rangeBlocked = (feature: any, s: number, e: number) => {
      const ranges = claimedRanges.get(feature);
      if (!ranges) return false;
      // Block if [s,e] expanded by TAPER_M overlaps any existing claimed range.
      return ranges.some(([rs, re]: number[]) => !(e + TAPER_M < rs || s - TAPER_M > re));
    };
    const claimRange = (feature: any, s: number, e: number) => {
      if (!claimedRanges.has(feature)) claimedRanges.set(feature, []);
      claimedRanges.get(feature).push([s, e]);
    };

    let segPairs = 0;
    let segFeaturesOffset = 0;
    const segDebug = [];
    for (const { a, b, ext } of pairs) {
      if (rangeBlocked(a, ext.aStartArc, ext.aEndArc)) continue;
      if (rangeBlocked(b, ext.bStartArc, ext.bEndArc)) continue;
      const aNeg = rankOf(a.properties.color) <= rankOf(b.properties.color);
      const aOff = (aNeg ? -1 : 1) * HALF_SLOT_M;
      const bOff = (aNeg ? 1 : -1) * HALF_SLOT_M;

      a.geometry = {
        type: "LineString",
        coordinates: offsetPolylineOverExtent(a.geometry.coordinates, ext.aStartArc, ext.aEndArc, aOff, TAPER_M),
      };
      b.geometry = {
        type: "LineString",
        coordinates: offsetPolylineOverExtent(b.geometry.coordinates, ext.bStartArc, ext.bEndArc, bOff, TAPER_M),
      };
      a.properties.cross_color_segment_side = aNeg ? -0.5 : 0.5;
      b.properties.cross_color_segment_side = aNeg ? 0.5 : -0.5;
      a.properties.cross_color_segment_count = (a.properties.cross_color_segment_count ?? 0) + 1;
      b.properties.cross_color_segment_count = (b.properties.cross_color_segment_count ?? 0) + 1;
      a.properties.lane_offset_baked = true;
      b.properties.lane_offset_baked = true;
      claimRange(a, ext.aStartArc, ext.aEndArc);
      claimRange(b, ext.bStartArc, ext.bEndArc);
      segPairs += 1;
      segFeaturesOffset += 2;
      segDebug.push({
        type: "Feature",
        geometry: null,
        properties: {
          visual_feature_type: "cross_color_spread_segment_pair",
          a_bundle_id: a.properties.bundle_id,
          a_color: a.properties.color,
          a_routes: a.properties.route_ids ?? [],
          b_bundle_id: b.properties.bundle_id,
          b_color: b.properties.color,
          b_routes: b.properties.route_ids ?? [],
          shared_len_m: Number(ext.sharedLenM.toFixed(1)),
        },
      });
    }

    writeFileSync(
      crossColorSegmentsGeoJsonPath,
      `${JSON.stringify({ type: "FeatureCollection", features: segDebug })}\n`,
    );
    console.log(`[visual-network] cross-color segment pairs:   ${segPairs}`);
    console.log(`[visual-network] cross-color segment offsets: ${segFeaturesOffset}`);
  }

  // (Removed overnight-agent "Final chord guard" stage: it split features at >250m segments and
  // DROPPED the long part, which shattered legitimate long runs (Manhattan Bridge crossing, express
  // station gaps) into disconnected pieces -- the "literally broken" lines. Continuity restored.)

  // ----- Phase 2: lane order debug summary -----
  {
    const laneOrderSummary: Record<string, any> = {};
    const allLanes = bundleArtifacts.bundleLaneFeatures ?? (bundleArtifacts as any).bundle_lane_features ?? [];
    for (const lane of allLanes) {
      const bid = lane.properties.bundle_id;
      if (!bid || laneOrderSummary[bid]) continue;
      laneOrderSummary[bid] = {
        bundle_id: bid,
        corridor_id: lane.properties.corridor_id ?? null,
        from_anchor_id: lane.properties.from_anchor_id ?? null,
        to_anchor_id: lane.properties.to_anchor_id ?? null,
        lane_order_basis: lane.properties.lane_order_basis ?? null,
        lane_slot_source: lane.properties.lane_slot_source ?? null,
        route_ids: lane.properties.route_ids ?? [],
        bundle_lane_count: lane.properties.bundle_lane_count ?? 1,
        override_applied: lane.properties.lane_order_override_applied === true,
      };
    }
    const summaryArray = Object.values(laneOrderSummary).sort((a, b) =>
      a.bundle_id.localeCompare(b.bundle_id),
    );
    writeFileSync(laneOrdersJsonPath, `${JSON.stringify(summaryArray, null, 2)}\n`);
    const overridesCount = summaryArray.filter((s) => s.override_applied).length;
    console.log(`[visual-network] lane-order entries:        ${summaryArray.length}`);
    console.log(`[visual-network] lane-order overrides used: ${overridesCount}`);
  }
}
