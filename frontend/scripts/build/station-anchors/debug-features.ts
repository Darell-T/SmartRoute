// frontend/scripts/build/station-anchors/debug-features.ts
// Builders for the station-anchor DEBUG GeoJSON collections (raw / snaps /
// rejected / ambiguous). buildStationAnchors emits these alongside the real
// anchors; they are written to artifacts/debug for visual QA and never shipped
// to the runtime map. Pure constructors -- each only reads the station and
// projection it is handed (callers pass pre-sorted route ids), so this module
// has no value dependency back on index.ts.

import {
  STATION_DEBUG_MARKERS,
  type AmbiguousDebugFeature,
  type Projection,
  type RawStationDebugFeature,
  type RejectedDebugFeature,
  type SnapDebugFeature,
  type StationFeature,
} from "./types.ts";

// Raw station point: the station's own GeoJSON coordinate, before any snapping.
// Lets you eyeball input stations against the snapped anchors on the same map.
export function rawStationDebugFeature(
  station: StationFeature,
  routeIds: string[],
): RawStationDebugFeature {
  return {
    type: "Feature",
    properties: {
      marker_type: STATION_DEBUG_MARKERS.raw,
      station_id: String(
        station.properties?.station_id ?? station.id ?? "",
      ),
      name: String(station.properties?.name ?? ""),
      route_ids: routeIds,
    },
    geometry: station.geometry,
  };
}

// Snap line: a segment from the station to where one route's lane was snapped.
// Shows which visual lane each route attached to and how far the snap reached.
export function snapDebugFeature(
  station: StationFeature,
  projection: Projection,
): SnapDebugFeature {
  return {
    type: "Feature",
    properties: {
      marker_type: STATION_DEBUG_MARKERS.snap,
      station_id: String(
        station.properties?.station_id ?? station.id ?? "",
      ),
      name: String(station.properties?.name ?? ""),
      route_id: projection.routeId,
      snapped_visual_feature_id: projection.visualFeature.id,
      snap_distance_m: projection.distance_m,
      tangent_bearing: projection.tangent_bearing,
    },
    geometry: {
      type: "LineString",
      coordinates: [station.geometry.coordinates, projection.coordinate],
    },
  };
}

// Rejected-snap line: a candidate snap that exceeded the distance threshold.
// Surfaces the near-misses that were discarded, so an over-tight gate is visible.
export function rejectedDebugFeature(
  station: StationFeature,
  projection: Projection,
): RejectedDebugFeature {
  return {
    type: "Feature",
    properties: {
      marker_type: STATION_DEBUG_MARKERS.rejected,
      station_id: String(
        station.properties?.station_id ?? station.id ?? "",
      ),
      name: String(station.properties?.name ?? ""),
      route_id: projection.routeId,
      visual_feature_id: projection.visualFeature.id,
      snap_distance_m: projection.distance_m,
      reason: "snap_distance_above_threshold",
    },
    geometry: {
      type: "LineString",
      coordinates: [station.geometry.coordinates, projection.coordinate],
    },
  };
}

// Ambiguous station marker: a station that produced no valid projection at all.
// Flags stations the snapper could not resolve, with candidate counts for triage.
export type AmbiguousDebugExtra = {
  debug_candidate_count?: number;
  debug_rejected_candidate_count?: number;
};

export function ambiguousDebugFeature(
  station: StationFeature,
  routeIds: string[],
  reason: string,
  extra: AmbiguousDebugExtra = {},
): AmbiguousDebugFeature {
  return {
    type: "Feature",
    properties: {
      marker_type: STATION_DEBUG_MARKERS.ambiguous,
      station_id: String(
        station.properties?.station_id ?? station.id ?? "",
      ),
      name: String(station.properties?.name ?? ""),
      route_ids: routeIds,
      reason,
      ...extra,
    },
    geometry: station.geometry,
  };
}
