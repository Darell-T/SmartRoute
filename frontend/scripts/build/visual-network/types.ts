import type { LineStringGeometry, PointGeometry, Position as BasePosition } from "../types.ts";

export type Position = BasePosition;

export type FeatureProps = Record<string, any>;

export type LineFeature = {
  type: "Feature";
  id?: string | number;
  geometry: LineStringGeometry;
  properties: FeatureProps;
};

export type PointFeat = {
  type: "Feature";
  id?: string | number;
  geometry: PointGeometry;
  properties: FeatureProps;
};
