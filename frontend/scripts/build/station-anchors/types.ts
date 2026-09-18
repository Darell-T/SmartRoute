// frontend/scripts/build/station-anchors/types.ts
// Station-anchors domain types: the internal data shapes of the anchoring
// algorithm -- meter-space points, route-to-lane projections, projection
// clusters, and the build result. These are module-local on purpose: only
// station-anchors uses them, so they live here rather than polluting the shared
// ../types.ts (which holds the cross-script GeoJSON primitives they build on).

import type {
  Position,
  Feature,
  LineStringGeometry,
  PointGeometry,
  FeatureCollection,
} from "../types.ts";

export const STATION_DEBUG_MARKERS = {
  raw: "raw_station_point",
  snap: "station_snap",
  rejected: "station_snap_rejected",
  ambiguous: "station_snap_ambiguous",
} as const;

export type MeterPoint = {
  x: number;
  y: number;
};

export type ProjectionBase = {
  coordinate: Position;
  distance_m: number;
  segment_index: number;
  segment_t: number;
  tangent_bearing: number;
};

export type VisualFeature = {
  feature: Feature<LineStringGeometry>;
  index: number;
  id: string;
  coordinates: Position[];
  routeIds: string[];
  colorRouteIds: string[];
  allRouteIds: string[];
  color: string | null;
  corridorId: string | null;
  physicalBundleId: string | null;
};

export type Projection = ProjectionBase & {
  routeId: string;
  visualFeature: VisualFeature;
  score: number;
};

export type ProjectionCluster = {
  projections: Projection[];
  centroid?: Position;
};

export type StationProperties = {
  station_id?: string;
  name?: string;
  route_ids?: string[];
};

export type StationFeature = Feature<PointGeometry, StationProperties>;

export type StationAnchorMarker =
  | "single_stop_dot"
  | "shared_stop_dot"
  | "shared_stop_bar"
  | "station_label"
  | "station_route_badge";

export type StationAnchorProperties = {
  marker_type: StationAnchorMarker;
  station_id: string;
  name: string;
  route_ids: string[];
  route_count: number;
  source_coordinate: Position;
  snapped_coordinate: Position | undefined;
  snapped_visual_feature_ids: string[];
  snapped_route_ids: string[];
  snapped_color_route_ids: string[];
  visual_corridor_id: string | undefined;
  physical_bundle_id: string | undefined;
  local_tangent_bearing: number;
  local_normal_bearing: number;
  snap_distance_m: number;
  snap_confidence: string;
  marker_priority: number;
  min_zoom: number;
  max_zoom: number | undefined;
  debug_candidate_count?: number;
  debug_rejected_candidate_count?: number;
  debug_cluster_id?: string;
  color?: string;
  dot_color?: string;
  label_anchor?: string;
  label_offset?: [number, number];
  route_id?: string;
  badge_index?: number;
  badge_count?: number;
  badge_row?: number;
  badge_col?: number;
  icon_id?: string;
  icon_offset?: [number, number];
};

export type StationAnchorFeature = Feature<
  PointGeometry | LineStringGeometry,
  StationAnchorProperties
>;

export type RawStationDebugProperties = {
  marker_type: typeof STATION_DEBUG_MARKERS.raw;
  station_id: string;
  name: string;
  route_ids: string[];
};

export type RawStationDebugFeature = Feature<
  PointGeometry,
  RawStationDebugProperties
>;

export type SnapDebugProperties = {
  marker_type: typeof STATION_DEBUG_MARKERS.snap;
  station_id: string;
  name: string;
  route_id: string;
  snapped_visual_feature_id: string;
  snap_distance_m: number;
  tangent_bearing: number;
};

export type SnapDebugFeature = Feature<LineStringGeometry, SnapDebugProperties>;

export type RejectedDebugProperties = {
  marker_type: typeof STATION_DEBUG_MARKERS.rejected;
  station_id: string;
  name: string;
  route_id: string;
  visual_feature_id: string;
  snap_distance_m: number;
  reason: "snap_distance_above_threshold";
};

export type RejectedDebugFeature = Feature<
  LineStringGeometry,
  RejectedDebugProperties
>;

export type AmbiguousDebugProperties = {
  marker_type: typeof STATION_DEBUG_MARKERS.ambiguous;
  station_id: string;
  name: string;
  route_ids: string[];
  reason: string;
  debug_candidate_count?: number;
  debug_rejected_candidate_count?: number;
};

export type AmbiguousDebugFeature = Feature<
  PointGeometry,
  AmbiguousDebugProperties
>;

export type StationAnchorMetadata = {
  max_snap_distance_m: number;
  visual_generated_at: string | null;
  visual_geometry_source: string | null;
  visual_geometry_source_dataset_id: string | null;
  visual_feature_count: number;
  station_count: number;
  anchor_feature_count: number;
};

export type StationBuildResult = {
  anchors: FeatureCollection<StationAnchorFeature, StationAnchorMetadata>;
  raw: FeatureCollection<RawStationDebugFeature>;
  snaps: FeatureCollection<SnapDebugFeature>;
  rejected: FeatureCollection<RejectedDebugFeature>;
  ambiguous: FeatureCollection<AmbiguousDebugFeature>;
  metadata: StationAnchorMetadata;
};
