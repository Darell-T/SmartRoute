import type { Position } from "./types.ts";

// GeoJSON-feature input for buildSpineFromCorridor (Stage D).
type CorridorFeatureProperties = {
  corridor_id?: string;
  base_geometry_selection?: string;
  route_ids?: string[];
  source_edge_ids?: string[];
  "source_shape_ids"?: string[];
  length_m?: number;
};

type CorridorFeatureInput = {
  geometry?: { type?: string; coordinates?: Position[] } | null;
  properties: CorridorFeatureProperties;
};

export function computeBaseSpineHash(coords: Position[]): string {
  let h = 5381;
  for (const [lon, lat] of coords) {
    const s = `${lon.toFixed(6)},${lat.toFixed(6)};`;
    for (let i = 0; i < s.length; i++) {
      h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    }
  }
  return `h${(h >>> 0).toString(36)}`;
}

export function buildSpineFromCorridor(corridorFeature: CorridorFeatureInput) {
  const geometry = corridorFeature?.geometry;
  const coords = geometry?.coordinates;
  if (!coords) {
    throw new Error(`buildSpineFromCorridor: corridorFeature.geometry.coordinates is required (corridor_id=${corridorFeature?.properties?.corridor_id ?? "<unknown>"})`);
  }
  const p = corridorFeature.properties;
  const corridor_id = p.corridor_id;
  return {
    spine_id: `spine-${corridor_id}`,
    base_corridor_id: corridor_id,
    method: p.base_geometry_selection ?? "quality_density_length",
    geometry,
    base_spine_hash: computeBaseSpineHash(coords),
    route_ids: p.route_ids ?? [],
    source_edge_ids: p.source_edge_ids ?? [],
    "source_shape_ids": p["source_shape_ids"] ?? [],
    length_m: p.length_m ?? 0,
  };
}
