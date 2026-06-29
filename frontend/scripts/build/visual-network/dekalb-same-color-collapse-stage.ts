import { collapseSameColorOverlaps } from "../collapse-same-color.ts";
import { parallelOffsetCrossColor } from "../parallel-offset-cross-color.ts";
import { smoothSharpCorners } from "../smooth-polyline.ts";
import { suppressShadowOrphans } from "../suppress-shadow-orphans.ts";
import { applyGeometrySmoothingPass } from "./geometry-smoothing-pass.ts";
import { geometryStats } from "./geometry-utils.ts";
import { applySameColorJunctionStage } from "./same-color-junction-stage.ts";
import { applySameRouteEndpointCrossingPass } from "./same-route-endpoint-crossing-pass.ts";
import { applyTightCurveSimplificationPass } from "./tight-curve-simplification-pass.ts";
import type { LineFeature, Position } from "./types.ts";

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
  // =====================================================================
  // DeKalb-zone redundant-lane collapse (match Transit/Apple: one orange + one yellow trunk)
  // =====================================================================
  //
  // DeKalb has multiple parallel BMT track alignments in the OpenData: the materialized B/N/Q/R/W
  // shared_spine PLUS the separate B/D, D, N/R, R/W corridors -- all real but stacked, where Transit
  // and Apple draw ONE orange (B/D) + ONE yellow (N/Q/R/W) trunk. We keep B/D (orange) and the
  // shared_spine YELLOW lane (N/Q/R/W) as the two trunks, and CLIP the redundant parallel same-color
  // corridors (shared_spine orange, D-solo, N/R, R/W) to OUTSIDE the zone -- their coverage elsewhere
  // is preserved, and the GTFS-topology connectivity gate (Gate 2D) is unaffected (it is edge-based,
  // not geometry-based). Scoped to the DeKalb bbox only; does NOT generalize to other junctions yet.
  const DEKALB_ZONE = { minLon: -73.985, maxLon: -73.975, minLat: 40.684, maxLat: 40.694 };
  const DEKALB_ZONE_CENTER = [-73.980, 40.689];
  const DEKALB_REDUNDANT_DIST_M = 22;   // a vertex this close to the kept same-color trunk is "redundant"
  const DEKALB_TRUNK_RADIUS_M = 1300;   // only treat kept-trunk geometry within this of the zone as the local trunk
  const DEKALB_SNAP_M = 50;             // connect a clipped cut-end (divergence point) to the trunk within this
  const DEKALB_MIN_CLIPPED_RUN_M = 250;
  const _dkHav = (a: Position, b: Position) => { const R = 6371000, r = Math.PI / 180, dy = (b[1] - a[1]) * r, dx = (b[0] - a[0]) * r; return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dx / 2) ** 2)); };
  const inDekalbZone = (p: Position) => p[0] >= DEKALB_ZONE.minLon && p[0] <= DEKALB_ZONE.maxLon && p[1] >= DEKALB_ZONE.minLat && p[1] <= DEKALB_ZONE.maxLat;
  function isDekalbRedundant(f: any) {
    // KEEP the materialized continuous-lane members (each route is its own continuous,
    // consistently-offset lane on the bundle alignment) as the DeKalb trunk; clip the other
    // parallel same-color SOLO/legacy corridors into it.
    const p = f.properties ?? {};
    const c = p.color;
    const rids = (p.route_ids ?? []).slice().sort().join(",");
    if (p.bundle_materialization_role === "continuous_lane") return false; // kept trunk lanes
    if (c === "#FF6319" && rids === "B,D") return true;                  // B/D corridor -> merge into trunk
    if (c === "#FF6319" && rids === "D" && p.lane_slot_source === "solo") return true; // D-solo
    if (c === "#FCCC0A" && (rids === "N,R" || rids === "R,W")) return true;            // N/R, R/W
    return false;
  }
  if (bundleArtifacts.visualFeatures) {
    const feats = bundleArtifacts.visualFeatures;
    // local kept same-color trunk vertices near DeKalb (the divergence reference)
    const keptNearByColor = new Map();
    for (const f of feats) {
      if (f.geometry?.type !== "LineString" || isDekalbRedundant(f)) continue;
      const near = f.geometry.coordinates.filter((p: Position) => _dkHav(p, DEKALB_ZONE_CENTER as Position) < DEKALB_TRUNK_RADIUS_M);
      if (near.length) { const c = f.properties.color; if (!keptNearByColor.has(c)) keptNearByColor.set(c, []); keptNearByColor.get(c).push(...near); }
    }
    const nearestKept = (p: Position, color: any) => { let bd = Infinity, bp = null; for (const q of (keptNearByColor.get(color) || [])) { const d = _dkHav(p, q); if (d < bd) { bd = d; bp = q; } } return { d: bd, p: bp }; };
    // A vertex is redundant where it runs within DEKALB_REDUNDANT_DIST_M of the kept same-color trunk
    // near DeKalb (i.e. they have merged). Distance-only -- NOT the raw bbox -- so the cut lands exactly
    // at the divergence point (and the snap below connects it), instead of dangling at the box edge.
    const vertexRedundant = (p: Position, color: any) => nearestKept(p, color).d < DEKALB_REDUNDANT_DIST_M;
    void inDekalbZone;
    const out: LineFeature[] = [];
    let clippedCount = 0, snapped = 0;
    for (const f of feats) {
      const color = f.properties?.color;
      if (!(f.geometry?.type === "LineString" && isDekalbRedundant(f) && f.geometry.coordinates.some((p: Position) => vertexRedundant(p, color)))) { out.push(f); continue; }
      // keep contiguous runs of vertices that have truly diverged from the kept trunk AND are outside the zone
      const runs = []; let cur = [];
      for (const p of f.geometry.coordinates) { if (vertexRedundant(p, color)) { if (cur.length >= 2) runs.push(cur); cur = []; } else cur.push(p); }
      if (cur.length >= 2) runs.push(cur);
      clippedCount += 1;
      let part = 0;
      for (const run of runs) {
        if (geometryStats(run).length_m < DEKALB_MIN_CLIPPED_RUN_M) continue;
        // snap each cut-end (the divergence point, near the trunk) onto the kept trunk so it merges (no
        // stub). Trim the short near-trunk wiggle first so the merge is a clean taper, not a lateral
        // notch (the clipped corridor carries its own baked lane offset, ~8m off the trunk lane).
        const nkStart = nearestKept(run[0], color);
        if (nkStart.p && nkStart.d > 1 && nkStart.d <= DEKALB_SNAP_M) {
          while (run.length > 3 && nearestKept(run[0], color).d < 30) run.shift();
          run.unshift(nkStart.p.slice()); snapped += 1;
        }
        const nkEnd = nearestKept(run[run.length - 1], color);
        if (nkEnd.p && nkEnd.d > 1 && nkEnd.d <= DEKALB_SNAP_M) {
          while (run.length > 3 && nearestKept(run[run.length - 1], color).d < 30) run.pop();
          run.push(nkEnd.p.slice()); snapped += 1;
        }
        // Aggressive local smoothing (lower angle threshold than the global pass) rounds the lateral
        // merge notch where the clipped corridor's baked offset meets the trunk lane.
        const mergedRun = smoothSharpCorners(run, { angleThresholdDeg: 16, iterations: 4, ratio: 0.25, maxFilletM: 28 });
        out.push({ ...f, properties: { ...f.properties, dekalb_clipped: true, dekalb_clip_part: part++ }, geometry: { type: "LineString" as const, coordinates: mergedRun } });
      }
    }
    bundleArtifacts.visualFeatures = out;
    console.log(`[visual-network] DeKalb-zone collapse:        redundant clipped=${clippedCount} cut-ends snapped=${snapped}`);
  }

  // ----- Same-color collapse: merge overlapping same-color lanes into one -----
  // Where multiple same-color features share a physical track (e.g. yellow N/W/R on
  // the Astoria/Broadway trunk at Queensboro, orange B/D + M on 6th Av), snap the
  // shorter onto the longer so they render as ONE line; portions that physically
  // diverge keep their own geometry (separate lines). Runs before smoothing so the
  // snap seams at divergence boundaries get rounded.
  if (bundleArtifacts.visualFeatures) {
    const collapse = collapseSameColorOverlaps(bundleArtifacts.visualFeatures, {
      collapseDistM: sameColorCollapseDistM,
      minOverlapM: 120,
    });
    bundleArtifacts.visualFeatures = collapse.features;
    console.log(`[visual-network] same-color collapse:           merged=${collapse.collapsedCount}`);
  }

  // ----- Cross-color parallelization (DISABLED): the proximity-based version shifted
  // genuine parallel pairs (e.g. Brighton B/Q at one-lane spacing) and re-introduced
  // crossings. The correct criterion is side-FLIP (crossing) detection, not proximity;
  // re-enable once parallelOffsetCrossColor is reworked to only fix runs where a
  // feature actually crosses (changes side of) a lower-rank different-color line.
  // if (bundleArtifacts.visualFeatures) {
  //   const par = parallelOffsetCrossColor(bundleArtifacts.visualFeatures, {
  //     colorOrder: BUNDLE_COLOR_ORDER, overlapDistM: 8, minOverlapM: 150, laneWidthM: LANE_WIDTH_METERS, taperM: 40,
  //   });
  //   bundleArtifacts.visualFeatures = par.features;
  //   console.log(`[visual-network] cross-color parallelize:        shifted=${par.shiftedCount}`);
  // }
  void parallelOffsetCrossColor;

  // ----- Suppress redundant cross-color shadow orphans (DISABLED): the geometric
  // "error-orphan that shadows a different color" criterion also removed legitimate
  // parallel pairs (B Brighton shadows Q; the 2 branch shadows the 3) -- B+Q and 2+5
  // legitimately share track. Distinguishing a redundant rush pattern from a legit
  // parallel route needs service-pattern data ("5 Peak") or a per-junction override,
  // not pure geometry. Left off until that is wired.
  void suppressShadowOrphans;

  // =====================================================================
  // Geometry smoothing: round sharp single-vertex elbows (Bug 3 / DeKalb)
  // =====================================================================
  //
  // Final geometry pass. The coarse OpenData polylines represent some real curves
  // (e.g. the Manhattan-Bridge -> 4th-Ave approach through the DeKalb interlocking)
  // as single-vertex 90-117deg elbows, and the Bug-2 cross-color offset amplifies
  // them. MapLibre's round line-join only rounds the stroke corner, not the
  // direction change, so they render as kinks. We round every sharp corner with
  // endpoint-pinned Chaikin corner-cutting; straight runs and gentle curves are
  // untouched. Endpoints stay byte-identical so feature-to-feature junctions
  // remain coincident (Gate 2D connectivity is GTFS-topology-based, not geometry-
  // based, so it is unaffected either way -- endpoint-pinning is the real guard).
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

  // ----- Tight-curve simplification (Apple/Transit look) -----
  // Some real revenue track hairpins through a tiny radius (e.g. the 5 at the
  // 149 St / Mott Haven curve, the red 148 St yard-lead curve). Drawn faithfully
  // at map scale those read as teardrop/hook scribbles; Apple and Transit App
  // round them into smooth gentle arcs. This pass relaxes only the tight runs
  // (a lot of total turning packed into a short arc) toward a gentler arc, leaving
  // straight runs and gentle curves byte-identical. Endpoints are pinned, so
  // junctions never move (Gate 2D connectivity is GTFS-topology-based).
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

  // ----- Same-route endpoint-crossing repair -----
  // When a same-route branch starts/ends a few meters past its sibling trunk, the
  // first/last segment can cross the trunk and render as an X. This pass is not a
  // connector: it only snaps that overshooting endpoint back to the actual
  // intersection, so the two features share a split node and the crossing segment
  // disappears. Interior crossings are left untouched for a fuller junction model.
  const { sameRouteEndpointRepairCount } = applySameRouteEndpointCrossingPass({
    bundleArtifacts,
    maxEndpointOvershootM: 180,
  });
  console.log(
    `[visual-network] same-route junction fabric: endpoint_repairs=${sameRouteEndpointRepairCount}`,
  );

  applySameColorJunctionStage({
    bundleArtifacts,
    sameColorSnapDistM,
    fanoutBlendM,
  });
}
