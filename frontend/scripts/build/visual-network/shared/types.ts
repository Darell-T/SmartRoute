import type {
  FeatureProps,
  LineStringGeometry,
  PointGeometry,
  Position as BasePosition,
} from "../../types.ts";

export type { VisualFeatureProperties } from "../../types.ts";

export type Position = BasePosition;

export type { FeatureProps };

export type LineFeature<Properties extends object = FeatureProps> = {
  type: "Feature";
  id?: string | number;
  geometry: LineStringGeometry;
  properties: Properties;
};

export type PointFeat<Properties extends object = FeatureProps> = {
  type: "Feature";
  id?: string | number;
  geometry: PointGeometry;
  properties: Properties;
};

export type BundleSpineRef = {
  spine_id?: string | null;
  base_spine_hash?: string | null;
  method?: string | null;
};

export type BundleArtifacts<
  LineProperties extends object = FeatureProps,
  PointProperties extends object = FeatureProps,
> = {
  bundleFeatures: LineFeature<LineProperties>[];
  bundleLaneFeatures: LineFeature<LineProperties>[];
  bundleGapFeatures: PointFeat<PointProperties>[];
  visualFeatures: LineFeature<LineProperties>[];
};
