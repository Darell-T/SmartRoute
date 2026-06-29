export type ValidationReportingPaths = {
  corridorsGeoJson: string;
  corridorsJson: string;
  junctionAnchorsGeoJson: string;
  junctionSnapsGeoJson: string;
  materializedBundlesGeoJson: string;
  materializedBundleFanoutsGeoJson: string;
  materializedBundleSplitsGeoJson: string;
  materializedBundleDefectsGeoJson: string;
  bundlesGeoJson: string;
  bundleLanesGeoJson: string;
  bundleGapsGeoJson: string;
  missingRouteLanesGeoJson: string;
  renderLaneContinuityJson: string;
  anomaliesGeoJson: string;
  anomaliesJson: string;
  routeComponentsJson: string;
};

export type ValidationReportingParameters = {
  resampleIntervalM: number;
  hausdorffMaxM: number;
  overlapMinRatio: number;
  tangentMaxDiffDeg: number;
  containmentAvgDistanceMaxM: number;
  containmentOverlapMinRatio: number;
  gridCellM: number;
  junctionSnapMaxM: number;
  maxSegmentAnomalyM: number;
  sparseLongSliceM: number;
  projectionAnomalyM: number;
  openDataMinFragmentLengthM: number;
};

export type RouteConnectivityStat = {
  route_id: string;
  edge_count: number;
  stop_count: number;
  component_count: number;
  largest_component_size: number;
  largest_component_ratio: number;
  components: Array<{ size: number; sample_stop_ids: any[] }>;
  passed: boolean;
};

export type RouteConnectivityFailure = {
  route_id: string;
  component_count: number;
  largest_component_ratio: number;
  total_stops: number;
  largest_size: number;
  sample_component_sizes: number[];
};

export type ValidationReportingStageResult = {
  perRouteStats: RouteConnectivityStat[];
  validationFailures: RouteConnectivityFailure[];
};
