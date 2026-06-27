import { existsSync, readFileSync } from "node:fs";
import { MTA_ROUTE_COLORS } from "./mta-colors.ts";
import type { Feature, LineStringGeometry, Position } from "./types.ts";

export const OPEN_DATA_SOURCE_DATASET_ID = "s692-irgq";
export const OPEN_DATA_SOURCE_NAME = "nyc_opendata_subway_service_lines";

const ROUTE_ORDER: string[] = [
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "6X",
  "7",
  "7X",
  "A",
  "C",
  "E",
  "B",
  "D",
  "F",
  "FX",
  "M",
  "N",
  "Q",
  "R",
  "W",
  "J",
  "Z",
  "L",
  "G",
  "S",
  "FS",
  "GS",
  "H",
  "SI",
];

// Single source of truth lives in lib/mta-colors.json.
const ROUTE_COLORS = MTA_ROUTE_COLORS;

const ROUTE_ALIASES: Array<{ base: string; alias: string }> = [
  { base: "6", alias: "6X" },
  { base: "7", alias: "7X" },
  { base: "F", alias: "FX" },
];

const EXACT_SERVICE_OVERRIDES: Record<string, string[]> = {
  SF: ["FS"],
  ST: ["GS"],
  SR: ["H"],
  "5 PEAK": ["5"],
};

const IGNORED_SERVICE_WORDS = new Set<string>([
  "PEAK",
  "EXPRESS",
  "LOCAL",
  "LINE",
  "AV",
  "AVE",
  "AVENUE",
  "ST",
  "STREET",
]);

type RawProperties = Record<string, unknown>;

type RawGeometry = {
  type?: string;
  coordinates?: unknown;
};

type RawFeature = {
  id?: string | number;
  geometry?: RawGeometry | null;
  properties?: RawProperties | null;
};

type RawFeatureCollection = {
  type?: string;
  features?: RawFeature[];
};

type RouteSymbol = {
  field: string;
  value: string;
};

type ColorRouteMap = Record<string, string[]>;
type CountMap = Record<string, number>;

type OpenDataLineProperties = {
  visual_feature_type: "opendata_line";
  opendata_id: string;
  opendata_objectid: string | null;
  opendata_name: unknown;
  opendata_rt_symbol: string;
  opendata_symbol_field: string;
  opendata_part_index: number;
  route_ids: string[];
  color_route_ids: ColorRouteMap;
  source_route_ids: string[];
  added_alias_route_ids: string[];
  geometry_source: typeof OPEN_DATA_SOURCE_NAME;
  geometry_source_dataset_id: typeof OPEN_DATA_SOURCE_DATASET_ID;
  length_m: number;
  coordinate_count: number;
};

type OpenDataLineFeature = Feature<LineStringGeometry, OpenDataLineProperties>;

type AliasApplication = {
  base_route_id: string;
  alias_route_id: string;
  feature_count: number;
};

type NormalizeOptions = {
  expectedRouteIds?: string[];
  minFragmentLengthM?: number;
};

type NormalizeResult = {
  features: OpenDataLineFeature[];
  diagnostics: {
    source_feature_count: number;
    normalized_feature_count: number;
    represented_route_ids: string[];
    missing_expected_route_ids: string[];
    alias_applications: AliasApplication[];
    dropped_short_fragment_count: number;
    source_field_counts: CountMap;
    geometry_type_counts: CountMap;
  };
};

function compareRouteIds(left: string, right: string): number {
  const leftIndex = ROUTE_ORDER.indexOf(left);
  const rightIndex = ROUTE_ORDER.indexOf(right);
  if (leftIndex !== -1 || rightIndex !== -1) {
    return (leftIndex === -1 ? 999 : leftIndex) - (rightIndex === -1 ? 999 : rightIndex);
  }
  return left.localeCompare(right, "en", { numeric: true });
}

function normalizeRouteId(value: unknown): string {
  const routeId = String(value ?? "").trim().toUpperCase();
  if (routeId === "6D") return "6X";
  if (routeId === "7D") return "7X";
  if (routeId === "FD") return "FX";
  if (routeId === "SIR") return "SI";
  if (routeId === "SF") return "FS";
  if (routeId === "ST") return "GS";
  if (routeId === "SR") return "H";
  return routeId;
}

function routeColorFor(routeId: string): string {
  return ROUTE_COLORS[routeId] ?? "#808183";
}

function colorRouteMap(routeIds: string[]): ColorRouteMap {
  const byColor: ColorRouteMap = {};
  for (const routeId of routeIds) {
    const color = routeColorFor(routeId);
    if (!byColor[color]) byColor[color] = [];
    byColor[color].push(routeId);
  }
  for (const color of Object.keys(byColor)) {
    byColor[color] = [...new Set(byColor[color])].sort(compareRouteIds);
  }
  return byColor;
}

function metersPerDegLng(lat: number): number {
  return 111_320 * Math.cos((lat * Math.PI) / 180);
}

function distanceMeters(a: Position, b: Position): number {
  const midLat = (a[1] + b[1]) / 2;
  const dx = (a[0] - b[0]) * metersPerDegLng(midLat);
  const dy = (a[1] - b[1]) * 111_320;
  return Math.hypot(dx, dy);
}

function lineLengthMeters(coords: Position[]): number {
  let total = 0;
  for (let index = 1; index < coords.length; index += 1) {
    total += distanceMeters(coords[index - 1], coords[index]);
  }
  return total;
}

function routeSymbolFromProperties(properties: RawProperties): RouteSymbol | null {
  for (const field of ["rt_symbol", "service", "route_id", "routes", "line"]) {
    const value = properties?.[field];
    if (value != null && String(value).trim() !== "") {
      return { field, value: String(value).trim() };
    }
  }
  return null;
}

function parseRouteSymbols(rawSymbol: unknown, context: string): string[] {
  const normalizedRaw = String(rawSymbol ?? "").trim().toUpperCase();
  if (!normalizedRaw) {
    throw new Error(`${context}: missing route symbol`);
  }

  if (EXACT_SERVICE_OVERRIDES[normalizedRaw]) {
    return [...EXACT_SERVICE_OVERRIDES[normalizedRaw]];
  }

  const splitTokens = normalizedRaw
    .replace(/[,&/]+/g, "-")
    .split(/[-\s]+/)
    .map((token) => token.trim())
    .filter(Boolean);

  const routeIds: string[] = [];
  const invalidTokens: string[] = [];

  for (const token of splitTokens) {
    if (IGNORED_SERVICE_WORDS.has(token)) continue;
    const routeId = normalizeRouteId(token);
    if (routeId === "S") {
      throw new Error(
        `${context}: ambiguous shuttle route symbol "S"; use FS, GS, H, SF, ST, or SR`,
      );
    }
    if (!ROUTE_COLORS[routeId]) {
      invalidTokens.push(token);
      continue;
    }
    routeIds.push(routeId);
  }

  if (routeIds.length === 0) {
    throw new Error(
      `${context}: no supported route ids parsed from "${rawSymbol}"` +
        (invalidTokens.length > 0 ? ` (invalid: ${invalidTokens.join(",")})` : ""),
    );
  }

  if (invalidTokens.length > 0) {
    throw new Error(`${context}: unsupported route tokens: ${invalidTokens.join(",")}`);
  }

  return [...new Set(routeIds)].sort(compareRouteIds);
}

function isFiniteCoordinate(coord: unknown): coord is [unknown, unknown] {
  return (
    Array.isArray(coord) &&
    coord.length >= 2 &&
    Number.isFinite(Number(coord[0])) &&
    Number.isFinite(Number(coord[1]))
  );
}

function cleanLineStringCoordinates(rawCoords: unknown, context: string): Position[] {
  if (!Array.isArray(rawCoords) || rawCoords.length < 2) {
    throw new Error(`${context}: LineString must contain at least two coordinates`);
  }

  const coords = rawCoords.map((coord, index) => {
    if (!isFiniteCoordinate(coord)) {
      throw new Error(`${context}: invalid coordinate at index ${index}`);
    }
    return [Number(coord[0]), Number(coord[1])] as Position;
  });

  const deduped: Position[] = [];
  for (const coord of coords) {
    const previous = deduped[deduped.length - 1];
    if (previous && previous[0] === coord[0] && previous[1] === coord[1]) continue;
    deduped.push(coord);
  }

  if (deduped.length < 2) {
    throw new Error(`${context}: LineString collapsed to fewer than two coordinates`);
  }

  return deduped;
}

function geometryParts(feature: RawFeature, context: string): Position[][] {
  const geometry = feature?.geometry;
  if (!geometry) throw new Error(`${context}: missing geometry`);

  if (geometry.type === "LineString") {
    return [cleanLineStringCoordinates(geometry.coordinates, context)];
  }

  if (geometry.type === "MultiLineString") {
    if (!Array.isArray(geometry.coordinates) || geometry.coordinates.length === 0) {
      throw new Error(`${context}: MultiLineString must contain parts`);
    }
    return geometry.coordinates.map((part, partIndex) =>
      cleanLineStringCoordinates(part, `${context}:part-${partIndex}`),
    );
  }

  throw new Error(`${context}: unsupported geometry type ${geometry.type}`);
}

function applyExpectedRouteAliases(
  features: OpenDataLineFeature[],
  expectedRouteIds: string[],
): AliasApplication[] {
  const expected = new Set(expectedRouteIds);
  const explicit = new Set(features.flatMap((feature) => feature.properties.route_ids));
  const aliasApplications: AliasApplication[] = [];

  for (const { base, alias } of ROUTE_ALIASES) {
    if (expected.size > 0 && !expected.has(alias)) continue;
    if (explicit.has(alias)) continue;

    let appliedCount = 0;
    for (const feature of features) {
      const routeIds = feature.properties.route_ids;
      if (!routeIds.includes(base) || routeIds.includes(alias)) continue;
      routeIds.push(alias);
      routeIds.sort(compareRouteIds);
      feature.properties.added_alias_route_ids.push(alias);
      feature.properties.color_route_ids = colorRouteMap(routeIds);
      appliedCount += 1;
    }

    if (appliedCount > 0) {
      aliasApplications.push({ base_route_id: base, alias_route_id: alias, feature_count: appliedCount });
    }
  }

  return aliasApplications;
}

export function normalizeOpenDataSubwayLines(
  geojson: RawFeatureCollection,
  options: NormalizeOptions = {},
): NormalizeResult {
  if (!geojson || geojson.type !== "FeatureCollection" || !Array.isArray(geojson.features)) {
    throw new Error("OpenData subway lines must be a GeoJSON FeatureCollection");
  }

  const expectedRouteIds = [...new Set(options.expectedRouteIds ?? [])].sort(compareRouteIds);
  const minFragmentLengthM = Number(options.minFragmentLengthM ?? 0);
  const features: OpenDataLineFeature[] = [];
  const sourceFieldCounts: CountMap = {};
  const geometryTypeCounts: CountMap = {};
  let droppedShortFragmentCount = 0;

  geojson.features.forEach((feature, featureIndex) => {
    const properties = feature.properties ?? {};
    const id = String(properties.id ?? properties.objectid ?? feature.id ?? featureIndex + 1);
    const objectId = properties.objectid == null ? null : String(properties.objectid);
    const routeSymbol = routeSymbolFromProperties(properties);
    if (!routeSymbol) {
      throw new Error(`OpenData feature ${id}: missing route symbol field`);
    }
    sourceFieldCounts[routeSymbol.field] = (sourceFieldCounts[routeSymbol.field] ?? 0) + 1;
    geometryTypeCounts[feature.geometry?.type ?? "missing"] =
      (geometryTypeCounts[feature.geometry?.type ?? "missing"] ?? 0) + 1;

    const routeIds = parseRouteSymbols(routeSymbol.value, `OpenData feature ${id}`);
    const parts = geometryParts(feature, `OpenData feature ${id}`);

    parts.forEach((coordinates, partIndex) => {
      const lengthM = lineLengthMeters(coordinates);
      if (minFragmentLengthM > 0 && lengthM < minFragmentLengthM) {
        droppedShortFragmentCount += 1;
        return;
      }

      const normalizedRouteIds = [...routeIds];
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates,
        },
        properties: {
          visual_feature_type: "opendata_line",
          opendata_id: String(properties.id ?? objectId ?? `feature-${featureIndex + 1}`),
          opendata_objectid: objectId,
          opendata_name: properties.name == null ? (properties.service_name ?? null) : String(properties.name),
          opendata_rt_symbol: routeSymbol.value,
          opendata_symbol_field: routeSymbol.field,
          opendata_part_index: partIndex,
          route_ids: normalizedRouteIds,
          color_route_ids: colorRouteMap(normalizedRouteIds),
          source_route_ids: normalizedRouteIds,
          added_alias_route_ids: [],
          geometry_source: OPEN_DATA_SOURCE_NAME,
          geometry_source_dataset_id: OPEN_DATA_SOURCE_DATASET_ID,
          length_m: Number(lengthM.toFixed(2)),
          coordinate_count: coordinates.length,
        },
      });
    });
  });

  const aliasApplications = applyExpectedRouteAliases(features, expectedRouteIds);
  features.sort((left, right) => {
    const routeCompare = left.properties.route_ids.join("|").localeCompare(
      right.properties.route_ids.join("|"),
      "en",
      { numeric: true },
    );
    if (routeCompare !== 0) return routeCompare;
    const idCompare = left.properties.opendata_id.localeCompare(right.properties.opendata_id, "en", {
      numeric: true,
    });
    if (idCompare !== 0) return idCompare;
    return left.properties.opendata_part_index - right.properties.opendata_part_index;
  });

  const representedRoutes = new Set(features.flatMap((feature) => feature.properties.route_ids));
  const missingExpectedRouteIds = expectedRouteIds.filter((routeId) => !representedRoutes.has(routeId));

  return {
    features,
    diagnostics: {
      source_feature_count: geojson.features.length,
      normalized_feature_count: features.length,
      represented_route_ids: [...representedRoutes].sort(compareRouteIds),
      missing_expected_route_ids: missingExpectedRouteIds,
      alias_applications: aliasApplications,
      dropped_short_fragment_count: droppedShortFragmentCount,
      source_field_counts: sourceFieldCounts,
      geometry_type_counts: geometryTypeCounts,
    },
  };
}

export function loadOpenDataSubwayLines(path: string, options: NormalizeOptions = {}): NormalizeResult {
  if (!existsSync(path)) {
    throw new Error(`OpenData subway lines file missing at ${path}`);
  }
  const geojson = JSON.parse(readFileSync(path, "utf8"));
  return normalizeOpenDataSubwayLines(geojson, options);
}
