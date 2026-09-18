import { readFileSync } from "node:fs";
import { bridgeRouteGaps } from "../../bridge-route-gaps.ts";
import { haversineM } from "../../brighton-bq-church-spacing.ts";
import { simplifyTightCurves } from "../../simplify-tight-curves.ts";
import { smoothSharpCorners } from "../../smooth-polyline.ts";
import { snapOffRevenueToPolyline } from "../../snap-off-revenue-to-shape.ts";
import type { JsonValue } from "../../types.ts";
import { isJsonNumber, isJsonObject, isJsonString, parsedJson } from "../shared/route-config.ts";
import type { LineFeature, Position } from "../shared/types.ts";

type RouteContinuityRepairStageInput = {
  bundleArtifacts: { visualFeatures?: LineFeature[] };
  canonicalGeoJsonPath: string;
  bridgeMinGapM: number;
  bridgeMaxGapM: number;
  bridgeSubsetConnectorMaxGapM: number;
  offRevenueMaxM: number;
};

type GtfsRouteTrack = {
  routeId: string;
  coords: Position[];
};

function parseGtfsRouteTrack(value: JsonValue | undefined): GtfsRouteTrack | null {
  if (!isJsonObject(value)) return null;
  const geometry = value.geometry;
  if (!isJsonObject(geometry)) return null;
  if (geometry.type !== "LineString" || !Array.isArray(geometry.coordinates)) return null;
  if (geometry.coordinates.length < 2) return null;
  const properties = value.properties;
  if (!isJsonObject(properties)) return null;
  const routeId = properties.route_id;
  const routeIdIsScalar = [
    isJsonString(routeId),
    isJsonNumber(routeId),
    routeId === true,
    routeId === false,
  ].includes(true);
  if (!routeIdIsScalar) return null;
  const coords: Position[] = [];
  for (const item of geometry.coordinates) {
    if (!Array.isArray(item) || item.length < 2) return null;
    const lon = Number(item[0]);
    const lat = Number(item[1]);
    if ([Number.isFinite(lon), item[0] === lon, Number.isFinite(lat), item[1] === lat].includes(false)) {
      return null;
    }
    coords.push([lon, lat]);
  }
  return { routeId: String(routeId), coords };
}

function indexGtfsTracksByRoute(raw: JsonValue): Map<string, Position[][]> {
  const tracksByRoute = new Map<string, Position[][]>();
  if (!isJsonObject(raw) || !Array.isArray(raw.features)) return tracksByRoute;
  for (const feature of raw.features) {
    const parsed = parseGtfsRouteTrack(feature);
    if (!parsed) continue;
    const existing = tracksByRoute.get(parsed.routeId) ?? [];
    existing.push(parsed.coords);
    tracksByRoute.set(parsed.routeId, existing);
  }
  return tracksByRoute;
}

function gtfsTracksForFeature(
  feature: LineFeature,
  tracksByRoute: Map<string, Position[][]>,
): Position[][] {
  const before = feature.geometry.coordinates;
  const routes = Array.isArray(feature.properties?.route_ids) ? feature.properties.route_ids : [];
  const start: Position = before[0];
  const end: Position = before[before.length - 1];
  const tracks: Position[][] = [];
  for (const routeId of routes) {
    const candidates = tracksByRoute.get(String(routeId));
    if (!candidates?.length) continue;
    let best = candidates[0];
    let bestDistance = Infinity;
    for (const candidate of candidates) {
      if (candidate.length < 2) continue;
      const forward = haversineM(start, candidate[0]) + haversineM(end, candidate[candidate.length - 1]);
      const reverse = haversineM(start, candidate[candidate.length - 1]) + haversineM(end, candidate[0]);
      const distance = Math.min(forward, reverse);
      if (distance >= bestDistance) continue;
      bestDistance = distance;
      best = candidate;
    }
    tracks.push(best);
  }
  return tracks;
}

function rerouteFeatureOntoGtfs(
  feature: LineFeature,
  gtfsTracks: Position[][],
  offRevenueMaxM: number,
): boolean {
  let coords = feature.geometry.coordinates;
  let moved = false;
  for (let pass = 0; pass < 4; pass += 1) {
    const next = (snapOffRevenueToPolyline((coords), (gtfsTracks), { maxOffM: (offRevenueMaxM) }));
    if (next === coords) break;
    coords = next;
    moved = true;
  }
  if (!moved) return false;
  const filleted = smoothSharpCorners(coords, {
    angleThresholdDeg: 12,
    iterations: 5,
    ratio: 0.28,
    maxFilletM: 30,
  });
  feature.geometry.coordinates = simplifyTightCurves(filleted, {
    tightTurnDeg: 40,
    windowM: 60,
    iterations: 40,
    lambda: 0.5,
  });
  feature.properties.off_revenue_rerouted = true;
  return true;
}

function rerouteOffRevenueToGtfs(
  features: LineFeature[],
  canonicalGeoJsonPath: string,
  offRevenueMaxM: number,
): void {
  const canonicalDoc = parsedJson(readFileSync(canonicalGeoJsonPath, "utf8"));
  const tracksByRoute = indexGtfsTracksByRoute(canonicalDoc);
  let reroutedFeatureCount = 0;
  for (const feature of features) {
    if (feature.geometry?.type !== "LineString") continue;
    const before = feature.geometry.coordinates;
    if (!Array.isArray(before) || before.length < 3) continue;
    const gtfsTracks = gtfsTracksForFeature(feature, tracksByRoute);
    if (!gtfsTracks.length) continue;
    if (rerouteFeatureOntoGtfs(feature, gtfsTracks, offRevenueMaxM)) reroutedFeatureCount += 1;
  }
  console.log(
    `[visual-network] off-revenue re-route:        features=${reroutedFeatureCount} (>${offRevenueMaxM}m off GTFS revenue shape)`,
  );
}

export function applyRouteContinuityRepairStage({
  bundleArtifacts,
  canonicalGeoJsonPath,
  bridgeMinGapM,
  bridgeMaxGapM,
  bridgeSubsetConnectorMaxGapM,
  offRevenueMaxM,
}: RouteContinuityRepairStageInput): void {
  if (bundleArtifacts.visualFeatures) {
    const bridgeResult = bridgeRouteGaps(bundleArtifacts.visualFeatures, {
      minGapM: bridgeMinGapM,
      maxGapM: bridgeMaxGapM,
      allowSubsetRouteConnectors: true,
      subsetConnectorMaxGapM: bridgeSubsetConnectorMaxGapM,
    });
    console.log(
      `[visual-network] route gap bridging:          integrated=${bridgeResult.bridgeCount} (gap ${bridgeMinGapM}-${bridgeMaxGapM}m, subset endpoint <=${bridgeSubsetConnectorMaxGapM}m)`,
    );
    bundleArtifacts.visualFeatures = bridgeResult.features;
  }
  if (bundleArtifacts.visualFeatures) {
    rerouteOffRevenueToGtfs(
      bundleArtifacts.visualFeatures,
      canonicalGeoJsonPath,
      offRevenueMaxM,
    );
  }
}
