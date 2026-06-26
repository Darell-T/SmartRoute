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

export type StationFeature = Feature<PointGeometry, Record<string, any>>;

export type StationBuildResult = {
  anchors: FeatureCollection;
  raw: FeatureCollection;
  snaps: FeatureCollection;
  rejected: FeatureCollection;
  ambiguous: FeatureCollection;
};
