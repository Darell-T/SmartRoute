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
import { propertyNumber, propertyString, routeIdsOf } from "../shared/route-config.ts";
import type { BundleArtifacts, LineFeature, Position } from "../shared/types.ts";

type LaneOffsetFinalizationStageInput = {
  bundleArtifacts: BundleArtifacts;
  crossColorSpreadGeoJsonPath: string;
  crossColorSegmentsGeoJsonPath: string;
  laneOrdersJsonPath: string;
};

type SharedExtent = {
  aStartArc: number;
  aEndArc: number;
  bStartArc: number;
  bEndArc: number;
  sharedLenM: number;
};

type SegmentPair = {
  a: LineFeature;
  b: LineFeature;
  ext: SharedExtent;
};

type LaneOrderEntry = {
  bundle_id: string;
  corridor_id: unknown;
  from_anchor_id: unknown;
  to_anchor_id: unknown;
  lane_order_basis: unknown;
  lane_slot_source: unknown;
  route_ids: string[];
  bundle_lane_count: unknown;
  override_applied: boolean;
};

const HALF_SLOT_M = 0.5 * LANE_WIDTH_METERS;
const TAPER_M = 40;
const DIST_MAX_M = 18;
const MIN_SHARED_LEN_M = 250;

function colorRank(color: string) {
  const index = BUNDLE_COLOR_ORDER.indexOf(color);
  return index === -1 ? 999 : index;
}

function applyWholeFeatureSpread(
  visualFeatures: LineFeature[],
  crossColorSpreadGeoJsonPath: string,
) {
  const { groups } = detectCrossColorAdjacency(visualFeatures, {
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
      const slot = member.lane_slot;
      if (!slot) continue;
      const feature = member._featureRef;
      if (!feature?.geometry?.coordinates) continue;
      feature.geometry = {
        type: "LineString",
        coordinates: offsetPolylineByLaneSlot(feature.geometry.coordinates, slot),
      };
      feature.properties.cross_color_spread_slot = slot;
      feature.properties.lane_offset_baked = true;
      feature.properties.lane_width_m = LANE_WIDTH_METERS;
      feature.properties.lane_slot_semantic = slot;
      spreadFeaturesOffset += 1;
    }
    debugFeatures.push({
      type: "Feature",
      geometry: null,
      properties: {
        visual_feature_type: "cross_color_spread_group",
        member_count: group.members.length,
        members: group.members.map((member) => ({
          bundle_id: member.bundle_id,
          color: member.color,
          route_ids: member.route_ids,
          lane_slot: member.lane_slot,
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

function isSegmentSpreadCandidate(feature: LineFeature) {
  const hasLineGeometry =
    feature.geometry?.type === "LineString" &&
    Array.isArray(feature.geometry.coordinates) &&
    feature.geometry.coordinates.length >= 2;
  if (!hasLineGeometry && feature.properties?.color) return false;
  if (feature.properties.cross_color_spread_slot !== undefined) return false;
  return Number(feature.properties.lane_slot_semantic ?? feature.properties.lane_slot ?? 0) === 0;
}

function paddedBBox(coords: Position[]) {
  const latPad = DIST_MAX_M / 111320;
  let minLon = Infinity;
  let minLat = Infinity;
  let maxLon = -Infinity;
  let maxLat = -Infinity;
  for (const [lon, lat] of coords) {
    if (lon < minLon) minLon = lon;
    if (lat < minLat) minLat = lat;
    if (lon > maxLon) maxLon = lon;
    if (lat > maxLat) maxLat = lat;
  }
  const lonPad = DIST_MAX_M / Math.max(1, Math.cos(((minLat + maxLat) / 2) * Math.PI / 180) * 111320);
  return [minLon - lonPad, minLat - latPad, maxLon + lonPad, maxLat + latPad];
}

function createClaimedRanges() {
  const claimedRanges = new Map<LineFeature, [number, number][]>();
  return {
    blocked(feature: LineFeature, s: number, e: number) {
      const ranges = claimedRanges.get(feature);
      if (!ranges) return false;
      return ranges.some(([rs, re]) => !(e + TAPER_M < rs || s - TAPER_M > re));
    },
    claim(feature: LineFeature, s: number, e: number) {
      const ranges = claimedRanges.get(feature);
      if (ranges) {
        ranges.push([s, e]);
        return;
      }
      claimedRanges.set(feature, [[s, e]]);
    },
  };
}

function classifySegmentPairs(candidates: LineFeature[]): SegmentPair[] {
  const bboxes = candidates.map((feature) => paddedBBox(feature.geometry.coordinates));
  const pairs: SegmentPair[] = [];
  for (let i = 0; i < candidates.length; i += 1) {
    for (let j = i + 1; j < candidates.length; j += 1) {
      const a = candidates[i];
      const b = candidates[j];
      if (a.properties.color === b.properties.color) continue;
      if (
        a.properties.lane_slot_source === "physical_bundle_continuous" ||
        b.properties.lane_slot_source === "physical_bundle_continuous"
      ) {
        continue;
      }
      if (
        bboxes[i][2] < bboxes[j][0] ||
        bboxes[j][2] < bboxes[i][0] ||
        bboxes[i][3] < bboxes[j][1] ||
        bboxes[j][3] < bboxes[i][1]
      ) {
        continue;
      }
      const ext = findSharedArcExtent(a.geometry.coordinates, b.geometry.coordinates, {
        resampleM: 25,
        distMaxM: DIST_MAX_M,
        minSharedLenM: MIN_SHARED_LEN_M,
      });
      if (!ext) continue;
      pairs.push({ a, b, ext });
    }
  }
  pairs.sort((p, q) => q.ext.sharedLenM - p.ext.sharedLenM);
  return pairs;
}

function applySegmentSpread(
  visualFeatures: LineFeature[],
  crossColorSegmentsGeoJsonPath: string,
) {
  const candidates = visualFeatures.filter(isSegmentSpreadCandidate);
  const pairs = classifySegmentPairs(candidates);
  const claimed = createClaimedRanges();
  let segPairs = 0;
  let segFeaturesOffset = 0;
  const segDebug = [];
  for (const { a, b, ext } of pairs) {
    if (claimed.blocked(a, ext.aStartArc, ext.aEndArc) || claimed.blocked(b, ext.bStartArc, ext.bEndArc)) {
      continue;
    }
    const aNeg = colorRank(String(a.properties.color)) <= colorRank(String(b.properties.color));
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
    a.properties.cross_color_segment_count = (propertyNumber(a.properties.cross_color_segment_count) ?? 0) + 1;
    b.properties.cross_color_segment_count = (propertyNumber(b.properties.cross_color_segment_count) ?? 0) + 1;
    a.properties.lane_offset_baked = true;
    b.properties.lane_offset_baked = true;
    claimed.claim(a, ext.aStartArc, ext.aEndArc);
    claimed.claim(b, ext.bStartArc, ext.bEndArc);
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

function writeLaneOrderSummary(bundleLaneFeatures: LineFeature[], laneOrdersJsonPath: string) {
  const laneOrderSummary = new Map<string, LaneOrderEntry>();
  for (const lane of bundleLaneFeatures) {
    const bundleId = propertyString(lane.properties.bundle_id);
    if (!bundleId || laneOrderSummary.has(bundleId)) continue;
    laneOrderSummary.set(bundleId, {
      bundle_id: bundleId,
      corridor_id: lane.properties.corridor_id ?? null,
      from_anchor_id: lane.properties.from_anchor_id ?? null,
      to_anchor_id: lane.properties.to_anchor_id ?? null,
      lane_order_basis: lane.properties.lane_order_basis ?? null,
      lane_slot_source: lane.properties.lane_slot_source ?? null,
      route_ids: routeIdsOf(lane.properties),
      bundle_lane_count: propertyNumber(lane.properties.bundle_lane_count) ?? 1,
      override_applied: lane.properties.lane_order_override_applied === true,
    });
  }
  const summaryArray = [...laneOrderSummary.values()].sort((a, b) =>
    a.bundle_id.localeCompare(b.bundle_id),
  );
  writeFileSync(laneOrdersJsonPath, `${JSON.stringify(summaryArray, null, 2)}\n`);
  const overridesCount = summaryArray.filter((entry) => entry.override_applied).length;
  console.log(`[visual-network] lane-order entries:        ${summaryArray.length}`);
  console.log(`[visual-network] lane-order overrides used: ${overridesCount}`);
}

export function applyLaneOffsetFinalizationStage({
  bundleArtifacts,
  crossColorSpreadGeoJsonPath,
  crossColorSegmentsGeoJsonPath,
  laneOrdersJsonPath,
}: LaneOffsetFinalizationStageInput): void {
  // Overnight chord-guard was removed: splitting at >250m segments dropped
  // legitimate long runs (Manhattan Bridge, express station gaps).
  applyWholeFeatureSpread(bundleArtifacts.visualFeatures, crossColorSpreadGeoJsonPath);
  applySegmentSpread(bundleArtifacts.visualFeatures, crossColorSegmentsGeoJsonPath);
  writeLaneOrderSummary(bundleArtifacts.bundleLaneFeatures, laneOrdersJsonPath);
}
