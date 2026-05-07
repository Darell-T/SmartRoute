export const ENABLE_SUBWAY_LANE_SEPARATION = true;
export type SubwayLaneRenderMode =
  | "canonical"
  | "family-visual"
  | "group-corridors"
  | "schematic-family-pilot"
  | "visual-no-lanes"
  | "global-lanes"
  | "corridor-lanes-4b"
  | "junction-transitions-4c"
  | "visual"
  | "lanes"
  | "junctions";
type ResolvedSubwayLaneRenderMode = Exclude<
  SubwayLaneRenderMode,
  "visual" | "lanes" | "junctions"
>;

type SubwayLaneProperties = GeoJSON.GeoJsonProperties & {
  route_id?: unknown;
  display_route?: unknown;
  visual_lane_slot?: number;
  visual_z_order?: number;
  visual_family?: string;
  corridor_id?: string | null;
  corridor_override?: boolean;
  render_segment_index?: number;
  render_source_key?: string;
  segment_kind?:
    | "corridor"
    | "fallback"
    | "junction-in"
    | "junction-out"
    | "junction-switch";
  transition_length_meters?: number;
};

type LanePrepOptions = {
  enabled?: boolean;
  mode?: SubwayLaneRenderMode;
  junctionTransitionsEnabled?: boolean;
};

type CorridorBounds = {
  minLng: number;
  minLat: number;
  maxLng: number;
  maxLat: number;
};

export type ManualCorridorOverride = {
  corridorId: string;
  routeIds: string[];
  bounds: CorridorBounds;
  laneOrder: string[];
  laneSlots: Record<string, number>;
  zOrderBase: number;
  transitionLengthMeters: number;
};

const ROUTE_LANE_SLOTS: Record<string, number> = {
  "1": -1,
  "2": 0,
  "3": 1,
  "4": -1.5,
  "5": -0.5,
  "6": 0.5,
  "6X": 1.5,
  "7": -0.5,
  "7X": 0.5,
  A: -1,
  C: 0,
  E: 1,
  B: -1.5,
  D: -0.5,
  F: 0.5,
  M: 1.5,
  N: -1.5,
  Q: -0.5,
  R: 0.5,
  W: 1.5,
  J: -0.5,
  Z: 0.5,
};

const ROUTE_Z_ORDER: Record<string, number> = {
  S: 10,
  SI: 20,
  L: 30,
  G: 40,
  J: 50,
  Z: 60,
  A: 70,
  C: 80,
  E: 90,
  F: 100,
  M: 110,
  B: 120,
  D: 130,
  N: 140,
  Q: 150,
  R: 160,
  W: 170,
  "1": 180,
  "4": 190,
  "5": 200,
  "6": 210,
  "6X": 220,
  "2": 230,
  "3": 240,
  "7": 250,
  "7X": 260,
};

const SUBWAY_LANE_RENDER_MODES = new Set<ResolvedSubwayLaneRenderMode>([
  "canonical",
  "family-visual",
  "group-corridors",
  "schematic-family-pilot",
  "visual-no-lanes",
  "global-lanes",
  "corridor-lanes-4b",
  "junction-transitions-4c",
]);
const MIN_TRANSITION_SEGMENT_METERS = 12;
const MIN_RENDER_SEGMENT_METERS = 1;

const FALLBACK_VARIANT_DEDUPE_BOUNDS: Array<{
  id: string;
  bounds: CorridorBounds;
}> = [
  {
    id: "lower-east-river",
    bounds: {
      minLng: -74.03,
      minLat: 40.68,
      maxLng: -73.965,
      maxLat: 40.72,
    },
  },
  {
    id: "canal-manhattan-bridge",
    bounds: {
      minLng: -74.02,
      minLat: 40.705,
      maxLng: -73.975,
      maxLat: 40.735,
    },
  },
  {
    id: "coney-west-8",
    bounds: {
      minLng: -74.02,
      minLat: 40.565,
      maxLng: -73.94,
      maxLat: 40.595,
    },
  },
  {
    id: "central-park-63st",
    bounds: {
      minLng: -73.99,
      minLat: 40.755,
      maxLng: -73.94,
      maxLat: 40.775,
    },
  },
];

// Phase 4B manual corridor overrides. Single source of truth lives in
// `subway-corridor-overrides.json` so the .mjs build script
// (`frontend/scripts/build-corridor-groups.mjs`) can read the same data
// without TS compilation. Canonical geometry remains the only trusted
// source for train snapping, pulses, route indexing, and route planning.
import manualCorridorOverridesJson from "./subway-corridor-overrides.json";

export const MANUAL_CORRIDOR_OVERRIDES: ManualCorridorOverride[] =
  manualCorridorOverridesJson as ManualCorridorOverride[];

export const SUBWAY_ROUTE_FAMILY: Record<string, string> = {
  "1": "1-2-3",
  "2": "1-2-3",
  "3": "1-2-3",
  "4": "4-5-6",
  "5": "4-5-6",
  "6": "4-5-6",
  "6X": "4-5-6",
  "7": "7",
  "7X": "7",
  A: "A-C-E",
  C: "A-C-E",
  E: "A-C-E",
  B: "B-D-F-M",
  D: "B-D-F-M",
  F: "B-D-F-M",
  M: "B-D-F-M",
  N: "N-Q-R-W",
  Q: "N-Q-R-W",
  R: "N-Q-R-W",
  W: "N-Q-R-W",
  J: "J-Z",
  Z: "J-Z",
};

function devWarn(message: string, detail?: unknown) {
  if (typeof process !== "undefined" && process.env.NODE_ENV !== "production") {
    console.warn(message, detail);
  }
}

export function resolveSubwayLaneRenderMode(
  mode?: SubwayLaneRenderMode,
): ResolvedSubwayLaneRenderMode {
  // Debug/stability modes:
  // - canonical / visual-no-lanes: leave the render source unseparated.
  // - family-visual: prebuilt continuous route-family geometry, no runtime lane prep.
  // - group-corridors: prebuilt service-group corridor metadata, no runtime bbox prep.
  // - schematic-family-pilot: prepositioned visual-only pilot topology, no runtime lane prep.
  // - global-lanes: old route-family offsets for diagnostics only.
  // - corridor-lanes-4b: stable corridor-only lane separation.
  // - junction-transitions-4c: explicit Phase 4C experiment.
  const envMode =
    typeof process !== "undefined"
      ? (process.env.NEXT_PUBLIC_NETWORK_RENDER_MODE ??
        process.env.NEXT_PUBLIC_SUBWAY_LANE_RENDER_MODE)
      : undefined;
  const candidate = mode ?? envMode;

  if (candidate === "visual") return "visual-no-lanes";
  if (candidate === "lanes") return "corridor-lanes-4b";
  if (candidate === "junctions") return "junction-transitions-4c";

  if (
    candidate &&
    SUBWAY_LANE_RENDER_MODES.has(candidate as ResolvedSubwayLaneRenderMode)
  ) {
    return candidate as ResolvedSubwayLaneRenderMode;
  }

  if (
    typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_SUBWAY_JUNCTION_TRANSITIONS === "true"
  ) {
    return "junction-transitions-4c";
  }

  return ENABLE_SUBWAY_LANE_SEPARATION ? "family-visual" : "visual-no-lanes";
}

export function normalizeSubwayRouteId(value: unknown) {
  const routeId = String(value ?? "")
    .trim()
    .toUpperCase();

  if (routeId === "6D") return "6X";
  if (routeId === "7D") return "7X";
  if (routeId === "FD" || routeId === "FX") return "F";
  if (routeId === "FS" || routeId === "GS" || routeId === "H") return "S";
  if (routeId === "SIR") return "SI";

  return routeId;
}

function cloneFeatureCollection(
  data: GeoJSON.FeatureCollection,
): GeoJSON.FeatureCollection {
  if (typeof structuredClone === "function") {
    return structuredClone(data);
  }

  return JSON.parse(JSON.stringify(data)) as GeoJSON.FeatureCollection;
}

function stripLaneMetadata(
  data: GeoJSON.FeatureCollection,
): GeoJSON.FeatureCollection {
  const renderData = cloneFeatureCollection(data);

  renderData.features = renderData.features.map((feature) => {
    const properties = {
      ...(feature.properties ?? {}),
    } as SubwayLaneProperties;
    delete properties.visual_lane_slot;
    delete properties.visual_z_order;
    delete properties.visual_family;
    delete properties.corridor_id;
    delete properties.corridor_override;
    delete properties.render_segment_index;
    delete properties.render_source_key;
    delete properties.segment_kind;
    delete properties.transition_length_meters;

    return {
      ...feature,
      properties,
    };
  });

  return renderData;
}

function isValidCoordinate(value: unknown): value is GeoJSON.Position {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  );
}

function validCoordinates(coordinates: unknown): GeoJSON.Position[] {
  if (!Array.isArray(coordinates)) return [];
  return coordinates.filter(isValidCoordinate);
}

function lineDistance(coordinates: GeoJSON.Position[]) {
  let distance = 0;

  for (let index = 1; index < coordinates.length; index += 1) {
    const [previousLng, previousLat] = coordinates[index - 1];
    const [lng, lat] = coordinates[index];
    const dx = lng - previousLng;
    const dy = lat - previousLat;
    distance += Math.sqrt(dx * dx + dy * dy);
  }

  return distance;
}

function metersBetween(start: GeoJSON.Position, end: GeoJSON.Position) {
  const [startLng, startLat] = start;
  const [endLng, endLat] = end;
  const metersPerDegreeLat = 111_320;
  const averageLat = ((startLat + endLat) / 2) * (Math.PI / 180);
  const metersPerDegreeLng = metersPerDegreeLat * Math.cos(averageLat);
  const dx = (endLng - startLng) * metersPerDegreeLng;
  const dy = (endLat - startLat) * metersPerDegreeLat;
  return Math.sqrt(dx * dx + dy * dy);
}

function coordinatesLengthMeters(coordinates: GeoJSON.Position[]) {
  let length = 0;

  for (let index = 1; index < coordinates.length; index += 1) {
    length += metersBetween(coordinates[index - 1], coordinates[index]);
  }

  return length;
}

function hasRenderableLine(coordinates: GeoJSON.Position[]) {
  return (
    coordinates.length >= 2 &&
    coordinatesLengthMeters(coordinates) >= MIN_RENDER_SEGMENT_METERS
  );
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function interpolateCoordinate(
  start: GeoJSON.Position,
  end: GeoJSON.Position,
  t: number,
): GeoJSON.Position {
  return [
    start[0] + (end[0] - start[0]) * t,
    start[1] + (end[1] - start[1]) * t,
  ];
}

function coordinatesEqual(left: GeoJSON.Position, right: GeoJSON.Position) {
  return (
    Math.abs(left[0] - right[0]) < 1e-10 && Math.abs(left[1] - right[1]) < 1e-10
  );
}

function pushUniqueCoordinate(
  coordinates: GeoJSON.Position[],
  coordinate: GeoJSON.Position,
) {
  const last = coordinates[coordinates.length - 1];
  if (!last || !coordinatesEqual(last, coordinate)) {
    coordinates.push(coordinate);
  }
}

function longestValidLinePart(
  geometry: GeoJSON.Geometry | null | undefined,
): GeoJSON.Position[] | null {
  if (!geometry) return null;

  if (geometry.type === "LineString") {
    const coordinates = validCoordinates(geometry.coordinates);
    return coordinates.length >= 2 ? coordinates : null;
  }

  if (geometry.type !== "MultiLineString") return null;

  let longest: GeoJSON.Position[] | null = null;
  let longestDistance = -1;

  for (const part of geometry.coordinates) {
    const coordinates = validCoordinates(part);
    if (coordinates.length < 2) continue;

    const distance = lineDistance(coordinates);
    if (
      distance > longestDistance ||
      (distance === longestDistance &&
        coordinates.length > (longest?.length ?? 0))
    ) {
      longest = coordinates;
      longestDistance = distance;
    }
  }

  return longest;
}

function directionMultiplier(
  geometry: GeoJSON.Geometry | null | undefined,
  routeId: string,
) {
  const line = longestValidLinePart(geometry);

  if (!line) {
    devWarn("[subway-lane-separation] invalid route geometry", { routeId });
    return 1;
  }

  const first = line[0];
  const last = line[line.length - 1];
  const dx = last[0] - first[0];
  const dy = last[1] - first[1];

  if (Math.abs(dx) < Number.EPSILON && Math.abs(dy) < Number.EPSILON) {
    return 1;
  }

  if (Math.abs(dy) >= Math.abs(dx)) {
    return dy >= 0 ? 1 : -1;
  }

  return dx >= 0 ? 1 : -1;
}

function routeMatchesOverride(
  routeId: string,
  override: ManualCorridorOverride,
) {
  return override.routeIds.some(
    (overrideRouteId) => normalizeSubwayRouteId(overrideRouteId) === routeId,
  );
}

function coordinateInsideBounds(
  coordinate: GeoJSON.Position,
  bounds: CorridorBounds,
) {
  const [lng, lat] = coordinate;
  return (
    lng >= bounds.minLng &&
    lng <= bounds.maxLng &&
    lat >= bounds.minLat &&
    lat <= bounds.maxLat
  );
}

function lineIntersectsBounds(
  coordinates: GeoJSON.Position[],
  bounds: CorridorBounds,
) {
  if (
    coordinates.some((coordinate) => coordinateInsideBounds(coordinate, bounds))
  ) {
    return true;
  }

  for (let index = 1; index < coordinates.length; index += 1) {
    if (
      lineBoundsInterval(coordinates[index - 1], coordinates[index], bounds)
    ) {
      return true;
    }
  }

  return false;
}

function featureIntersectsBounds(
  feature: GeoJSON.Feature,
  bounds: CorridorBounds,
) {
  if (feature.geometry?.type === "LineString") {
    return lineIntersectsBounds(
      validCoordinates(feature.geometry.coordinates),
      bounds,
    );
  }

  if (feature.geometry?.type !== "MultiLineString") return false;

  return feature.geometry.coordinates.some((part) =>
    lineIntersectsBounds(validCoordinates(part), bounds),
  );
}

function lineBoundsInterval(
  start: GeoJSON.Position,
  end: GeoJSON.Position,
  bounds: CorridorBounds,
) {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  let tMin = 0;
  let tMax = 1;

  const clip = (delta: number, minDelta: number, maxDelta: number) => {
    if (Math.abs(delta) < Number.EPSILON) {
      return minDelta <= 0 && maxDelta >= 0;
    }

    const t1 = minDelta / delta;
    const t2 = maxDelta / delta;
    const enter = Math.min(t1, t2);
    const exit = Math.max(t1, t2);
    tMin = Math.max(tMin, enter);
    tMax = Math.min(tMax, exit);
    return tMin <= tMax;
  };

  if (!clip(dx, bounds.minLng - start[0], bounds.maxLng - start[0]))
    return null;
  if (!clip(dy, bounds.minLat - start[1], bounds.maxLat - start[1]))
    return null;

  return { enter: clamp(tMin, 0, 1), exit: clamp(tMax, 0, 1) };
}

function matchingCorridorOverride(
  routeId: string,
  coordinate: GeoJSON.Position,
) {
  return MANUAL_CORRIDOR_OVERRIDES.find(
    (override) =>
      routeMatchesOverride(routeId, override) &&
      coordinateInsideBounds(coordinate, override.bounds),
  );
}

function overrideLaneSlot(routeId: string, override: ManualCorridorOverride) {
  return override.laneSlots[routeId] ?? ROUTE_LANE_SLOTS[routeId] ?? 0;
}

function overrideZOrder(routeId: string, override: ManualCorridorOverride) {
  const laneOrderIndex = override.laneOrder.findIndex(
    (overrideRouteId) => normalizeSubwayRouteId(overrideRouteId) === routeId,
  );

  if (laneOrderIndex < 0) return ROUTE_Z_ORDER[routeId] ?? 0;
  return override.zOrderBase + laneOrderIndex;
}

function baseLaneMetadata(
  properties: SubwayLaneProperties,
  geometry: GeoJSON.Geometry | null | undefined,
  mode: ResolvedSubwayLaneRenderMode,
) {
  const routeId = normalizeSubwayRouteId(
    properties.route_id ?? properties.display_route,
  );
  const direction = directionMultiplier(geometry, routeId);

  // Defensive: if the build script has pre-baked lane metadata onto the
  // feature, trust it. The build script is the single source of truth for
  // group-corridors mode; runtime overrides here would fight it.
  if (
    typeof properties.visual_lane_slot === "number" &&
    Number.isFinite(properties.visual_lane_slot)
  ) {
    return {
      routeId,
      direction,
      visual_lane_slot: properties.visual_lane_slot,
      visual_z_order:
        typeof properties.visual_z_order === "number"
          ? properties.visual_z_order
          : (ROUTE_Z_ORDER[routeId] ?? 0),
      visual_family:
        typeof properties.visual_family === "string"
          ? properties.visual_family
          : (SUBWAY_ROUTE_FAMILY[routeId] ?? "solo"),
    };
  }

  const baseSlot = ROUTE_LANE_SLOTS[routeId] ?? 0;
  return {
    routeId,
    direction,
    visual_lane_slot: mode === "global-lanes" ? baseSlot * direction : 0,
    visual_z_order: ROUTE_Z_ORDER[routeId] ?? 0,
    visual_family: SUBWAY_ROUTE_FAMILY[routeId] ?? "solo",
  };
}

function sameCorridorState(
  left: ManualCorridorOverride | undefined,
  right: ManualCorridorOverride | undefined,
) {
  return (left?.corridorId ?? null) === (right?.corridorId ?? null);
}

function laneSlotForState(
  routeId: string,
  direction: number,
  override: ManualCorridorOverride | undefined,
  mode: ResolvedSubwayLaneRenderMode,
) {
  if (override) return overrideLaneSlot(routeId, override);
  return mode === "global-lanes"
    ? (ROUTE_LANE_SLOTS[routeId] ?? 0) * direction
    : 0;
}

function zOrderForState(
  routeId: string,
  override: ManualCorridorOverride | undefined,
) {
  if (override) return overrideZOrder(routeId, override);
  return ROUTE_Z_ORDER[routeId] ?? 0;
}

function transitionKind(
  from: ManualCorridorOverride | undefined,
  to: ManualCorridorOverride | undefined,
): SubwayLaneProperties["segment_kind"] {
  if (from && to) return "junction-switch";
  if (to) return "junction-in";
  return "junction-out";
}

function segmentLaneProperties(
  properties: SubwayLaneProperties,
  routeId: string,
  direction: number,
  override: ManualCorridorOverride | undefined,
  renderSegmentIndex: number,
  mode: ResolvedSubwayLaneRenderMode,
): SubwayLaneProperties {
  if (!override) {
    const baseSlot = ROUTE_LANE_SLOTS[routeId] ?? 0;
    return {
      ...properties,
      visual_lane_slot: mode === "global-lanes" ? baseSlot * direction : 0,
      visual_z_order: ROUTE_Z_ORDER[routeId] ?? 0,
      visual_family: SUBWAY_ROUTE_FAMILY[routeId] ?? "solo",
      corridor_id: null,
      corridor_override: false,
      segment_kind: "fallback",
      transition_length_meters: 0,
      render_segment_index: renderSegmentIndex,
    };
  }

  return {
    ...properties,
    // Corridor overrides model one lane per service in the corridor. Direction
    // variants and branch shapes must not flip the service into a second lane.
    visual_lane_slot: overrideLaneSlot(routeId, override),
    visual_z_order: overrideZOrder(routeId, override),
    visual_family: override.corridorId,
    corridor_id: override.corridorId,
    corridor_override: true,
    segment_kind: "corridor",
    transition_length_meters: override.transitionLengthMeters,
    render_segment_index: renderSegmentIndex,
  };
}

function transitionLaneProperties(
  properties: SubwayLaneProperties,
  routeId: string,
  direction: number,
  from: ManualCorridorOverride | undefined,
  to: ManualCorridorOverride | undefined,
  laneSlot: number,
  renderSegmentIndex: number,
): SubwayLaneProperties {
  const corridor = from ?? to;

  return {
    ...properties,
    visual_lane_slot: laneSlot,
    visual_z_order: zOrderForState(routeId, corridor),
    visual_family:
      corridor?.corridorId ?? SUBWAY_ROUTE_FAMILY[routeId] ?? "solo",
    corridor_id: corridor?.corridorId ?? null,
    corridor_override: Boolean(corridor),
    segment_kind: transitionKind(from, to),
    transition_length_meters: corridor?.transitionLengthMeters ?? 0,
    render_segment_index: renderSegmentIndex,
  };
}

function featureWithProperties(
  feature: GeoJSON.Feature,
  properties: SubwayLaneProperties,
  coordinates?: GeoJSON.Position[],
): GeoJSON.Feature {
  if (!coordinates) {
    return {
      ...feature,
      properties,
    };
  }

  return {
    ...feature,
    properties,
    geometry: {
      type: "LineString",
      coordinates,
    },
  };
}

function corridorDedupeKey(feature: GeoJSON.Feature) {
  const properties = (feature.properties ?? {}) as SubwayLaneProperties;
  if (!properties.corridor_override || !properties.corridor_id) return null;

  const routeId = normalizeSubwayRouteId(
    properties.route_id ?? properties.display_route,
  );
  if (!routeId) return null;

  return `${properties.corridor_id}:${routeId}`;
}

function featureLineDistance(feature: GeoJSON.Feature) {
  if (feature.geometry?.type === "LineString") {
    return lineDistance(validCoordinates(feature.geometry.coordinates));
  }

  if (feature.geometry?.type !== "MultiLineString") return 0;

  return feature.geometry.coordinates.reduce((total, part) => {
    return total + lineDistance(validCoordinates(part));
  }, 0);
}

function collapseDuplicateCorridorServiceSegments(features: GeoJSON.Feature[]) {
  const bestSourceByKey = new Map<
    string,
    { sourceKey: string; distance: number; index: number }
  >();
  const distanceBySource = new Map<
    string,
    { sourceKey: string; distance: number; index: number }
  >();

  features.forEach((feature, index) => {
    const key = corridorDedupeKey(feature);
    if (!key) return;
    const properties = (feature.properties ?? {}) as SubwayLaneProperties;
    const sourceKey = String(
      properties.render_source_key ?? properties.shape_id ?? index,
    );
    const sourceGroupKey = `${key}:${sourceKey}`;

    const distance =
      properties.segment_kind === "corridor" ? featureLineDistance(feature) : 0;
    const existingSource = distanceBySource.get(sourceGroupKey);
    if (!existingSource) {
      distanceBySource.set(sourceGroupKey, { sourceKey, distance, index });
    } else {
      existingSource.distance += distance;
      existingSource.index = Math.min(existingSource.index, index);
    }
  });

  for (const [sourceGroupKey, candidate] of distanceBySource) {
    const key = sourceGroupKey.slice(0, sourceGroupKey.lastIndexOf(":"));
    const existing = bestSourceByKey.get(key);

    if (
      !existing ||
      candidate.distance > existing.distance ||
      (candidate.distance === existing.distance &&
        candidate.index < existing.index)
    ) {
      bestSourceByKey.set(key, candidate);
    }
  }

  if (bestSourceByKey.size === 0) return features;

  return features.filter((feature) => {
    const key = corridorDedupeKey(feature);
    if (!key) return true;

    const properties = (feature.properties ?? {}) as SubwayLaneProperties;
    const sourceKey = String(
      properties.render_source_key ?? properties.shape_id ?? "",
    );
    return bestSourceByKey.get(key)?.sourceKey === sourceKey;
  });
}

function roundedCoordinateKey(value: number, precision = 2) {
  return value.toFixed(precision);
}

function directionlessGeometryKey(feature: GeoJSON.Feature) {
  const parts = featureCoordinatesForDedupe(feature);
  if (parts.length === 0) return null;

  const partKeys = parts.map((coordinates) => {
    const forward = coordinates
      .map(
        ([lng, lat]) =>
          `${roundedCoordinateKey(lng, 4)},${roundedCoordinateKey(lat, 4)}`,
      )
      .join(";");
    const reverse = coordinates
      .slice()
      .reverse()
      .map(
        ([lng, lat]) =>
          `${roundedCoordinateKey(lng, 4)},${roundedCoordinateKey(lat, 4)}`,
      )
      .join(";");
    return forward < reverse ? forward : reverse;
  });

  return partKeys.sort().join("|");
}

function featureCoordinatesForDedupe(feature: GeoJSON.Feature) {
  if (feature.geometry?.type === "LineString") {
    const coordinates = validCoordinates(feature.geometry.coordinates);
    return coordinates.length >= 2 ? [coordinates] : [];
  }

  if (feature.geometry?.type !== "MultiLineString") return [];

  return feature.geometry.coordinates
    .map(validCoordinates)
    .filter((coordinates) => coordinates.length >= 2);
}

function fallbackProblemDedupeKey(feature: GeoJSON.Feature) {
  const properties = (feature.properties ?? {}) as SubwayLaneProperties;
  if (properties.segment_kind !== "fallback" || properties.corridor_id)
    return null;

  const laneSlot = Number(properties.visual_lane_slot ?? 0);
  if (Number.isFinite(laneSlot) && Math.abs(laneSlot) > 1e-9) return null;

  const routeId = normalizeSubwayRouteId(
    properties.route_id ?? properties.display_route,
  );
  if (!routeId) return null;

  const matchingBounds = FALLBACK_VARIANT_DEDUPE_BOUNDS.find(({ bounds }) =>
    featureIntersectsBounds(feature, bounds),
  );
  if (!matchingBounds) return null;

  const geometryKey = directionlessGeometryKey(feature);
  if (!geometryKey) return null;

  return `${matchingBounds.id}:${routeId}:${geometryKey}`;
}

function collapseDuplicateFallbackProblemSegments(features: GeoJSON.Feature[]) {
  const bestByKey = new Map<string, { distance: number; index: number }>();

  features.forEach((feature, index) => {
    const key = fallbackProblemDedupeKey(feature);
    if (!key) return;
    const distance = featureLineDistance(feature);
    const existing = bestByKey.get(key);
    if (
      !existing ||
      distance > existing.distance ||
      (distance === existing.distance && index < existing.index)
    ) {
      bestByKey.set(key, { distance, index });
    }
  });

  if (bestByKey.size === 0) return features;

  return features.filter((feature, index) => {
    const key = fallbackProblemDedupeKey(feature);
    if (!key) return true;
    return bestByKey.get(key)?.index === index;
  });
}

function renderCompatibilityKey(feature: GeoJSON.Feature) {
  if (feature.geometry?.type !== "LineString") return null;

  const properties = (feature.properties ?? {}) as SubwayLaneProperties;
  const segmentKind = String(properties.segment_kind ?? "fallback");
  if (segmentKind.startsWith("junction")) return null;

  const routeId = normalizeSubwayRouteId(
    properties.route_id ?? properties.display_route,
  );
  if (!routeId) return null;

  const color = String(properties.color ?? "");
  const laneSlot = Number(properties.visual_lane_slot ?? 0);
  const zOrder = Number(properties.visual_z_order ?? 0);
  const corridorId = properties.corridor_id ?? null;

  return [
    routeId,
    Number.isFinite(laneSlot) ? laneSlot.toFixed(6) : "0",
    Number.isFinite(zOrder) ? zOrder.toFixed(6) : "0",
    corridorId ?? "none",
    segmentKind,
    color,
  ].join("|");
}

function mergeContiguousCompatibleSegments(features: GeoJSON.Feature[]) {
  const merged: GeoJSON.Feature[] = [];

  for (const feature of features) {
    const currentCoordinates =
      feature.geometry?.type === "LineString"
        ? validCoordinates(feature.geometry.coordinates)
        : [];
    const currentKey = renderCompatibilityKey(feature);
    if (currentKey && currentCoordinates.length >= 2) {
      const connectableIndex = merged.findIndex((candidate) => {
        if (renderCompatibilityKey(candidate) !== currentKey) return false;
        if (candidate.geometry?.type !== "LineString") return false;
        const candidateCoordinates = validCoordinates(
          candidate.geometry.coordinates,
        );
        if (candidateCoordinates.length < 2) return false;
        return (
          coordinatesEqual(
            candidateCoordinates[candidateCoordinates.length - 1],
            currentCoordinates[0],
          ) ||
          coordinatesEqual(
            candidateCoordinates[0],
            currentCoordinates[currentCoordinates.length - 1],
          )
        );
      });

      if (connectableIndex >= 0) {
        const connectable = merged[connectableIndex];
        const connectableCoordinates =
          connectable.geometry?.type === "LineString"
            ? validCoordinates(connectable.geometry.coordinates)
            : [];

        if (
          coordinatesEqual(
            connectableCoordinates[connectableCoordinates.length - 1],
            currentCoordinates[0],
          )
        ) {
          connectable.geometry = {
            type: "LineString",
            coordinates: [
              ...connectableCoordinates,
              ...currentCoordinates.slice(1),
            ],
          };
        } else {
          connectable.geometry = {
            type: "LineString",
            coordinates: [
              ...currentCoordinates,
              ...connectableCoordinates.slice(1),
            ],
          };
        }
        continue;
      }
    }

    merged.push(feature);
  }

  return merged;
}

function transitionBoundaryT(
  start: GeoJSON.Position,
  end: GeoJSON.Position,
  from: ManualCorridorOverride | undefined,
  to: ManualCorridorOverride | undefined,
) {
  if (from && !to) {
    return lineBoundsInterval(start, end, from.bounds)?.exit ?? 0.5;
  }

  if (!from && to) {
    return lineBoundsInterval(start, end, to.bounds)?.enter ?? 0.5;
  }

  if (from && to) {
    const fromExit = lineBoundsInterval(start, end, from.bounds)?.exit;
    const toEnter = lineBoundsInterval(start, end, to.bounds)?.enter;
    if (Number.isFinite(fromExit) && Number.isFinite(toEnter)) {
      return clamp(((fromExit ?? 0.5) + (toEnter ?? 0.5)) / 2, 0, 1);
    }
  }

  return 0.5;
}

function transitionRange(
  start: GeoJSON.Position,
  end: GeoJSON.Position,
  boundaryT: number,
  transitionLengthMeters: number,
) {
  const edgeMeters = metersBetween(start, end);
  const transitionFraction =
    edgeMeters > 0
      ? clamp(transitionLengthMeters / edgeMeters, 0.002, 0.72)
      : 0.5;
  const half = transitionFraction / 2;
  let startT = clamp(boundaryT - half, 0, 1);
  let endT = clamp(boundaryT + half, 0, 1);

  if (endT - startT < 0.08) {
    startT = clamp(boundaryT - 0.04, 0, 1);
    endT = clamp(boundaryT + 0.04, 0, 1);
  }

  return { startT, endT };
}

function createTransitionSegments(
  feature: GeoJSON.Feature,
  properties: SubwayLaneProperties,
  routeId: string,
  direction: number,
  from: ManualCorridorOverride | undefined,
  to: ManualCorridorOverride | undefined,
  start: GeoJSON.Position,
  end: GeoJSON.Position,
  renderSegmentIndex: number,
  mode: ResolvedSubwayLaneRenderMode,
) {
  const transitionLengthMeters =
    Math.max(
      from?.transitionLengthMeters ?? 0,
      to?.transitionLengthMeters ?? 0,
    ) || 30;
  const boundaryT = transitionBoundaryT(start, end, from, to);
  const { startT, endT } = transitionRange(
    start,
    end,
    boundaryT,
    transitionLengthMeters,
  );
  const fromSlot = laneSlotForState(routeId, direction, from, mode);
  const toSlot = laneSlotForState(routeId, direction, to, mode);
  const transitionStart = interpolateCoordinate(start, end, startT);
  const transitionEnd = interpolateCoordinate(start, end, endT);
  const transitionMeters = metersBetween(transitionStart, transitionEnd);
  const steps = Math.max(
    1,
    Math.min(4, Math.floor(transitionMeters / MIN_TRANSITION_SEGMENT_METERS)),
  );
  const segments: GeoJSON.Feature[] = [];

  for (let step = 0; step < steps; step += 1) {
    const t0 = startT + ((endT - startT) * step) / steps;
    const t1 = startT + ((endT - startT) * (step + 1)) / steps;
    if (Math.abs(t1 - t0) < 1e-6) continue;

    const midpoint = (step + 0.5) / steps;
    const eased = 0.5 * (1 - Math.cos(Math.PI * midpoint));
    const laneSlot = fromSlot + (toSlot - fromSlot) * eased;
    const startCoordinate = interpolateCoordinate(start, end, t0);
    const endCoordinate = interpolateCoordinate(start, end, t1);
    const segmentMeters = metersBetween(startCoordinate, endCoordinate);

    if (segmentMeters < MIN_TRANSITION_SEGMENT_METERS && steps > 1) {
      devWarn("[subway-lane-separation] suppressed tiny transition fragment", {
        routeId,
        from: from?.corridorId ?? null,
        to: to?.corridorId ?? null,
        segmentMeters,
      });
      continue;
    }

    segments.push(
      featureWithProperties(
        feature,
        transitionLaneProperties(
          properties,
          routeId,
          direction,
          from,
          to,
          laneSlot,
          renderSegmentIndex + segments.length,
        ),
        [startCoordinate, endCoordinate],
      ),
    );
  }

  return {
    startPoint: transitionStart,
    endPoint: transitionEnd,
    segments,
  };
}

function splitLineStringByCorridorOverrides(
  feature: GeoJSON.Feature,
  properties: SubwayLaneProperties,
  options: {
    junctionTransitionsEnabled: boolean;
    mode: ResolvedSubwayLaneRenderMode;
  },
): GeoJSON.Feature[] | null {
  if (feature.geometry?.type !== "LineString") return null;

  const coordinates = validCoordinates(feature.geometry.coordinates);
  if (coordinates.length < 2) return null;

  const { routeId, direction } = baseLaneMetadata(
    properties,
    feature.geometry,
    options.mode,
  );
  const states = coordinates.map((coordinate) =>
    matchingCorridorOverride(routeId, coordinate),
  );

  if (!states.some(Boolean)) return null;

  const segments: GeoJSON.Feature[] = [];
  let currentState = states[0];
  let currentCoordinates: GeoJSON.Position[] = [coordinates[0]];

  for (let index = 1; index < coordinates.length; index += 1) {
    const nextState = states[index];

    if (sameCorridorState(currentState, nextState)) {
      currentCoordinates.push(coordinates[index]);
      continue;
    }

    const previousCoordinate = coordinates[index - 1];
    const nextCoordinate = coordinates[index];

    if (!options.junctionTransitionsEnabled) {
      const boundaryCoordinate = interpolateCoordinate(
        previousCoordinate,
        nextCoordinate,
        transitionBoundaryT(
          previousCoordinate,
          nextCoordinate,
          currentState,
          nextState,
        ),
      );

      pushUniqueCoordinate(currentCoordinates, boundaryCoordinate);

      if (hasRenderableLine(currentCoordinates)) {
        segments.push(
          featureWithProperties(
            feature,
            segmentLaneProperties(
              properties,
              routeId,
              direction,
              currentState,
              segments.length,
              options.mode,
            ),
            currentCoordinates,
          ),
        );
      }

      currentState = nextState;
      currentCoordinates = [];
      pushUniqueCoordinate(currentCoordinates, boundaryCoordinate);
      pushUniqueCoordinate(currentCoordinates, nextCoordinate);
      continue;
    }

    const transition = createTransitionSegments(
      feature,
      properties,
      routeId,
      direction,
      currentState,
      nextState,
      previousCoordinate,
      nextCoordinate,
      segments.length,
      options.mode,
    );

    pushUniqueCoordinate(currentCoordinates, transition.startPoint);

    if (hasRenderableLine(currentCoordinates)) {
      segments.push(
        featureWithProperties(
          feature,
          segmentLaneProperties(
            properties,
            routeId,
            direction,
            currentState,
            segments.length,
            options.mode,
          ),
          currentCoordinates,
        ),
      );
    }

    segments.push(...transition.segments);
    currentState = nextState;
    currentCoordinates = [];
    pushUniqueCoordinate(currentCoordinates, transition.endPoint);
    pushUniqueCoordinate(currentCoordinates, nextCoordinate);
  }

  if (hasRenderableLine(currentCoordinates)) {
    segments.push(
      featureWithProperties(
        feature,
        segmentLaneProperties(
          properties,
          routeId,
          direction,
          currentState,
          segments.length,
          options.mode,
        ),
        currentCoordinates,
      ),
    );
  }

  return segments.length > 0 ? segments : null;
}

export function prepareSubwayNetworkForLaneSeparation(
  data: GeoJSON.FeatureCollection,
  options: LanePrepOptions = {},
) {
  const renderMode = resolveSubwayLaneRenderMode(options.mode);
  const enabled =
    options.enabled ??
    (ENABLE_SUBWAY_LANE_SEPARATION &&
      renderMode !== "canonical" &&
      renderMode !== "visual-no-lanes" &&
      renderMode !== "family-visual" &&
      renderMode !== "group-corridors" &&
      renderMode !== "schematic-family-pilot");
  if (!enabled) return data;

  if (
    renderMode === "canonical" ||
    renderMode === "visual-no-lanes" ||
    renderMode === "schematic-family-pilot"
  ) {
    return stripLaneMetadata(data);
  }

  const junctionTransitionsEnabled =
    options.junctionTransitionsEnabled ??
    renderMode === "junction-transitions-4c";

  const renderData = cloneFeatureCollection(data);

  const preparedFeatures = renderData.features.flatMap(
    (feature, featureIndex) => {
      const rawProperties = (feature.properties ?? {}) as SubwayLaneProperties;
      const routeId = normalizeSubwayRouteId(
        rawProperties.route_id ?? rawProperties.display_route,
      );
      const properties = {
        ...rawProperties,
        render_source_key: String(
          rawProperties.shape_id ?? `${routeId || "route"}-${featureIndex}`,
        ),
      };
      if (renderMode === "global-lanes") {
        const metadata = baseLaneMetadata(
          properties,
          feature.geometry,
          renderMode,
        );
        return featureWithProperties(feature, {
          ...properties,
          visual_lane_slot: metadata.visual_lane_slot,
          visual_z_order: metadata.visual_z_order,
          visual_family: metadata.visual_family,
          corridor_id: null,
          corridor_override: false,
          segment_kind: "fallback",
          transition_length_meters: 0,
          render_segment_index: 0,
        });
      }
      const splitFeatures = splitLineStringByCorridorOverrides(
        feature,
        properties,
        {
          junctionTransitionsEnabled,
          mode: renderMode,
        },
      );
      if (splitFeatures) return splitFeatures;

      const metadata = baseLaneMetadata(
        properties,
        feature.geometry,
        renderMode,
      );
      return featureWithProperties(feature, {
        ...properties,
        visual_lane_slot: metadata.visual_lane_slot,
        visual_z_order: metadata.visual_z_order,
        visual_family: metadata.visual_family,
        corridor_id: null,
        corridor_override: false,
        segment_kind: "fallback",
        transition_length_meters: 0,
        render_segment_index: 0,
      });
    },
  );
  renderData.features = mergeContiguousCompatibleSegments(
    collapseDuplicateFallbackProblemSegments(
      collapseDuplicateCorridorServiceSegments(preparedFeatures),
    ),
  );

  return renderData;
}
