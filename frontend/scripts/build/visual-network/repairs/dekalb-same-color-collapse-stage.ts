import { collapseSameColorOverlaps } from "../../collapse-same-color.ts";
import { smoothSharpCorners } from "../../smooth-polyline.ts";
import { applyGeometrySmoothingPass } from "./geometry-smoothing-pass.ts";
import { geometryStats } from "../shared/geometry-utils.ts";
import { applySameColorJunctionStage } from "./same-color-junction-stage.ts";
import { repairSameRouteEndpointCrossings } from "../../same-route-junction-fabric.ts";
import { applyTightCurveSimplificationPass } from "./tight-curve-simplification-pass.ts";
import type { LineFeature, Position } from "../shared/types.ts";
import { routeIdsOf } from "../shared/route-config.ts";

type DekalbSameColorCollapseStageInput = {
  bundleArtifacts: {
    visualFeatures?: LineFeature[];
  };
  sameColorCollapseDistM: number;
  smoothAngleThresholdDeg: number;
  smoothIterations: number;
  smoothRatio: number;
  smoothMaxFilletM: number;
  tightCurveTurnDeg: number;
  tightCurveWindowM: number;
  tightCurveIterations: number;
  tightCurveLambda: number;
  sameColorSnapDistM: number;
  fanoutBlendM: number;
};

type KeptVertexHit = {
  distanceM: number;
  point: Position | null;
};

type DekalbClipResult = {
  features: LineFeature[];
  clipped: boolean;
  snapped: number;
};

const DEKALB_ZONE_CENTER: Position = [-73.980, 40.689];
const DEKALB_REDUNDANT_DIST_M = 22;
const DEKALB_TRUNK_RADIUS_M = 1300;
const DEKALB_SNAP_M = 50;
const DEKALB_MIN_CLIPPED_RUN_M = 250;
const ORANGE = "#FF6319";
const YELLOW = "#FCCC0A";

function dekalbHaversineM(a: Position, b: Position): number {
  const earthM = 6371000;
  const radians = Math.PI / 180;
  const dLat = (b[1] - a[1]) * radians;
  const dLon = (b[0] - a[0]) * radians;
  return (
    2 *
    earthM *
    Math.asin(
      Math.sqrt(
        Math.sin(dLat / 2) ** 2 +
          Math.cos(a[1] * radians) * Math.cos(b[1] * radians) * Math.sin(dLon / 2) ** 2,
      ),
    )
  );
}

function clonePosition(position: Position): Position {
  return [position[0], position[1]];
}

function isDekalbRedundantLane(feature: LineFeature): boolean {
  const properties = feature.properties ?? {};
  if (properties.bundle_materialization_role === "continuous_lane") return false;
  const color = properties.color;
  const routeKey = routeIdsOf(properties).slice().sort().join(",");
  if (color === ORANGE && routeKey === "B,D") return true;
  if (color === ORANGE && routeKey === "D" && properties.lane_slot_source === "solo") return true;
  if (color === YELLOW && (routeKey === "N,R" || routeKey === "R,W")) return true;
  return false;
}

function keptTrunkVerticesByColor(features: LineFeature[]): Map<string, Position[]> {
  const kept = new Map<string, Position[]>();
  for (const feature of features) {
    if (feature.geometry?.type !== "LineString" || isDekalbRedundantLane(feature)) continue;
    const near = feature.geometry.coordinates.filter(
      (point) => dekalbHaversineM(point, DEKALB_ZONE_CENTER) < DEKALB_TRUNK_RADIUS_M,
    );
    if (near.length === 0) continue;
    const color = String(feature.properties.color ?? "");
    const bucket = kept.get(color);
    if (bucket) bucket.push(...near);
    else kept.set(color, [...near]);
  }
  return kept;
}

function nearestKeptVertex(
  point: Position,
  color: string,
  kept: Map<string, Position[]>,
): KeptVertexHit {
  let bestDistance = Infinity;
  let bestPoint: Position | null = null;
  for (const candidate of kept.get(color) ?? []) {
    const distanceM = dekalbHaversineM(point, candidate);
    if (distanceM < bestDistance) {
      bestDistance = distanceM;
      bestPoint = candidate;
    }
  }
  return { distanceM: bestDistance, point: bestPoint };
}

function vertexIsRedundant(point: Position, color: string, kept: Map<string, Position[]>): boolean {
  return nearestKeptVertex(point, color, kept).distanceM < DEKALB_REDUNDANT_DIST_M;
}

function splitNonRedundantRuns(
  coords: Position[],
  color: string,
  kept: Map<string, Position[]>,
): Position[][] {
  const runs: Position[][] = [];
  let current: Position[] = [];
  for (const point of coords) {
    if (vertexIsRedundant(point, color, kept)) {
      if (current.length >= 2) runs.push(current);
      current = [];
    } else {
      current.push(point);
    }
  }
  if (current.length >= 2) runs.push(current);
  return runs;
}

function snapRunToTrunk(
  run: Position[],
  side: "start" | "end",
  color: string,
  kept: Map<string, Position[]>,
): number {
  const endIndex = side === "start" ? 0 : run.length - 1;
  const nearest = nearestKeptVertex(run[endIndex], color, kept);
  if (!nearest.point || nearest.distanceM <= 1 || nearest.distanceM > DEKALB_SNAP_M) return 0;
  while (run.length > 3) {
    const index = side === "start" ? 0 : run.length - 1;
    if (nearestKeptVertex(run[index], color, kept).distanceM >= 30) break;
    if (side === "start") run.shift();
    else run.pop();
  }
  if (side === "start") run.unshift(clonePosition(nearest.point));
  else run.push(clonePosition(nearest.point));
  return 1;
}

function clipRedundantDekalbFeature(
  feature: LineFeature,
  kept: Map<string, Position[]>,
): DekalbClipResult {
  const color = String(feature.properties?.color ?? "");
  if (
    feature.geometry?.type !== "LineString" ||
    !isDekalbRedundantLane(feature) ||
    !feature.geometry.coordinates.some((point) => vertexIsRedundant(point, color, kept))
  ) {
    return { features: [feature], clipped: false, snapped: 0 };
  }

  const parts: LineFeature[] = [];
  let part = 0;
  let snapped = 0;
  for (const run of splitNonRedundantRuns(feature.geometry.coordinates, color, kept)) {
    if (geometryStats(run).length_m < DEKALB_MIN_CLIPPED_RUN_M) continue;
    snapped += snapRunToTrunk(run, "start", color, kept);
    snapped += snapRunToTrunk(run, "end", color, kept);
    const nearDekalb = run.some(
      (point) => dekalbHaversineM(point, DEKALB_ZONE_CENTER) < DEKALB_TRUNK_RADIUS_M,
    );
    parts.push({
      ...feature,
      properties: { ...feature.properties, dekalb_clipped: true, dekalb_clip_part: part },
      geometry: {
        type: "LineString",
        coordinates: nearDekalb
          ? smoothSharpCorners(run, { angleThresholdDeg: 16, iterations: 4, ratio: 0.25, maxFilletM: 28 })
          : run,
      },
    });
    part += 1;
  }
  return { features: parts, clipped: true, snapped };
}

export function applyDekalbSameColorCollapseStage({
  bundleArtifacts,
  sameColorCollapseDistM,
  smoothAngleThresholdDeg,
  smoothIterations,
  smoothRatio,
  smoothMaxFilletM,
  tightCurveTurnDeg,
  tightCurveWindowM,
  tightCurveIterations,
  tightCurveLambda,
  sameColorSnapDistM,
  fanoutBlendM,
}: DekalbSameColorCollapseStageInput): void {
  if (bundleArtifacts.visualFeatures) {
    const kept = keptTrunkVerticesByColor(bundleArtifacts.visualFeatures);
    const clippedFeatures: LineFeature[] = [];
    let clippedCount = 0;
    let snapped = 0;
    for (const feature of bundleArtifacts.visualFeatures) {
      const clipped = clipRedundantDekalbFeature(feature, kept);
      if (clipped.clipped) clippedCount += 1;
      snapped += clipped.snapped;
      clippedFeatures.push(...clipped.features);
    }
    bundleArtifacts.visualFeatures = clippedFeatures;
    console.log(
      `[visual-network] DeKalb-zone collapse:        redundant clipped=${clippedCount} cut-ends snapped=${snapped}`,
    );
  }

  if (bundleArtifacts.visualFeatures) {
    const collapse = collapseSameColorOverlaps(bundleArtifacts.visualFeatures, {
      collapseDistM: sameColorCollapseDistM,
      minOverlapM: 120,
    });
    bundleArtifacts.visualFeatures = collapse.features;
    console.log(`[visual-network] same-color collapse:           merged=${collapse.collapsedCount}`);
  }

  // Cross-color parallelize and shadow-orphan suppression stay disabled: those
  // proximity heuristics also remove legitimate parallel pairs (Brighton B/Q, 2/5).

  const { smoothedFeatureCount, smoothedCornerCount } = applyGeometrySmoothingPass({
    features: bundleArtifacts.visualFeatures,
    angleThresholdDeg: smoothAngleThresholdDeg,
    iterations: smoothIterations,
    ratio: smoothRatio,
    maxFilletM: smoothMaxFilletM,
  });
  console.log(
    `[visual-network] geometry smoothing:          features=${smoothedFeatureCount} sharp_corners=${smoothedCornerCount}`,
  );

  const { tightCurveFeatureCount } = applyTightCurveSimplificationPass({
    features: bundleArtifacts.visualFeatures,
    tightTurnDeg: tightCurveTurnDeg,
    windowM: tightCurveWindowM,
    iterations: tightCurveIterations,
    lambda: tightCurveLambda,
  });
  console.log(
    `[visual-network] tight-curve simplification:   features=${tightCurveFeatureCount} (turn>=${tightCurveTurnDeg}deg/${tightCurveWindowM}m)`,
  );

  let sameRouteEndpointRepairCount = 0;
  if (bundleArtifacts.visualFeatures) {
    const repair = repairSameRouteEndpointCrossings(bundleArtifacts.visualFeatures, {
      maxEndpointOvershootM: 180,
    });
    bundleArtifacts.visualFeatures = repair.features;
    sameRouteEndpointRepairCount = repair.repairCount;
  }
  console.log(
    `[visual-network] same-route junction fabric: endpoint_repairs=${sameRouteEndpointRepairCount}`,
  );

  applySameColorJunctionStage({
    bundleArtifacts,
    sameColorSnapDistM,
    fanoutBlendM,
  });
}
