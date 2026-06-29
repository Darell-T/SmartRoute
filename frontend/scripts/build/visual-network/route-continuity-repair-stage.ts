import { readFileSync } from "node:fs";
import { bridgeRouteGaps } from "../bridge-route-gaps.ts";
import { simplifyTightCurves } from "../simplify-tight-curves.ts";
import { smoothSharpCorners } from "../smooth-polyline.ts";
import { snapOffRevenueToShape } from "../snap-off-revenue-to-shape.ts";
import type { RouteContinuityRepairStageInput } from "./route-continuity-repair-types.ts";

export function applyRouteContinuityRepairStage({
  bundleArtifacts,
  canonicalGeoJsonPath,
  bridgeMinGapM,
  bridgeMaxGapM,
  bridgeSubsetConnectorMaxGapM,
  offRevenueMaxM,
}: RouteContinuityRepairStageInput): void {
  // ----- Route gap bridging: close the small seams between same-route pieces -----
  // The split-and-reassemble pipeline (shared spine from BASE geometry, fanouts/
  // tails from MEMBER geometry, DeKalb clips) leaves small gaps (~11-20m) where a
  // member fans out from the shared spine -- the two pieces differ by up to the
  // overlap tolerance. Close those seams by extending the dangling source geometry
  // into its same-route sibling. For same-color broad branch splits like the
  // Queensboro N/W -> N/R seam, append an exact shared-route connector instead of
  // extending either broad feature and falsely carrying W/R over the seam.
  // In-place repairs stay bounded to <= BRIDGE_MAX_GAP_M; subset connectors are
  // endpoint-only and capped by BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M.
  // Connectivity (Gate 2D) is GTFS-topology-based, so bridges do not affect it.
  if (bundleArtifacts.visualFeatures) {
    const bridgeResult = bridgeRouteGaps(bundleArtifacts.visualFeatures, {
      minGapM: bridgeMinGapM,
      maxGapM: bridgeMaxGapM,
      allowSubsetRouteConnectors: true,
      subsetConnectorMaxGapM: bridgeSubsetConnectorMaxGapM,
    });
    bundleArtifacts.visualFeatures = bridgeResult.features;
    console.log(
      `[visual-network] route gap bridging:          integrated=${bridgeResult.bridgeCount} (gap ${bridgeMinGapM}-${bridgeMaxGapM}m, subset endpoint <=${bridgeSubsetConnectorMaxGapM}m)`,
    );
  }

  // ----- Off-revenue re-route: pull OpenData excursions onto the GTFS track -----
  // FINAL geometry pass (after snap + bridge, so it operates on the settled
  // endpoint geometry). Some NYC OpenData strokes swing far off the route's real
  // revenue track (e.g. the 5 at 149 St / Mott Haven bulges ~300m west toward
  // Walton Av). Each contiguous OFF-shape excursion (vertices > OFF_REVENUE_MAX_M
  // from every GTFS revenue shape of that feature's routes) is replaced with the
  // GTFS shape's own sub-path between where the line left and rejoined it -- so
  // lines follow the real curve, never a straight chord, with no wild jumps.
  if (bundleArtifacts.visualFeatures) {
    const canonicalDoc = JSON.parse(
      readFileSync(canonicalGeoJsonPath, "utf8"),
    );
    const shapesByRoute = new Map();
    for (const f of canonicalDoc.features) {
      if (f.geometry?.type !== "LineString") continue;
      const r = String(f.properties?.route_id);
      if (!shapesByRoute.has(r)) shapesByRoute.set(r, []);
      shapesByRoute.get(r).push(f.geometry.coordinates);
    }
    let reroutedFeatureCount = 0;
    for (const f of bundleArtifacts.visualFeatures) {
      if (f.geometry?.type !== "LineString") continue;
      const before = f.geometry.coordinates;
      if (!Array.isArray(before) || before.length < 3) continue;
      const routes = Array.isArray(f.properties?.route_ids) ? f.properties.route_ids : [];
      const shapes = routes.flatMap((r: any) => shapesByRoute.get(String(r)) ?? []);
      if (!shapes.length) continue;
      let coords = before;
      let moved = false;
      for (let pass = 0; pass < 4; pass += 1) {
        const next = snapOffRevenueToShape(coords, shapes, { maxOffM: offRevenueMaxM });
        if (next === coords) break;
        coords = next;
        moved = true;
      }
      if (!moved) continue;
      // Smooth the GTFS-derived path: round sharp single-vertex elbows and relax
      // any tight kink where the re-routed sub-path rejoins, so the result reads as
      // a clean curve rather than a literal/sharp GTFS trace. Endpoints are pinned.
      let smoothed = smoothSharpCorners(coords, {
        angleThresholdDeg: 12, // GTFS-derived path: round densely-sampled tight curls into clean arcs
        iterations: 5,
        ratio: 0.28,
        maxFilletM: 30,
      });
      smoothed = simplifyTightCurves(smoothed, {
        tightTurnDeg: 40,   // GTFS-derived: relax the real tight Mott-Haven-style curls harder
        windowM: 60,
        iterations: 40,
        lambda: 0.5,
      });
      f.geometry.coordinates = smoothed;
      f.properties.off_revenue_rerouted = true;
      reroutedFeatureCount += 1;
    }
    console.log(
      `[visual-network] off-revenue re-route:        features=${reroutedFeatureCount} (>${offRevenueMaxM}m off GTFS revenue shape)`,
    );
  }
}
