import { MTA_ROUTE_COLORS, darkenHexColor } from "../mta-colors.ts";
import type {
  Position,
  PointGeometry,
  LineStringGeometry,
  Feature,
  FeatureCollection,
} from "../types.ts";
import type {
  MeterPoint,
  ProjectionBase,
  VisualFeature,
  Projection,
  ProjectionCluster,
  StationFeature,
  StationBuildResult,
} from "./types.ts";
import {
  rawStationDebugFeature,
  snapDebugFeature,
  rejectedDebugFeature,
  ambiguousDebugFeature,
} from "./debug-features.ts";

const MAX_SNAP_DISTANCE_M = 90;
// Fallback tier for stations whose schematic lane drifted past the strict
// gate (terminals like Wakefield-241 St sit 92-114m out). Only used when the
// strict pass finds NOTHING, so ambiguity protection is unaffected.
const RELAXED_SNAP_DISTANCE_M = 140;
const MEDIUM_SNAP_DISTANCE_M = 35;
const LOW_SNAP_DISTANCE_M = 65;
const CLUSTER_LINK_M = 35;
const CLUSTER_MERGE_M = 55;
const SHARED_BAR_BASE_LENGTH_M = 12;
const SHARED_BAR_PER_ROUTE_M = 2;
const SHARED_BAR_MAX_LENGTH_M = 24;
// End-cap padding so the interchange capsule extends slightly past the
// outermost served lane rather than ending exactly on it.
const SHARED_BAR_END_PADDING_M = 5;
const BADGE_SPACING_X_PX = 18;
const BADGE_SPACING_Y_PX = 18;
const BADGE_BASE_OFFSET_Y_PX = 28;
const BADGE_MAX_COLUMNS = 5;

const MTA_ROUTE_ORDER = [
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "6X",
  "7",
  "7X",
  "A",
  "C",
  "E",
  "B",
  "D",
  "F",
  "FX",
  "M",
  "G",
  "J",
  "Z",
  "L",
  "N",
  "Q",
  "R",
  "W",
  "S",
  "FS",
  "GS",
  "H",
  "SI",
];

// Single source of truth lives in lib/mta-colors.json.
const ROUTE_COLORS = new Map(Object.entries(MTA_ROUTE_COLORS));

function routeSortValue(routeId: string) {
  const index = MTA_ROUTE_ORDER.indexOf(routeId);
  return index === -1 ? MTA_ROUTE_ORDER.length + routeId.charCodeAt(0) : index;
}

function sortRoutes(routeIds: unknown[]) {
  return [...new Set(routeIds.filter(Boolean).map(String))].sort((a, b) => {
    const rank = routeSortValue(a) - routeSortValue(b);
    return rank || a.localeCompare(b, "en", { numeric: true });
  });
}

function routeColor(routeId: string) {
  return ROUTE_COLORS.get(routeId) ?? "#808183";
}

export function subwayBulletName(routeId: string) {
  const normalized = String(routeId ?? "").trim().toUpperCase();
  if (normalized === "6X") return "6d";
  if (normalized === "7X") return "7d";
  if (normalized === "FX") return "fd";
  if (normalized === "FS") return "sf";
  if (normalized === "SI" || normalized === "SIR") return "sir";
  return normalized.toLowerCase();
}

function featureCollection(features: Feature[] = []): FeatureCollection {
  return { type: "FeatureCollection", features };
}

function metersPerDegreeLng(lat: number) {
  return 111_320 * Math.cos((lat * Math.PI) / 180);
}

function toMeters(coord: Position, origin: Position): MeterPoint {
  const [lng, lat] = coord;
  return {
    x: (lng - origin[0]) * metersPerDegreeLng(origin[1]),
    y: (lat - origin[1]) * 110_540,
  };
}

function fromMeters(point: MeterPoint, origin: Position): Position {
  return [
    origin[0] + point.x / metersPerDegreeLng(origin[1]),
    origin[1] + point.y / 110_540,
  ];
}

function distanceM(a: Position, b: Position) {
  const origin: Position = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const am = toMeters(a, origin);
  const bm = toMeters(b, origin);
  return Math.hypot(am.x - bm.x, am.y - bm.y);
}

function bearingFromVector(dx: number, dy: number) {
  return ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
}

function offsetCoordinate(coord: Position, bearing: number, meters: number) {
  const radians = (bearing * Math.PI) / 180;
  const origin = coord;
  return fromMeters(
    {
      x: Math.sin(radians) * meters,
      y: Math.cos(radians) * meters,
    },
    origin,
  );
}

function projectPointToLineString(
  pointCoord: Position,
  lineCoords: Position[],
): ProjectionBase | null {
  if (!Array.isArray(lineCoords) || lineCoords.length < 2) return null;
  const origin = pointCoord;
  const p = toMeters(pointCoord, origin);
  let best: ProjectionBase | null = null;

  for (let index = 0; index < lineCoords.length - 1; index += 1) {
    const aCoord = lineCoords[index];
    const bCoord = lineCoords[index + 1];
    if (!aCoord || !bCoord) continue;
    const a = toMeters(aCoord, origin);
    const b = toMeters(bCoord, origin);
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSq = dx * dx + dy * dy;
    if (lengthSq <= 1e-9) continue;

    const t = Math.max(
      0,
      Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq),
    );
    const snapped = { x: a.x + dx * t, y: a.y + dy * t };
    const distance = Math.hypot(p.x - snapped.x, p.y - snapped.y);
    const candidate = {
      coordinate: fromMeters(snapped, origin),
      distance_m: distance,
      segment_index: index,
      segment_t: t,
      tangent_bearing: bearingFromVector(dx, dy),
    };

    if (!best || candidate.distance_m < best.distance_m) {
      best = candidate;
    }
  }

  return best;
}

function visualFeatureId(feature: Feature, index: number) {
  const properties = feature.properties ?? {};
  return String(
    properties.corridor_id ??
      properties.bundle_id ??
      properties.lane_group_id ??
      properties.source_corridor_id ??
      properties.segment_id ??
      `visual-${index}`,
  );
}

function normalizeColorRouteIds(value: unknown) {
  if (Array.isArray(value)) return sortRoutes(value);
  if (value && typeof value === "object") {
    return sortRoutes(Object.values(value as Record<string, unknown>).flat());
  }
  return [];
}

function buildVisualIndex(visual: FeatureCollection): VisualFeature[] {
  return (visual.features ?? [])
    .map((feature, index): VisualFeature | null => {
      const geometry = feature.geometry;
      if (!geometry || geometry.type !== "LineString") return null;
      const coordinates = geometry.coordinates;
      if (!Array.isArray(coordinates) || coordinates.length < 2) return null;
      const properties = feature.properties ?? {};
      const routeIds = sortRoutes(properties.route_ids ?? []);
      const colorRouteIds = normalizeColorRouteIds(properties.color_route_ids);
      const allRouteIds = sortRoutes([...routeIds, ...colorRouteIds]);
      if (allRouteIds.length === 0) return null;

      return {
        feature: feature as Feature<LineStringGeometry>,
        index,
        id: visualFeatureId(feature, index),
        coordinates: coordinates as Position[],
        routeIds,
        colorRouteIds: colorRouteIds.length > 0 ? colorRouteIds : routeIds,
        allRouteIds,
        color: properties.color ?? null,
        corridorId: properties.corridor_id ?? null,
        physicalBundleId:
          properties.physical_bundle_id ?? properties.lane_group_id ?? null,
      };
    })
    .filter((feature): feature is VisualFeature => feature !== null);
}

// stations.geojson publishes the three physically distinct shuttles as plain
// "S"; the visual lanes carry FS (Franklin Av), GS (42 St), and H (Rockaway
// Park). Proximity disambiguates which shuttle a station belongs to.
const STATION_ROUTE_ALIASES = new Map<string, string[]>([
  ["S", ["S", "FS", "GS", "H"]],
  ["SIR", ["SIR", "SI"]],
]);

function routeMatchesVisual(visualFeature: VisualFeature, routeId: string) {
  const candidates = STATION_ROUTE_ALIASES.get(routeId) ?? [routeId];
  return candidates.some((id) => visualFeature.allRouteIds.includes(id));
}

function snapConfidence(distance: number) {
  if (distance <= MEDIUM_SNAP_DISTANCE_M) return "high";
  if (distance <= LOW_SNAP_DISTANCE_M) return "medium";
  return "low";
}

function bestProjectionForRoute({
  visualIndex,
  stationCoord,
  routeId,
  maxSnapM = MAX_SNAP_DISTANCE_M,
}: {
  visualIndex: VisualFeature[];
  stationCoord: Position;
  routeId: string;
  maxSnapM?: number;
}) {
  const rejected: Projection[] = [];
  let best: Projection | null = null;
  let candidateCount = 0;

  for (const visualFeature of visualIndex) {
    if (!routeMatchesVisual(visualFeature, routeId)) continue;
    candidateCount += 1;
    const projection = projectPointToLineString(
      stationCoord,
      visualFeature.coordinates,
    );
    if (!projection) continue;

    const enriched = {
      routeId,
      visualFeature,
      ...projection,
      score: projection.distance_m,
    };

    if (projection.distance_m > maxSnapM) {
      rejected.push(enriched);
      continue;
    }

    if (!best || enriched.score < best.score) {
      best = enriched;
    }
  }

  return { best, rejected, candidateCount };
}

function meanCoordinate(coords: Position[]): Position | null {
  if (coords.length === 0) return null;
  const lng = coords.reduce((sum, coord) => sum + coord[0], 0) / coords.length;
  const lat = coords.reduce((sum, coord) => sum + coord[1], 0) / coords.length;
  return [lng, lat];
}

function clustersShouldMerge(left: ProjectionCluster, right: ProjectionCluster) {
  if (!left.centroid || !right.centroid) return false;
  const centroidDistance = distanceM(left.centroid, right.centroid);
  if (centroidDistance > CLUSTER_MERGE_M) return false;

  const leftRoutes = new Set(left.projections.map((p) => p.routeId));
  const routeOverlap = right.projections.some((p) => leftRoutes.has(p.routeId));
  const leftBundles = new Set(
    left.projections
      .map((p) => p.visualFeature.physicalBundleId)
      .filter(Boolean),
  );
  const bundleOverlap = right.projections.some((p) =>
    leftBundles.has(p.visualFeature.physicalBundleId),
  );
  const leftCorridors = new Set(
    left.projections.map((p) => p.visualFeature.corridorId).filter(Boolean),
  );
  const corridorOverlap = right.projections.some((p) =>
    leftCorridors.has(p.visualFeature.corridorId),
  );

  return routeOverlap || bundleOverlap || corridorOverlap;
}

function clusterProjections(projections: Projection[]): ProjectionCluster[] {
  const clusters: ProjectionCluster[] = [];

  for (const projection of projections) {
    let target: ProjectionCluster | null = null;
    for (const cluster of clusters) {
      const linked = cluster.projections.some(
        (existing) =>
          distanceM(existing.coordinate, projection.coordinate) <=
          CLUSTER_LINK_M,
      );
      if (linked) {
        target = cluster;
        break;
      }
    }

    if (!target) {
      target = { projections: [] };
      clusters.push(target);
    }
    target.projections.push(projection);
    target.centroid = meanCoordinate(
      target.projections.map((p) => p.coordinate),
    ) ?? undefined;
  }

  let merged = true;
  while (merged) {
    merged = false;
    for (let i = 0; i < clusters.length; i += 1) {
      for (let j = i + 1; j < clusters.length; j += 1) {
        if (!clustersShouldMerge(clusters[i], clusters[j])) continue;
        clusters[i].projections.push(...clusters[j].projections);
        clusters[i].centroid = meanCoordinate(
          clusters[i].projections.map((p) => p.coordinate),
        ) ?? undefined;
        clusters.splice(j, 1);
        merged = true;
        break;
      }
      if (merged) break;
    }
  }

  return clusters;
}

// MapLibre wraps station labels at text-max-width (~10em). At our text sizes
// that is roughly 18 characters per wrapped line; names cap out at 3 lines.
const LABEL_WRAP_CHARS = 18;
const LABEL_MAX_LINES = 3;
// Raw icon_offset px (scaled by icon-size at render). 28px sits snugly under
// the marker; clearing a below-anchored label needs the text-top offset plus
// one wrapped line plus a gap (~68px raw), plus ~24px per extra line.
const BADGE_BELOW_LABEL_BASE_Y_PX = 68;
const BADGE_LABEL_LINE_Y_PX = 24;

function estimateLabelLines(name: string) {
  const length = String(name ?? "").trim().length;
  if (length === 0) return 1;
  return Math.min(LABEL_MAX_LINES, Math.ceil(length / LABEL_WRAP_CHARS));
}

function badgeLayout(
  index: number,
  count: number,
  { labelBelow = false, labelLines = 1 }: { labelBelow?: boolean; labelLines?: number } = {},
) {
  const rowCount = count <= 4 ? 1 : Math.ceil(count / BADGE_MAX_COLUMNS);
  const row = Math.floor(index / BADGE_MAX_COLUMNS);
  const firstIndexInRow = row * BADGE_MAX_COLUMNS;
  const rowSize = Math.min(BADGE_MAX_COLUMNS, count - firstIndexInRow);
  const col = index - firstIndexInRow;
  const x = (col - (rowSize - 1) / 2) * BADGE_SPACING_X_PX;
  // Apple layout: dot, station name, then the bullet row UNDER the name.
  // When the label is anchored below the point the badges must clear the
  // wrapped text; when the label sits above, badges stay snug under the dot.
  const baseY = labelBelow
    ? BADGE_BELOW_LABEL_BASE_Y_PX + (labelLines - 1) * BADGE_LABEL_LINE_Y_PX
    : BADGE_BASE_OFFSET_Y_PX;
  const y = baseY + (row - (rowCount - 1) / 2) * BADGE_SPACING_Y_PX;

  return { row, col, offset: [x, y] };
}

function representativeProjection(cluster: ProjectionCluster) {
  return [...cluster.projections].sort(
    (a, b) =>
      a.distance_m - b.distance_m ||
      routeSortValue(a.routeId) - routeSortValue(b.routeId),
  )[0];
}

function baseProperties({
  markerType,
  station,
  cluster,
  clusterId,
  center,
  debugCandidateCount,
  debugRejectedCandidateCount,
}: {
  markerType: string;
  station: StationFeature;
  cluster: ProjectionCluster;
  clusterId: string;
  center?: Position | null;
  debugCandidateCount: number;
  debugRejectedCandidateCount: number;
}): Record<string, any> {
  const stationProps = station.properties ?? {};
  const routes = sortRoutes(cluster.projections.map((p) => p.routeId));
  const visualIds = [
    ...new Set(cluster.projections.map((p) => p.visualFeature.id)),
  ];
  const snappedRouteIds = sortRoutes(
    cluster.projections.flatMap((p) => p.visualFeature.routeIds),
  );
  const snappedColorRouteIds = sortRoutes(
    cluster.projections.flatMap((p) => p.visualFeature.colorRouteIds),
  );
  const representative = representativeProjection(cluster);
  // The bundle midpoint (center) is where the marker is actually drawn; keep
  // snapped_coordinate consistent with it so diagnostics line up.
  const snappedCoordinate = center ?? representative?.coordinate ?? cluster.centroid;
  const snapDistance = Math.max(
    ...cluster.projections.map((p) => p.distance_m),
  );
  const tangent = representative?.tangent_bearing ?? 0;
  const normal = (tangent + 90) % 360;

  return {
    marker_type: markerType,
    station_id: String(stationProps.station_id ?? station.id ?? ""),
    name: String(stationProps.name ?? ""),
    route_ids: routes,
    route_count: routes.length,
    source_coordinate: station.geometry.coordinates,
    snapped_coordinate: snappedCoordinate,
    snapped_visual_feature_ids: visualIds,
    snapped_route_ids: snappedRouteIds,
    snapped_color_route_ids: snappedColorRouteIds,
    visual_corridor_id:
      representative?.visualFeature.corridorId ??
      representative?.visualFeature.id ??
      undefined,
    physical_bundle_id:
      representative?.visualFeature.physicalBundleId ?? undefined,
    local_tangent_bearing: tangent,
    local_normal_bearing: normal,
    snap_distance_m: snapDistance,
    snap_confidence: snapConfidence(snapDistance),
    marker_priority: routes.length * 10 + (routes.length > 1 ? 20 : 0),
    min_zoom:
      markerType === "station_label" || markerType === "station_route_badge"
        ? 14.5
        : routes.length > 1
          ? 11.5
          : 12.5,
    max_zoom:
      markerType === "single_stop_dot"
        ? 16
        : markerType === "shared_stop_dot" || markerType === "shared_stop_bar"
          ? 16.5
          : undefined,
    debug_candidate_count: debugCandidateCount,
    debug_rejected_candidate_count: debugRejectedCandidateCount,
    debug_cluster_id: clusterId,
  };
}

function makePointFeature(properties: Record<string, any>, coordinate: Position): Feature<PointGeometry> {
  return {
    type: "Feature",
    properties,
    geometry: {
      type: "Point",
      coordinates: coordinate,
    },
  };
}

function projectOffsetAlongNormal(coord: Position, ref: Position, normalBearing: number) {
  // Signed metres from ref to coord measured along the normal direction (the
  // same axis offsetCoordinate moves along), so positive/negative separate the
  // two sides of the bundle.
  const m = toMeters(coord, ref);
  const radians = (normalBearing * Math.PI) / 180;
  return m.x * Math.sin(radians) + m.y * Math.cos(radians);
}

// Geometry of the served lane bundle at a station: the perpendicular extent of
// every snapped lane and the geometric MIDPOINT between the two outermost
// lanes. Markers center on this midpoint and the shared-stop bar spans the
// whole extent -- so capsules/dots sit on top of the bundle and the bar
// crosses every line it serves, not just the nearest lane.
function clusterBundleGeometry(cluster: ProjectionCluster) {
  const coords = cluster.projections.map((p) => p.coordinate);
  const ref = meanCoordinate(coords) ?? coords[0];
  const tangent = representativeProjection(cluster)?.tangent_bearing ?? 0;
  const normal = (tangent + 90) % 360;
  let minOff = Infinity;
  let maxOff = -Infinity;
  for (const coord of coords) {
    const off = projectOffsetAlongNormal(coord, ref, normal);
    if (off < minOff) minOff = off;
    if (off > maxOff) maxOff = off;
  }
  if (!Number.isFinite(minOff)) {
    minOff = 0;
    maxOff = 0;
  }
  return {
    ref,
    normal,
    tangent,
    laneSpan: maxOff - minOff,
    center: offsetCoordinate(ref, normal, (minOff + maxOff) / 2),
  };
}

function makeSharedBarFeature(
  properties: Record<string, any>,
  bundle: ReturnType<typeof clusterBundleGeometry>,
): Feature<LineStringGeometry> {
  const routeCount = properties.route_ids.length;
  const minLength = Math.min(
    SHARED_BAR_MAX_LENGTH_M,
    SHARED_BAR_BASE_LENGTH_M + routeCount * SHARED_BAR_PER_ROUTE_M,
  );
  // Long enough to cross every served lane (full span + end caps), but never
  // shorter than the base tick so an isolated transfer still reads clearly.
  const length = Math.max(minLength, bundle.laneSpan + 2 * SHARED_BAR_END_PADDING_M);
  const half = length / 2;
  const start = offsetCoordinate(bundle.center, bundle.normal, -half);
  const end = offsetCoordinate(bundle.center, bundle.normal, half);
  return {
    type: "Feature",
    properties,
    geometry: {
      type: "LineString",
      coordinates: [start, end],
    },
  };
}

function emitClusterFeatures({
  station,
  cluster,
  clusterId,
  debugCandidateCount,
  debugRejectedCandidateCount,
}: {
  station: StationFeature;
  cluster: ProjectionCluster;
  clusterId: string;
  debugCandidateCount: number;
  debugRejectedCandidateCount: number;
}): Feature[] {
  const routes = sortRoutes(cluster.projections.map((p) => p.routeId));
  const colors = new Set(routes.map(routeColor));
  const bundle = clusterBundleGeometry(cluster);
  const center = bundle.center;
  const features: Feature[] = [];
  const markerType =
    routes.length === 1
      ? "single_stop_dot"
      : colors.size === 1
        ? "shared_stop_dot"
        : "shared_stop_bar";

  const stopProperties = baseProperties({
    markerType,
    station,
    cluster,
    clusterId,
    center,
    debugCandidateCount,
    debugRejectedCandidateCount,
  });
  // Single-route AND same-color multi-route stops take the LINE color, so the
  // dot reads as a bead sitting on its colored line (Apple style). Only genuine
  // multi-color transfers (shared_stop_bar) use the neutral white node.
  stopProperties.color =
    colors.size === 1 ? routeColor(routes[0]) : "#f4f6f8";
  if (colors.size === 1) {
    // Apple bead rim: a darker shade of the line's own hue, baked for the
    // runtime circle-stroke (replaces the old neutral near-black ring).
    stopProperties.dot_color = darkenHexColor(stopProperties.color, 0.45);
  }

  if (markerType === "shared_stop_bar") {
    features.push(makeSharedBarFeature(stopProperties, bundle));
  } else {
    features.push(makePointFeature(stopProperties, center));
  }

  const labelProperties = baseProperties({
    markerType: "station_label",
    station,
    cluster,
    clusterId,
    center,
    debugCandidateCount,
    debugRejectedCandidateCount,
  });
  const labelBelow = routes.length === 1;
  labelProperties.label_anchor = labelBelow ? "top" : "bottom";
  labelProperties.label_offset = labelBelow ? [0, 1.1] : [0, -1.45];
  features.push(makePointFeature(labelProperties, center));

  const labelLines = estimateLabelLines(labelProperties.name);
  routes.forEach((routeId, index) => {
    const layout = badgeLayout(index, routes.length, { labelBelow, labelLines });
    const badgeProperties = baseProperties({
      markerType: "station_route_badge",
      station,
      cluster,
      clusterId,
      center,
      debugCandidateCount,
      debugRejectedCandidateCount,
    });
    badgeProperties.route_ids = [routeId];
    badgeProperties.route_id = routeId;
    badgeProperties.route_count = routes.length;
    badgeProperties.badge_index = index;
    badgeProperties.badge_count = routes.length;
    badgeProperties.badge_row = layout.row;
    badgeProperties.badge_col = layout.col;
    badgeProperties.icon_id = subwayBulletName(routeId);
    badgeProperties.icon_offset = layout.offset;
    features.push(makePointFeature(badgeProperties, center));
  });

  return features;
}

export function buildStationAnchors({
  visual,
  stations,
  options = {},
}: {
  visual: FeatureCollection;
  stations: FeatureCollection<StationFeature>;
  options?: { maxSnapDistanceM?: number };
}): StationBuildResult & { metadata: Record<string, any> } {
  const visualIndex = buildVisualIndex(visual);
  const anchors: Feature[] = [];
  const raw: Feature[] = [];
  const snaps: Feature[] = [];
  const rejected: Feature[] = [];
  const ambiguous: Feature[] = [];

  const stationFeatures = stations.features ?? [];
  for (const station of stationFeatures) {
    if (!station.geometry || station.geometry.type !== "Point") continue;
    const routeIds = sortRoutes(station.properties?.route_ids ?? []);
    raw.push(rawStationDebugFeature(station, routeIds));

    const stationCoord = station.geometry.coordinates;
    const projections: Projection[] = [];
    let debugCandidateCount = 0;
    let debugRejectedCandidateCount = 0;

    const unmatchedRouteIds: string[] = [];
    for (const routeId of routeIds) {
      const result = bestProjectionForRoute({
        visualIndex,
        stationCoord,
        routeId,
      });
      debugCandidateCount += result.candidateCount;
      debugRejectedCandidateCount += result.rejected.length;
      for (const rejectedProjection of result.rejected) {
        rejected.push(rejectedDebugFeature(station, rejectedProjection));
      }
      if (!result.best) {
        unmatchedRouteIds.push(routeId);
        continue;
      }
      projections.push(result.best);
      snaps.push(snapDebugFeature(station, result.best));
    }

    // Relaxed retry runs PER ROUTE, not only when the whole station failed:
    // at Court Sq the 7 snaps at ~20m while the G terminal lane sits ~127m
    // out -- without the per-route retry the G silently vanishes from its
    // own terminal (snap_confidence comes out "low" from the distance).
    for (const routeId of unmatchedRouteIds) {
      const result = bestProjectionForRoute({
        visualIndex,
        stationCoord,
        routeId,
        maxSnapM: RELAXED_SNAP_DISTANCE_M,
      });
      if (!result.best) continue;
      projections.push(result.best);
      snaps.push(snapDebugFeature(station, result.best));
    }

    if (projections.length === 0) {
      ambiguous.push(
        ambiguousDebugFeature(station, routeIds, "no_valid_projection", {
          debug_candidate_count: debugCandidateCount,
          debug_rejected_candidate_count: debugRejectedCandidateCount,
        }),
      );
      continue;
    }

    const clusters = clusterProjections(projections);
    clusters.forEach((cluster, index) => {
      const stationId = String(
        station.properties?.station_id ?? station.id ?? "station",
      );
      anchors.push(
        ...emitClusterFeatures({
          station,
          cluster,
          clusterId: `${stationId}-cluster-${index}`,
          debugCandidateCount,
          debugRejectedCandidateCount,
        }),
      );
    });
  }

  const result = {
    anchors: featureCollection(anchors),
    raw: featureCollection(raw),
    snaps: featureCollection(snaps),
    rejected: featureCollection(rejected),
    ambiguous: featureCollection(ambiguous),
    metadata: {
      max_snap_distance_m: options.maxSnapDistanceM ?? MAX_SNAP_DISTANCE_M,
      visual_generated_at: visual.metadata?.generated_at ?? null,
      visual_geometry_source: visual.metadata?.visual_geometry_source ?? null,
      visual_geometry_source_dataset_id:
        visual.metadata?.visual_geometry_source_dataset_id ?? null,
      visual_feature_count: visualIndex.length,
      station_count: stationFeatures.length,
      anchor_feature_count: anchors.length,
    },
  };
  result.anchors.metadata = result.metadata;
  return result;
}

const RUNTIME_DEBUG_PROPERTY_KEYS = [
  "debug_candidate_count",
  "debug_rejected_candidate_count",
  "debug_cluster_id",
];

export function stripRuntimeStationAnchorDebugProperties(
  collection: FeatureCollection,
): FeatureCollection {
  return {
    ...collection,
    features: (collection.features ?? []).map((feature: Feature) => {
      const properties = { ...(feature.properties ?? {}) };
      for (const key of RUNTIME_DEBUG_PROPERTY_KEYS) {
        delete properties[key];
      }
      return { ...feature, properties };
    }),
  };
}

export function splitStationAnchorCollections(anchors: FeatureCollection) {
  const features = anchors.features ?? [];
  return {
    dots: featureCollection(
      features.filter(
        (feature) => feature.properties?.marker_type === "single_stop_dot",
      ),
    ),
    sharedStops: featureCollection(
      features.filter((feature) =>
        ["shared_stop_dot", "shared_stop_bar"].includes(
          feature.properties?.marker_type,
        ),
      ),
    ),
    labels: featureCollection(
      features.filter(
        (feature) => feature.properties?.marker_type === "station_label",
      ),
    ),
    badges: featureCollection(
      features.filter(
        (feature) =>
          feature.properties?.marker_type === "station_route_badge",
      ),
    ),
  };
}
