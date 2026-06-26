// frontend/scripts/build/types.ts
// Shared geometry + GeoJSON shapes for the transit build scripts. These were
// re-declared (identically) across smooth-polyline.ts, dedupe-duplicate-
// corridors.ts and station-anchors/index.ts; consolidating them here gives the
// build helpers a single source of truth. Pure type module -- no runtime code,
// so `import type` keeps it erased under `node --experimental-strip-types`.

// A [lon, lat] pair. Some helpers spell it "Position", others "Coordinate";
// both names point at the same tuple.
export type Position = [number, number];
export type Coordinate = Position;

export type PointGeometry = {
  type: "Point";
  coordinates: Position;
};

export type LineStringGeometry = {
  type: "LineString";
  coordinates: Position[];
};

export type AnyGeometry = PointGeometry | LineStringGeometry | Record<string, any>;

export type Feature<
  G = AnyGeometry,
  P extends Record<string, any> = Record<string, any>,
> = {
  type: "Feature";
  id?: string | number;
  geometry: G;
  properties: P;
};

export type FeatureCollection<F extends Feature = Feature> = {
  type: "FeatureCollection";
  features: F[];
  metadata?: Record<string, any>;
};

// An MTA route designator -- "A", "7", "GS", "SI", etc. A nominal alias for
// readability where a bare string is really a route id.
export type RouteId = string;

// [minLon, minLat, maxLon, maxLat].
export type BBox = [number, number, number, number];

// Properties carried by the baked subway visual-network LineString features.
// Permissive (index signature) because the pipeline attaches stage-specific
// debug fields; the listed members are the ones the renderer actually reads.
export type VisualFeatureProperties = {
  route_ids?: RouteId[];
  color?: string;
  corridor_id?: string;
  lane_slot_semantic?: number;
  visual_z_order?: number;
  [key: string]: unknown;
};
