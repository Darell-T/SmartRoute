// frontend/scripts/build/types.ts
// Shared geometry + GeoJSON shapes for the transit build scripts. These were
// re-declared (identically) across smooth-polyline.ts, dedupe-duplicate-
// corridors.ts and station-anchors/index.ts; consolidating them here gives the
// build helpers a single source of truth. Pure type module -- no runtime code,
// so `import type` keeps it erased under `node --experimental-strip-types`.

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

export type AnyGeometry = PointGeometry | LineStringGeometry;

// Raw GeoJSON property bags are JSON objects. Stages that read named fields
// still declare their own property contracts; this is the unparsed default.
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | JsonObject;

export type JsonObject = { [key: string]: JsonValue | undefined };

export type FeatureProps = JsonObject;

export type Feature<G = AnyGeometry, P = FeatureProps> = {
  type: "Feature";
  id?: string | number;
  geometry: G;
  properties: P;
};

export type FeatureCollection<
  F extends Feature<unknown, unknown> = Feature,
  Metadata extends object = FeatureProps,
> = {
  type: "FeatureCollection";
  features: F[];
  metadata?: Metadata;
};

export type RouteId = string;

export type BBox = [number, number, number, number];

export type VisualFeatureProperties = {
  route_ids?: RouteId[] | string;
  color?: string;
  corridor_id?: string | null;
  bundle_id?: string | null;
  route_id?: string;
  lane_slot_semantic?: number;
  visual_z_order?: number;
};
