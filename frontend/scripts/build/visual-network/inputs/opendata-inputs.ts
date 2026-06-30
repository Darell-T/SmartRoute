import type { LineFeature } from "../shared/types.ts";
import {
  bidirectionalHausdorff,
  geometryStats,
  resampleEdgeAt5m,
  routeSetsIntersect,
} from "../shared/geometry-utils.ts";

export type OpenDataInputsStageInput = {
  opendataLineFeatures: any[];
  geometrySourceName: string;
  overlapMinRatio: number;
  overlapSharedLenMinM: number;
  containmentAvgDistanceMaxM: number;
  tangentMaxDiffDeg: number;
};

export type OpenDataInputsStageResult = {
  pairsConsidered: number;
  pairsMatched: number;
  matchedPairs: any[];
  corridorFeatures: LineFeature[];
  corridorRows: any[];
  opendataOverlapWarnings: any[];
};

export function buildOpenDataInputsStage({
  opendataLineFeatures,
  geometrySourceName,
  overlapMinRatio,
  overlapSharedLenMinM,
  containmentAvgDistanceMaxM,
  tangentMaxDiffDeg,
}: OpenDataInputsStageInput): OpenDataInputsStageResult {
  const pairsConsidered = 0;
  const pairsMatched = 0;
  const matchedPairs: any[] = [];
  const corridorFeatures: LineFeature[] = [];
  const corridorRows: any[] = [];

  for (let index = 0; index < opendataLineFeatures.length; index += 1) {
    const feature = opendataLineFeatures[index];
    const stats = geometryStats(feature.geometry.coordinates);
    const corridorId = feature.properties.opendata_line_id;
    corridorFeatures.push({
      type: "Feature",
      geometry: feature.geometry,
      properties: {
        ...feature.properties,
        corridor_id: corridorId,
        branch_ids: [],
        member_edge_count: 0,
        base_member_edge_id: null,
        longest_member_edge_id: null,
        longest_member_length_m: stats.length_m,
        base_geometry_selection: "nyc_opendata_full_line",
        from_stop_id: null,
        to_stop_id: null,
        from_stop_name: null,
        to_stop_name: null,
        source_edge_ids: [],
        source_shape_ids: [],
        length_m: stats.length_m,
        direct_distance_m: stats.direct_distance_m,
        sinuosity: stats.sinuosity,
        max_segment_length_m: stats.max_segment_length_m,
        coordinate_count: stats.coordinate_count,
        sharp_angle_count: stats.sharp_angle_count,
      },
    });
    corridorRows.push({
      corridor_id: corridorId,
      route_ids: feature.properties.route_ids,
      member_edge_count: 1,
      longest_length_m: stats.length_m,
      is_shared: feature.properties.route_ids.length > 1,
      geometry_source: geometrySourceName,
    });
  }

  const opendataSamples = opendataLineFeatures.map((feature) =>
    resampleEdgeAt5m(feature.geometry.coordinates),
  );
  const opendataOverlapWarnings = [];
  for (let i = 0; i < opendataLineFeatures.length; i += 1) {
    for (let j = i + 1; j < opendataLineFeatures.length; j += 1) {
      const left = opendataLineFeatures[i];
      const right = opendataLineFeatures[j];
      const leftRoutes = left.properties.route_ids ?? [];
      const rightRoutes = right.properties.route_ids ?? [];
      if (routeSetsIntersect(leftRoutes, rightRoutes)) continue;
      const metrics = bidirectionalHausdorff(opendataSamples[i], opendataSamples[j]);
      const shorterLenM = Math.min(left.properties.length_m ?? 0, right.properties.length_m ?? 0);
      const sharedLenM = shorterLenM * metrics.overlap;
      if (
        metrics.overlap >= overlapMinRatio &&
        sharedLenM >= overlapSharedLenMinM &&
        Math.max(metrics.avgDistanceA, metrics.avgDistanceB) <= containmentAvgDistanceMaxM &&
        metrics.avgTangentDeg <= tangentMaxDiffDeg
      ) {
        opendataOverlapWarnings.push({
          type: "Feature",
          geometry: left.geometry,
          properties: {
            marker_type: "opendata_overlap_warning",
            reason: "overlap_without_shared_route_ids",
            left_corridor_id: left.properties.opendata_line_id,
            right_corridor_id: right.properties.opendata_line_id,
            left_route_ids: leftRoutes,
            right_route_ids: rightRoutes,
            hausdorff_m: Number(metrics.hausdorff.toFixed(2)),
            overlap: Number(metrics.overlap.toFixed(3)),
            overlap_a: Number(metrics.overlapA.toFixed(3)),
            overlap_b: Number(metrics.overlapB.toFixed(3)),
            shared_length_m: Number(sharedLenM.toFixed(2)),
            avg_distance_a_m: Number(metrics.avgDistanceA.toFixed(2)),
            avg_distance_b_m: Number(metrics.avgDistanceB.toFixed(2)),
            avg_tangent_deg: Number(metrics.avgTangentDeg.toFixed(2)),
          },
        });
      }
    }
  }

  return {
    pairsConsidered,
    pairsMatched,
    matchedPairs,
    corridorFeatures,
    corridorRows,
    opendataOverlapWarnings,
  };
}
