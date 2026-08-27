import maplibregl from "maplibre-gl";
import { getLineColor } from "./route-layers";
import { subwayBulletName, subwayBulletSrc } from "../smart-route/train-bullet";

// =====================================================================
// Apple Maps-faithful subway rendering
//
// The map is drawn entirely from the build-time visual network artifact
// (subway-network.visual.geojson). This module:
//   1. Adapts that artifact into render features (one feature per
//      distinct MTA color group per corridor) via
//      buildSubwayLaneFeaturesFromVisual.
//   2. Exposes a per-(station, route) stop-dot feature collection
//      derived from subway-network.stations.geojson.
//   3. Installs the MapLibre line / circle / symbol layers.
// =====================================================================

// =====================================================================
// Types
// =====================================================================

type StationFeature = GeoJSON.Feature<
  GeoJSON.Point,
  {
    station_id: string;
    name: string;
    route_ids: string[];
    is_transfer?: boolean;
  }
>;

interface LaneSegmentProps {
  route_id: string;
  color: string;
  lane_slot: number;
  corridor: string | null;
}

interface StopDotProps {
  station_id: string;
  route_id: string;
  color: string;
  is_transfer: boolean;
}

type StationMarkerType =
  | "single_stop_dot"
  | "shared_stop_dot"
  | "shared_stop_bar"
  | "station_label"
  | "station_route_badge";

interface StationAnchorProps {
  marker_type: StationMarkerType;
  station_id: string;
  name: string;
  route_ids: string[];
  route_count: number;
  color?: string;
  icon_id?: string;
  icon_offset?: [number, number];
  marker_priority?: number;
  min_zoom?: number;
  max_zoom?: number;
}

export interface SubwayStationMarkerCollections {
  dots: GeoJSON.FeatureCollection<GeoJSON.Point, StationAnchorProps>;
  sharedStops: GeoJSON.FeatureCollection<
    GeoJSON.Point | GeoJSON.LineString,
    StationAnchorProps
  >;
  labels: GeoJSON.FeatureCollection<GeoJSON.Point, StationAnchorProps>;
  badges: GeoJSON.FeatureCollection<GeoJSON.Point, StationAnchorProps>;
}

// =====================================================================
// Constants
// =====================================================================

const SUBWAY_NETWORK_SOURCE_ID = "sr-subway-network";
const SUBWAY_STOP_SOURCE_ID = "sr-subway-stops";
export const SUBWAY_STATION_DOTS_SOURCE_ID = "sr-subway-station-dots";
export const SUBWAY_STATION_SHARED_STOPS_SOURCE_ID =
  "sr-subway-station-shared-stops";
export const SUBWAY_STATION_LABELS_SOURCE_ID = "sr-subway-station-labels";
export const SUBWAY_STATION_ROUTE_BADGES_SOURCE_ID =
  "sr-subway-station-route-badges";
export const SUBWAY_GLOW_LAYER_ID = "sr-subway-glow";
export const SUBWAY_CASING_LAYER_ID = "sr-subway-casing";
export const SUBWAY_FILL_LAYER_ID = "sr-subway-fill";
export const SUBWAY_HIGHLIGHT_LAYER_ID = "sr-subway-highlight";
export const SUBWAY_STOP_DOT_LAYER_ID = "sr-subway-stop-dot";
export const SUBWAY_STATION_SINGLE_DOTS_LAYER_ID =
  "sr-subway-station-single-dots";
export const SUBWAY_STATION_SHARED_DOTS_LAYER_ID =
  "sr-subway-station-shared-dots";
export const SUBWAY_STATION_SHARED_BAR_CASING_LAYER_ID =
  "sr-subway-station-shared-bars-casing";
export const SUBWAY_STATION_SHARED_BAR_FILL_LAYER_ID =
  "sr-subway-station-shared-bars-fill";
export const SUBWAY_STATION_LABELS_LAYER_ID = "sr-subway-station-labels";
export const SUBWAY_STATION_ROUTE_BADGES_LAYER_ID =
  "sr-subway-station-route-badges";
// =====================================================================
// Gate 2E — Visual network builder
//
// Consumes the Gate-2D-validated artifact subway-network.visual.geojson
// (corridor features with `route_ids: string[]`) and emits one render
// feature per distinct MTA color group per corridor.
//
// Same-color collapse: in a corridor whose route_ids include B/D/F/M, we
// emit ONE orange feature with route_ids = ["B","D","F","M"]. We do NOT
// emit four separate orange polylines that would visually stack on the
// same lane.
//
// Different-color collapse: corridors with B/D + N/Q emit TWO features
// (orange + yellow) with distinct lane_slots so they render as a
// parallel pair under the existing line-offset paint expression.
//
// Lane slot is centered: K distinct colors → slots [- (K-1)/2 ... +(K-1)/2 ].
// Z-order is by color rank (utility/shuttle colors lowest; prominent
// trunk colors highest) so high-traffic routes paint over noise.
// =====================================================================

// Stable rank for visual_z_order / line-sort-key. Lower number draws
// first (under). Utility/shuttle colors at the bottom; prominent route
// families on top.
const COLOR_VISUAL_Z_ORDER: Record<string, number> = {
  "#808183": 0, // S/FS/H gray shuttles
  "#A7A9AC": 1, // L gray
  "#996633": 2, // J/Z brown
  "#6CBE45": 3, // G light green
  "#0078C6": 4, // SI blue
  "#FCCC0A": 5, // N/Q/R/W yellow
  "#B933AD": 6, // 7 purple
  "#0A84FF": 7, // A/C/E Apple system blue
  "#FF6319": 8, // B/D/F/M orange
  "#00933C": 9, // 4/5/6 green
  "#EE352E": 10, // 1/2/3 red
};

function visualZOrderForColor(color: string): number {
  return COLOR_VISUAL_Z_ORDER[color] ?? 100;
}

// Normalize segment direction so MapLibre's perpendicular line-offset is
// stable. Heuristic: reverse if the segment trends westward or southward.
// This gives every emitted feature a consistent "left side" so the
// line-offset paint expression produces predictable parallel lanes.
function normalizeVisualDirection(
  coords: [number, number][],
): [number, number][] {
  if (coords.length < 2) return coords;
  const first = coords[0];
  const last = coords[coords.length - 1];
  const dLng = last[0] - first[0];
  const dLat = last[1] - first[1];
  // If the segment is more horizontal than vertical, normalize to east.
  // Otherwise normalize to north.
  if (Math.abs(dLng) > Math.abs(dLat)) {
    return dLng >= 0 ? coords : coords.slice().reverse();
  }
  return dLat >= 0 ? coords : coords.slice().reverse();
}

interface VisualLaneProps extends LaneSegmentProps {
  /** Full set of routes that share this corridor (for click inspector) */
  route_ids: string[];
  /** Routes represented by this emitted color lane. */
  color_route_ids: string[];
  /** Stable color-rank z-order; used as line-sort-key */
  visual_z_order: number;
  /** Source corridor id from build-subway-visual-network.mjs */
  corridor_id: string | null;
  /** Source stop-pair label, e.g. "Atlantic Av-Barclays → Pacific St" */
  stop_pair: string | null;
  /** Original arc length in meters (from the longest member edge) */
  length_m: number;
  /** Source shape ids (for click inspector / debugging) */
  source_shape_ids: string[];
  /** Source visual edge ids from the Gate 2D artifact. */
  source_edge_ids: string[];
  representative_route_id: string;
  lane_group_id: string | null;
  lane_slot_source: "bundle" | "chain" | "local";
  lane_order_basis: string[];
  /** Optional segment-side hint emitted by the visual artifact builder. */
  cross_color_segment_side?: number;
  bundle_id?: string | null;
  visual_feature_type?: string | null;
  /**
   * The original (semantic) lane slot computed by the visual builder
   * before geometry baking. Preserved for click inspector / debugging.
   */
  lane_slot_semantic?: number;
  /**
   * True when the build script has baked the lane offset into the
   * geometry coordinates. Runtime emits `lane_slot: 0` so the paint
   * expression does not double-offset.
   */
  lane_offset_baked?: boolean;
}

/**
 * Build lane render features from a Gate-2D-validated visual geojson.
 * Emits one feature per (corridor, distinct MTA color group).
 */
export function buildSubwayLaneFeaturesFromVisual(
  visual: GeoJSON.FeatureCollection,
): GeoJSON.FeatureCollection<GeoJSON.LineString, VisualLaneProps> {
  const out: GeoJSON.Feature<GeoJSON.LineString, VisualLaneProps>[] = [];

  for (const raw of visual.features) {
    if (raw.geometry?.type !== "LineString") continue;
    const coords = raw.geometry.coordinates as [number, number][];
    if (coords.length < 2) continue;

    const routeIds = Array.isArray(raw.properties?.route_ids)
      ? (raw.properties!.route_ids as string[]).map((r) => String(r))
      : [];
    if (routeIds.length === 0) continue;

    const rawProps = raw.properties ?? {};
    if (rawProps.visual_feature_type === "bundle_lane") {
      const color = String(rawProps.color ?? getLineColor(String(rawProps.route_id ?? routeIds[0])));
      const colorRouteIds = Array.isArray(rawProps.color_route_ids)
        ? (rawProps.color_route_ids as string[]).map((routeId) => String(routeId))
        : routeIds.filter((routeId) => getLineColor(routeId) === color);
      const representativeRouteId =
        String(rawProps.representative_route_id ?? rawProps.route_id ?? colorRouteIds[0] ?? routeIds[0]);
      const laneOrderBasis = Array.isArray(rawProps.lane_order_basis)
        ? (rawProps.lane_order_basis as string[]).map((basis) => String(basis))
        : [color];
      // If the build script pre-baked the offset into the geometry, do
      // NOT apply any MapLibre line-offset at runtime — pass lane_slot=0
      // through to the paint expression. Also skip direction normalization
      // because the baked geometry is already correctly directed.
      const laneOffsetBaked = rawProps.lane_offset_baked === true;
      const renderLaneSlot = laneOffsetBaked
        ? 0
        : Number(rawProps.lane_slot ?? 0);
      const semanticLaneSlot = Number(
        rawProps.lane_slot_semantic ?? rawProps.lane_slot ?? 0,
      );
      const renderCoords = laneOffsetBaked
        ? coords
        : normalizeVisualDirection(coords);
      out.push({
        type: "Feature",
        properties: {
          route_id: representativeRouteId,
          representative_route_id: representativeRouteId,
          color,
          lane_slot: renderLaneSlot,
          lane_slot_semantic: semanticLaneSlot,
          // Segment-level cross-color spread (cross-color-spread.ts v2) only
          // tapers a SUB-extent of the feature, so it can't set a whole-feature
          // lane_slot_semantic without lying about the untouched majority of
          // the line. It still carries a per-feature side flag for the fill
          // sort-key to fall back on ahead of the color-rank tiebreak.
          cross_color_segment_side: Number(rawProps.cross_color_segment_side ?? 0),
          lane_offset_baked: laneOffsetBaked,
          corridor: null,
          route_ids: routeIds,
          color_route_ids: colorRouteIds,
          visual_z_order: visualZOrderForColor(color),
          corridor_id: String(rawProps.corridor_id ?? rawProps.bundle_id ?? "") || null,
          stop_pair: rawProps.from_stop_name && rawProps.to_stop_name
            ? `${rawProps.from_stop_name} â†’ ${rawProps.to_stop_name}`
            : null,
          length_m: Number(rawProps.length_m ?? 0),
          source_shape_ids: Array.isArray(rawProps.source_shape_ids)
            ? (rawProps.source_shape_ids as string[])
            : [],
          source_edge_ids: Array.isArray(rawProps.source_edge_ids)
            ? (rawProps.source_edge_ids as string[])
            : [],
          lane_group_id: String(rawProps.lane_group_id ?? rawProps.bundle_id ?? "") || null,
          lane_slot_source: "bundle",
          lane_order_basis: laneOrderBasis,
          bundle_id: String(rawProps.bundle_id ?? "") || null,
          visual_feature_type: "bundle_lane",
        },
        geometry: { type: "LineString", coordinates: renderCoords },
      });
      continue;
    }

    // Group routes by their MTA color, preserving first-seen order
    const colorBuckets = new Map<string, string[]>();
    for (const rid of routeIds) {
      const c = getLineColor(rid);
      if (!colorBuckets.has(c)) colorBuckets.set(c, []);
      colorBuckets.get(c)!.push(rid);
    }
    const distinctColors = [...colorBuckets.keys()];
    const K = distinctColors.length;
    // Sort the distinct colors by their visual_z_order so the lane-slot
    // assignment is stable run-to-run and the parallel order on a bundle
    // matches the global color hierarchy.
    distinctColors.sort(
      (a, b) => visualZOrderForColor(a) - visualZOrderForColor(b),
    );

    const normalized = normalizeVisualDirection(coords);

    const laneColorSlots =
      raw.properties?.lane_color_slots &&
      typeof raw.properties.lane_color_slots === "object"
        ? (raw.properties.lane_color_slots as Record<string, unknown>)
        : null;
    const laneOrderBasis = Array.isArray(raw.properties?.lane_order_basis)
      ? (raw.properties!.lane_order_basis as string[]).map((color) => String(color))
      : distinctColors;

    distinctColors.forEach((color, idx) => {
      const localSlot = idx - (K - 1) / 2;
      const chainSlot = Number(laneColorSlots?.[color]);
      const slot = Number.isFinite(chainSlot) ? chainSlot : localSlot;
      const routesInColor = colorBuckets.get(color) ?? [];
      const representativeRouteId = [...routesInColor].sort((a, b) =>
        a.localeCompare(b, "en", { numeric: true }),
      )[0] ?? "";
      const props = raw.properties ?? {};
      out.push({
        type: "Feature",
        properties: {
          route_id: representativeRouteId,
          representative_route_id: representativeRouteId,
          color,
          lane_slot: slot,
          corridor: null,
          route_ids: routeIds,
          color_route_ids: routesInColor,
          visual_z_order: visualZOrderForColor(color),
          corridor_id: String(props.corridor_id ?? "") || null,
          stop_pair: props.from_stop_name && props.to_stop_name
            ? `${props.from_stop_name} → ${props.to_stop_name}`
            : null,
          length_m: Number(props.longest_member_length_m ?? props.length_m ?? 0),
          source_shape_ids: Array.isArray(props.source_shape_ids)
            ? (props.source_shape_ids as string[])
            : [],
          source_edge_ids: Array.isArray(props.source_edge_ids)
            ? (props.source_edge_ids as string[])
            : [],
          lane_group_id: String(props.lane_group_id ?? "") || null,
          lane_slot_source:
            props.lane_slot_source === "chain" ? "chain" : "local",
          lane_order_basis: laneOrderBasis,
        },
        geometry: { type: "LineString", coordinates: normalized },
      });
    });
  }

  // Stable paint order: by visual_z_order ascending, then by first route_id
  out.sort((a, b) => {
    if (a.properties.visual_z_order !== b.properties.visual_z_order) {
      return a.properties.visual_z_order - b.properties.visual_z_order;
    }
    const aRid = a.properties.route_ids[0] ?? "";
    const bRid = b.properties.route_ids[0] ?? "";
    return aRid.localeCompare(bRid, "en", { numeric: true });
  });

  return { type: "FeatureCollection", features: out };
}

/**
 * Summary statistics for the visual lane builder. For console logging.
 */
export function summarizeVisualLanes(
  features: GeoJSON.FeatureCollection<GeoJSON.LineString, VisualLaneProps>,
): {
  renderFeatures: number;
  distinctRoutes: number;
  distinctColorGroups: number;
  distinctColorLanes: number;
  multiColorCorridors: number;
  corridorsWithMultipleRoutes: number;
} {
  const routes = new Set<string>();
  const colors = new Set<string>();
  const corridorColorCounts = new Map<string, Set<string>>();
  const routeCountsByCorridor = new Map<string, number>();
  for (const f of features.features) {
    for (const r of f.properties.route_ids) routes.add(r);
    colors.add(f.properties.color);
    const cid = f.properties.corridor_id ?? "";
    if (!corridorColorCounts.has(cid)) corridorColorCounts.set(cid, new Set());
    corridorColorCounts.get(cid)!.add(f.properties.color);
    routeCountsByCorridor.set(
      cid,
      Math.max(routeCountsByCorridor.get(cid) ?? 0, f.properties.route_ids.length),
    );
  }
  let multiColor = 0;
  for (const s of corridorColorCounts.values()) {
    if (s.size > 1) multiColor += 1;
  }
  let multiRoute = 0;
  for (const routeCount of routeCountsByCorridor.values()) {
    if (routeCount > 1) multiRoute += 1;
  }
  return {
    renderFeatures: features.features.length,
    distinctRoutes: routes.size,
    distinctColorGroups: colors.size,
    distinctColorLanes: features.features.length,
    multiColorCorridors: multiColor,
    corridorsWithMultipleRoutes: multiRoute,
  };
}

/**
 * For each station feature in stations.geojson, emit one stop-dot
 * Point feature per route_id at the station's coords. Dot color
 * matches the route. Position is the un-offset station coord — at
 * z14+ with small lane offsets the discrepancy is invisible.
 */
export function buildSubwayStopFeatures(
  stations: GeoJSON.FeatureCollection,
): GeoJSON.FeatureCollection<GeoJSON.Point, StopDotProps> {
  const out: GeoJSON.Feature<GeoJSON.Point, StopDotProps>[] = [];
  for (const raw of stations.features) {
    if (raw.geometry?.type !== "Point") continue;
    const s = raw as StationFeature;
    const routeIds = Array.isArray(s.properties.route_ids)
      ? s.properties.route_ids
      : [];
    const isTransfer = Boolean(s.properties.is_transfer);
    const coord = s.geometry.coordinates as [number, number];
    for (const rid of routeIds) {
      out.push({
        type: "Feature",
        properties: {
          station_id: s.properties.station_id,
          route_id: rid,
          color: getLineColor(rid),
          is_transfer: isTransfer,
        },
        geometry: { type: "Point", coordinates: coord },
      });
    }
  }
  return { type: "FeatureCollection", features: out };
}

function emptyPointCollection(): GeoJSON.FeatureCollection<GeoJSON.Point, StationAnchorProps> {
  return { type: "FeatureCollection", features: [] };
}

function emptySharedStopCollection(): GeoJSON.FeatureCollection<
  GeoJSON.Point | GeoJSON.LineString,
  StationAnchorProps
> {
  return { type: "FeatureCollection", features: [] };
}

export function splitStationAnchorFeatureCollections(
  anchors: GeoJSON.FeatureCollection,
): SubwayStationMarkerCollections {
  const features = anchors.features.filter(
    (feature): feature is GeoJSON.Feature<
      GeoJSON.Point | GeoJSON.LineString,
      StationAnchorProps
    > => Boolean(feature.properties?.marker_type),
  );

  return {
    dots: {
      type: "FeatureCollection",
      features: features.filter(
        (feature): feature is GeoJSON.Feature<GeoJSON.Point, StationAnchorProps> =>
          feature.geometry?.type === "Point" &&
          feature.properties.marker_type === "single_stop_dot",
      ),
    },
    sharedStops: {
      type: "FeatureCollection",
      features: features.filter((feature) =>
        ["shared_stop_dot", "shared_stop_bar"].includes(
          feature.properties.marker_type,
        ),
      ),
    },
    labels: {
      type: "FeatureCollection",
      features: features.filter(
        (feature): feature is GeoJSON.Feature<GeoJSON.Point, StationAnchorProps> =>
          feature.geometry?.type === "Point" &&
          feature.properties.marker_type === "station_label",
      ),
    },
    badges: {
      type: "FeatureCollection",
      features: features.filter(
        (feature): feature is GeoJSON.Feature<GeoJSON.Point, StationAnchorProps> =>
          feature.geometry?.type === "Point" &&
          feature.properties.marker_type === "station_route_badge",
      ),
    },
  };
}

export function emptySubwayStationMarkerCollections(): SubwayStationMarkerCollections {
  return {
    dots: emptyPointCollection(),
    sharedStops: emptySharedStopCollection(),
    labels: emptyPointCollection(),
    badges: emptyPointCollection(),
  };
}

export function stationMarkerRouteIds(
  stationMarkers: SubwayStationMarkerCollections | null | undefined,
): string[] {
  if (!stationMarkers) return [];
  const routeIds = new Set<string>();
  for (const feature of stationMarkers.badges.features) {
    const routeId = (feature.properties as StationAnchorProps & { route_id?: string }).route_id;
    if (routeId) routeIds.add(routeId);
    for (const value of feature.properties.route_ids ?? []) routeIds.add(value);
  }
  return [...routeIds];
}

export async function ensureMtaBulletImages(
  map: maplibregl.Map,
  routeIds: string[],
): Promise<void> {
  if (typeof window === "undefined" || typeof Image === "undefined") return;
  const uniqueRouteIds = [...new Set(routeIds.filter(Boolean))];

  await Promise.all(
    uniqueRouteIds.map(async (routeId) => {
      const imageId = subwayBulletName(routeId);
      if (map.hasImage(imageId)) return;

      try {
        const image = await new Promise<HTMLImageElement>((resolve, reject) => {
          const img = new Image();
          img.onload = () => resolve(img);
          img.onerror = () => reject(new Error(`Failed to load ${imageId}`));
          img.src = subwayBulletSrc(routeId);
        });
        if (map.hasImage(imageId)) return;
        const size = 48;
        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        ctx.drawImage(image, 0, 0, size, size);
        const imageData = ctx.getImageData(0, 0, size, size);
        map.addImage(imageId, imageData, { pixelRatio: 2 });
      } catch (error) {
        if (process.env.NODE_ENV !== "production") {
          globalThis.reportError?.(
            new Error(`Failed to register subway icon ${imageId} for ${routeId}`, {
              cause: error,
            }),
          );
        }
      }
    }),
  );
}

function setSubwayLayerVisibility(
  map: maplibregl.Map,
  layerId: string,
  visibility: "visible" | "none",
) {
  if (!map.getLayer(layerId)) return;
  map.setLayoutProperty(layerId, "visibility", visibility);
}

function addOrUpdateGeoJsonSource(
  map: maplibregl.Map,
  sourceId: string,
  data: GeoJSON.FeatureCollection,
) {
  const existing = map.getSource(sourceId) as maplibregl.GeoJSONSource | undefined;
  if (existing) {
    existing.setData(data);
    return;
  }

  map.addSource(sourceId, {
    type: "geojson",
    data,
  });
}

function addLayerIfMissing(
  map: maplibregl.Map,
  layer: maplibregl.AddLayerObject,
  beforeId?: string,
) {
  if (map.getLayer(layer.id)) return;
  if (beforeId && map.getLayer(beforeId)) {
    map.addLayer(layer, beforeId);
    return;
  }
  map.addLayer(layer);
}

// =====================================================================
// MapLibre layer setup
// =====================================================================

// Lane separation is baked into the geometry at LANE_WIDTH_METERS (12m)
// per slot. 12m is useful at high zoom, but it collapses to a fraction
// of a screen pixel at neighborhood/borough zooms. When the color fill is
// wider than the apparent inter-lane separation, the globally top-painted
// color (red/orange/blue in busy trunks) visually overpowers its siblings.
//
// Apple-style transit maps preserve a small screen-space lane pitch at
// those zooms. Baked lanes (lane_slot: 0, semantic slot preserved) get a
// modest runtime top-up that keeps adjacent color lanes separated through
// z11-z14, then fades out once the baked meter offset is large enough on its
// own. Keep this top-up small: the artifact is emitted as many baked
// LineStrings, and a large second screen-space offset pulls adjacent pieces
// apart at seams, making trunks look visually broken at neighborhood zooms.
// Sign convention matches the bake (right-hand normal == MapLibre positive
// line-offset), so the top-up expands the bundle outward. Unbaked lanes keep
// a full per-slot offset.
function laneOffsetAt(
  fullPerSlotPx: number,
  bakedTopUpPx: number,
): maplibregl.ExpressionSpecification {
  return [
    "+",
    ["*", ["coalesce", ["get", "lane_slot"], 0], fullPerSlotPx],
    [
      "*",
      [
        "case",
        ["==", ["get", "lane_offset_baked"], true],
        ["coalesce", ["get", "lane_slot_semantic"], 0],
        0,
      ],
      bakedTopUpPx,
    ],
  ] as unknown as maplibregl.ExpressionSpecification;
}

// The baked top-up (second arg) peaks across z12.5-14 -- the neighbourhood band
// where a bundle is read up close but the baked 12 m offset is still only
// ~1-1.7 screen px, so two adjacent colour fills would otherwise touch and the
// top-painted one would swallow its sibling. Peaking the top-up here opens a
// dark gutter between every bundled lane (so no colour overwhelms a neighbour)
// while leaving z15+ mostly untouched (the baked offset alone is wide enough).
// The top-up is capped at 2.6 px by subway-renderer.check.mjs -- beyond that
// the independently emitted LineString pieces can pull apart at their seams.
const LANE_OFFSET_EXPR: maplibregl.ExpressionSpecification = [
  "interpolate",
  ["linear"],
  ["zoom"],
  11,
  laneOffsetAt(1.15, 1.55),
  12.5,
  laneOffsetAt(2.05, 2.35),
  13,
  laneOffsetAt(2.55, 2.45),
  14,
  laneOffsetAt(3.25, 2.3),
  15,
  laneOffsetAt(3.85, 0.45),
  15.5,
  laneOffsetAt(3.95, 0),
  17,
  laneOffsetAt(5.25, 0),
];

function ensureSubwayStationOverlayLayers(
  map: maplibregl.Map,
  beforeId: string | undefined,
  stationMarkers: SubwayStationMarkerCollections | null | undefined,
) {
  const markers = stationMarkers ?? emptySubwayStationMarkerCollections();

  addOrUpdateGeoJsonSource(map, SUBWAY_STATION_DOTS_SOURCE_ID, markers.dots);
  addOrUpdateGeoJsonSource(
    map,
    SUBWAY_STATION_SHARED_STOPS_SOURCE_ID,
    markers.sharedStops,
  );
  addOrUpdateGeoJsonSource(map, SUBWAY_STATION_LABELS_SOURCE_ID, markers.labels);
  addOrUpdateGeoJsonSource(
    map,
    SUBWAY_STATION_ROUTE_BADGES_SOURCE_ID,
    markers.badges,
  );

  addLayerIfMissing(
    map,
    {
      id: SUBWAY_STATION_SHARED_BAR_CASING_LAYER_ID,
      type: "line",
      source: SUBWAY_STATION_SHARED_STOPS_SOURCE_ID,
      filter: ["==", ["get", "marker_type"], "shared_stop_bar"],
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        // Apple interchange capsule: white pill with a soft gray rim (a
        // near-black rim disappears against the dark basemap and leaves the
        // pill looking like a floating white bar).
        "line-color": "#8b939e",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          11,
          2.8,
          14,
          4.0,
          16,
          4.8,
        ],
        "line-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.4,
          0,
          13.2,
          0.65,
          14.2,
          0.9,
          15.5,
          0.85,
        ],
      },
    },
    beforeId,
  );

  addLayerIfMissing(
    map,
    {
      id: SUBWAY_STATION_SHARED_BAR_FILL_LAYER_ID,
      type: "line",
      source: SUBWAY_STATION_SHARED_STOPS_SOURCE_ID,
      filter: ["==", ["get", "marker_type"], "shared_stop_bar"],
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": "#f4f6f8",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          11,
          1.8,
          14,
          2.8,
          16,
          3.6,
        ],
        "line-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.4,
          0,
          13.2,
          0.75,
          14.2,
          0.96,
          15.5,
          0.9,
        ],
      },
    },
    beforeId,
  );

  addLayerIfMissing(
    map,
    {
      id: SUBWAY_STATION_SINGLE_DOTS_LAYER_ID,
      type: "circle",
      source: SUBWAY_STATION_DOTS_SOURCE_ID,
      paint: {
        "circle-color": ["get", "color"],
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["zoom"],
          11,
          1.1,
          14,
          2.3,
          16,
          3.5,
        ],
        // Apple bead: the rim is a darker shade of the line's own hue (baked
        // as dot_color by the anchors builder), not a neutral black ring.
        "circle-stroke-color": [
          "coalesce",
          ["get", "dot_color"],
          ["get", "color"],
        ],
        "circle-stroke-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.8,
          0.2,
          14,
          0.9,
          16,
          1.25,
        ],
        "circle-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.8,
          0,
          13.6,
          0.7,
          14.5,
          0.95,
          16.5,
          0.9,
        ],
      },
    },
    beforeId,
  );

  addLayerIfMissing(
    map,
    {
      id: SUBWAY_STATION_SHARED_DOTS_LAYER_ID,
      type: "circle",
      source: SUBWAY_STATION_SHARED_STOPS_SOURCE_ID,
      filter: ["==", ["get", "marker_type"], "shared_stop_dot"],
      paint: {
        // Same-color multi-route stop -> the line's color (set in the builder),
        // not a neutral white dot.
        "circle-color": ["get", "color"],
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["zoom"],
          11,
          1.6,
          14,
          2.8,
          16,
          4.2,
        ],
        // Apple bead rim: darker shade of the line's own hue (see builder).
        "circle-stroke-color": [
          "coalesce",
          ["get", "dot_color"],
          ["get", "color"],
        ],
        "circle-stroke-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.8,
          0.25,
          14,
          1.1,
          16,
          1.5,
        ],
        "circle-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.8,
          0,
          13.6,
          0.85,
          15.5,
          0.9,
        ],
      },
    },
    beforeId,
  );

  addLayerIfMissing(
    map,
    {
      id: SUBWAY_STATION_LABELS_LAYER_ID,
      type: "symbol",
      source: SUBWAY_STATION_LABELS_SOURCE_ID,
      minzoom: 14.2,
      layout: {
        "text-field": ["get", "name"],
        "text-size": [
          "interpolate",
          ["linear"],
          ["zoom"],
          14,
          10,
          16,
          12,
          18,
          14,
        ],
        "text-anchor": ["coalesce", ["get", "label_anchor"], "top"],
        "text-offset": ["coalesce", ["get", "label_offset"], ["literal", [0, 1.0]]],
        "symbol-sort-key": ["coalesce", ["get", "marker_priority"], 0],
        "text-allow-overlap": false,
        "text-ignore-placement": false,
      },
      paint: {
        "text-color": "#f4f6f8",
        "text-halo-color": "#111827",
        "text-halo-width": 1.75,
        "text-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          14.1,
          0,
          14.8,
          0.95,
        ],
      },
    },
    beforeId,
  );

  addLayerIfMissing(
    map,
    {
      id: SUBWAY_STATION_ROUTE_BADGES_LAYER_ID,
      type: "symbol",
      source: SUBWAY_STATION_ROUTE_BADGES_SOURCE_ID,
      minzoom: 14.4,
      layout: {
        "icon-image": ["get", "icon_id"],
        "icon-size": [
          "interpolate",
          ["linear"],
          ["zoom"],
          14,
          0.45,
          16,
          0.62,
          18,
          0.74,
        ],
        "icon-offset": ["get", "icon_offset"],
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
        "icon-rotation-alignment": "viewport",
        "symbol-sort-key": ["coalesce", ["get", "marker_priority"], 0],
      },
      paint: {
        "icon-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          14.2,
          0,
          14.8,
          1,
        ],
      },
    },
    beforeId,
  );
}

/**
 * Add (or update) the subway-network polyline + stop-dot layers on the
 * map. Idempotent — safe to call repeatedly. `beforeId` controls
 * z-ordering: pass the basemap's first symbol layer id so labels render
 * above polylines.
 */
export function ensureSubwayNetworkLayers(
  map: maplibregl.Map,
  beforeId: string | undefined,
  lanes: GeoJSON.FeatureCollection,
  stops: GeoJSON.FeatureCollection,
  stationMarkers?: SubwayStationMarkerCollections | null,
): void {
  // --- Sources ---
  if (!map.getSource(SUBWAY_NETWORK_SOURCE_ID)) {
    map.addSource(SUBWAY_NETWORK_SOURCE_ID, {
      type: "geojson",
      data: lanes,
    });
  } else {
    const src = map.getSource(
      SUBWAY_NETWORK_SOURCE_ID,
    ) as maplibregl.GeoJSONSource;
    src.setData(lanes);
  }

  if (!map.getSource(SUBWAY_STOP_SOURCE_ID)) {
    map.addSource(SUBWAY_STOP_SOURCE_ID, {
      type: "geojson",
      data: stops,
    });
  } else {
    const src = map.getSource(
      SUBWAY_STOP_SOURCE_ID,
    ) as maplibregl.GeoJSONSource;
    src.setData(stops);
  }

  // --- Glow layer (subtle colored aura, drawn below casing) ---
  if (!map.getLayer(SUBWAY_GLOW_LAYER_ID)) {
    map.addLayer(
      {
        id: SUBWAY_GLOW_LAYER_ID,
        type: "line",
        source: SUBWAY_NETWORK_SOURCE_ID,
        layout: {
          "line-cap": "round",
          "line-join": "round",
          "line-sort-key": [
            "coalesce",
            ["get", "visual_z_order"],
            0,
          ],
        },
        paint: {
          // A subtle DARK ground-shadow (not a colored aura) lifts the network
          // off the dark 3D basemap and the lighter building extrusions. Kept
          // TIGHT and low-blur on purpose: a wide, soft per-line bloom reads as
          // "vibe-coded neon" and smears into a muddy haze where many lanes
          // converge (Atlantic Av, DeKalb, Lower Manhattan). This hugs the
          // casing -- it only peeks ~0.5-1px beyond it -- so the network gets
          // Apple-style depth without a halo, and dense junctions ground into a
          // crisp soft shadow rather than a rainbow smear.
          "line-color": "#05070D",
          "line-opacity": [
            "interpolate",
            ["linear"],
            ["zoom"],
            10.8,
            0.18,
            12.5,
            0.24,
            14,
            0.22,
            16,
            0.13,
          ],
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            2.0,
            13,
            2.8,
            14,
            3.4,
            17,
            4.8,
          ],
          "line-blur": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            0.35,
            14,
            0.5,
            17,
            0.7,
          ],
          "line-offset": LANE_OFFSET_EXPR,
        },
      },
      beforeId,
    );
  }

  // --- Casing layer (dark outline, drawn below fill) ---
  if (!map.getLayer(SUBWAY_CASING_LAYER_ID)) {
    map.addLayer(
      {
        id: SUBWAY_CASING_LAYER_ID,
        type: "line",
        source: SUBWAY_NETWORK_SOURCE_ID,
        layout: {
          "line-cap": "round",
          "line-join": "round",
        },
        paint: {
          // Casing: dark outline framing the colored fill. The casing sits
          // below ALL fills, so it darkens the inter-lane seam (Apple-style)
          // without ever covering a neighbor's color. Pushed toward true black
          // (a faint blue-black tint to match the palette) so the ~0.8px baked
          // gutter between bundled colors reads as a crisp dark separator and no
          // single bright route swallows its darker neighbours. A touch wider
          // than before so each route is cleanly framed against the basemap;
          // the colored core stays narrower than the casing on every side.
          "line-color": "#04060C",
          "line-opacity": 1.0,
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            1.5,
            13,
            2.0,
            14,
            2.5,
            17,
            3.8,
          ],
          "line-offset": LANE_OFFSET_EXPR,
        },
      },
      beforeId,
    );
  }

  // --- Color fill layer ---
  if (!map.getLayer(SUBWAY_FILL_LAYER_ID)) {
    map.addLayer(
      {
        id: SUBWAY_FILL_LAYER_ID,
        type: "line",
        source: SUBWAY_NETWORK_SOURCE_ID,
        layout: {
          "line-cap": "round",
          "line-join": "round",
          // Paint order for the COLORED fill only (visual, not geometry).
          //
          // Paint order follows the baked lane position, not route color. A
          // previous override hard-coded green above red/yellow/orange, which
          // made green visually dominate at borough zoom whenever neighboring
          // lanes compressed toward the same screen pixels. Semantic slot order
          // keeps the top stroke tied to geometry side. cross_color_segment_side
          // is the fallback for segment-level cross-color spread (v2), which
          // can't set a whole-feature lane_slot_semantic (see the property's
          // comment in subway-network.ts's buildSubwayLaneFeaturesFromVisual);
          // without it, those features all sort as 0 and collapse to a
          // color-rank tiebreak that can paint one line over its neighbor
          // wherever their baked offset runs thin.
          // visual_z_order stays the tiny last-resort tiebreak for exact ties.
          "line-sort-key": [
            "+",
            [
              "*",
              ["coalesce", ["get", "lane_slot_semantic"], ["get", "lane_slot"], 0],
              10,
            ],
            ["*", ["coalesce", ["get", "cross_color_segment_side"], 0], 1],
            ["*", ["coalesce", ["get", "visual_z_order"], 0], 0.01],
          ],
        },
        paint: {
          // Fill: bright Apple-like colored stroke inside the casing. Widths
          // are paired with LANE_OFFSET_EXPR so bundled colors keep a visible
          // dark gutter at low/mid zoom; bumping one without the other
          // re-smears bundles.
          // On-dark display palette: the baked geojson keeps the exact MTA hex
          // (bullets + the palette check read that, untouched), but the MAP FILL
          // lifts the dark/cool hues to roughly equal perceptual weight on the
          // dark ground -- so no single bright route (red, orange) overwhelms a
          // bundle and its darker neighbours (navy A/C/E, forest 4/5/6, brown
          // J/Z) hold their own. Unmapped colors fall through to the baked value.
          "line-color": [
            "match",
            ["get", "color"],
            "#0078C6", "#3DA0E8",
            "#996633", "#C68A4E",
            "#0A84FF", "#4DA3FF",
            "#00933C", "#24A85B",
            "#B933AD", "#D45FC9",
            "#EE352E", "#FF5247",
            "#808183", "#A2A4A7",
            "#FF6319", "#FF7A33",
            "#6CBE45", "#70BC4F",
            "#A7A9AC", "#B3B5B8",
            "#FCCC0A", "#FCCC0A",
            ["get", "color"],
          ],
          "line-opacity": 1.0,
          // Apple-weight bands. Keep z11-z14 lean enough that adjacent bundled
          // colors stay readable before the baked meter offset becomes visually
          // large. Close zoom can carry more weight because the lanes have
          // already separated on screen.
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            0.9,
            13,
            1.35,
            14,
            1.7,
            17,
            2.7,
          ],
          "line-offset": LANE_OFFSET_EXPR,
        },
      },
      beforeId,
    );
  }

  // --- Highlight layer (premium inner sheen, drawn above the color fill) ---
  if (!map.getLayer(SUBWAY_HIGHLIGHT_LAYER_ID)) {
    map.addLayer(
      {
        id: SUBWAY_HIGHLIGHT_LAYER_ID,
        type: "line",
        source: SUBWAY_NETWORK_SOURCE_ID,
        layout: {
          "line-cap": "round",
          "line-join": "round",
          "line-sort-key": ["coalesce", ["get", "visual_z_order"], 0],
        },
        paint: {
          // A hairline cool-white sheen down each lane's spine, faded in only at
          // close zoom (z13.5+) where bands are wide enough to carry it. Very
          // low opacity so it reads as a faint LIT ribbon, never gloss/neon.
          // Because it adds luminance it lifts the darker route colors (navy
          // A/C/E, forest G, the dim purple/brown) so a bright neighbour
          // (orange, yellow, red) stops perceptually dominating the bundle.
          // Narrow + centered (same LANE_OFFSET_EXPR) so it never reaches the
          // inter-lane gutter and cannot smear bundles.
          "line-color": "#EAF1FF",
          "line-opacity": [
            "interpolate",
            ["linear"],
            ["zoom"],
            13.0,
            0.0,
            14.0,
            0.04,
            16.0,
            0.055,
            17.0,
            0.06,
          ],
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            13,
            0.5,
            14,
            0.8,
            17,
            1.25,
          ],
          "line-blur": 0.25,
          "line-offset": LANE_OFFSET_EXPR,
        },
      },
      beforeId,
    );
  }

  ensureSubwayStationOverlayLayers(map, beforeId, stationMarkers);

  // --- Stop dot layer ---
  if (!map.getLayer(SUBWAY_STOP_DOT_LAYER_ID)) {
    map.addLayer(
      {
        id: SUBWAY_STOP_DOT_LAYER_ID,
        type: "circle",
        source: SUBWAY_STOP_SOURCE_ID,
        paint: {
          "circle-color": ["get", "color"],
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            1.4,
            14,
            2.6,
            17,
            4.0,
          ],
          "circle-stroke-color": "#070A12",
          "circle-stroke-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            0.4,
            14,
            0.8,
            17,
            1.2,
          ],
          "circle-opacity": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            0.0,
            12.5,
            1.0,
          ],
        },
      },
      beforeId,
    );
  }
}

// =====================================================================
// Focus mode: hide the ambient network while a planned route is active
// =====================================================================
// The picked route (true polyline + its stops) is the hero; rendering the
// citywide network underneath it overwhelmed the display, so the whole
// ambient stack is toggled off via layout visibility (also skips its
// render cost entirely) and restored on clear.

const SUBWAY_AMBIENT_LAYER_IDS = [
  SUBWAY_GLOW_LAYER_ID,
  SUBWAY_CASING_LAYER_ID,
  SUBWAY_FILL_LAYER_ID,
  SUBWAY_HIGHLIGHT_LAYER_ID,
  SUBWAY_STOP_DOT_LAYER_ID,
  SUBWAY_STATION_SINGLE_DOTS_LAYER_ID,
  SUBWAY_STATION_SHARED_DOTS_LAYER_ID,
  SUBWAY_STATION_SHARED_BAR_CASING_LAYER_ID,
  SUBWAY_STATION_SHARED_BAR_FILL_LAYER_ID,
  SUBWAY_STATION_LABELS_LAYER_ID,
  SUBWAY_STATION_ROUTE_BADGES_LAYER_ID,
];

export function setSubwayNetworkHidden(map: maplibregl.Map, hidden: boolean) {
  const visibility = hidden ? "none" : "visible";
  for (const layerId of SUBWAY_AMBIENT_LAYER_IDS) {
    if (!map.getLayer(layerId)) continue;
    if (map.getLayoutProperty(layerId, "visibility") !== visibility) {
      map.setLayoutProperty(layerId, "visibility", visibility);
    }
  }
}
