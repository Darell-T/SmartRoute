#!/usr/bin/env node

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { inflateRawSync } from "node:zlib";
import { createHash } from "node:crypto";
import { MTA_ROUTE_COLORS } from "./build/mta-colors.ts";
import type { Feature, FeatureCollection, LineStringGeometry, Position, RouteId } from "./build/types.ts";

const here = __dirname;
const frontendRoot = resolve(here, "..");
const publicDir = resolve(frontendRoot, "public");
const cacheDir = resolve(process.env.SMARTROUTE_GTFS_CACHE_DIR ?? resolve(frontendRoot, ".gtfs-cache"));

const GTFS_URL =
  "http://web.mta.info/developers/data/nyct/subway/google_transit.zip";
const ZIP_PATH = resolve(cacheDir, "google_transit.zip");
const OUTPUT_PATH = resolve(publicDir, "subway-network.canonical.geojson");
const CURRENT_NETWORK_PATH = resolve(publicDir, "subway-network.geojson");

const NYC_BBOX = {
  minLng: -75,
  maxLng: -72.5,
  minLat: 40,
  maxLat: 41.2,
};

const EXPECTED_ROUTES = [
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
  "B",
  "C",
  "D",
  "E",
  "F",
  "FX",
  "G",
  "J",
  "L",
  "M",
  "N",
  "Q",
  "R",
  "S",
  "SI",
  "W",
  "Z",
] as const;

type CsvRow = Record<string, string>;

type GtfsRoute = {
  rawRouteId: string;
  routeId: RouteId;
  displayRoute: RouteId;
  color: string;
};

type GtfsPolylineRoute = {
  polylineId: string;
  route: GtfsRoute;
};

type GtfsPolylineVertex = {
  lat: number;
  lng: number;
  sequence: number;
};

type CanonicalProperties = {
  route_id: RouteId;
  display_route: RouteId;
  "shape_id": string;
  color: string;
};

type CanonicalFeature = Feature<LineStringGeometry, CanonicalProperties>;

type CanonicalFeatureCollection = FeatureCollection<CanonicalFeature> & {
  metadata: {
    source: string;
    generated_at: string;
    canonical_hash_basis: string;
    phase: string;
    dedupe: {
      strategy: string;
      input_features: number;
      dropped_features: number;
    };
    gtfs_zip_sha256?: string;
  };
};

type RouteStats = {
  count: number;
  km: number;
};

type BuildCanonicalInput = {
  routesRows: CsvRow[];
  tripsRows: CsvRow[];
  polylineRows: CsvRow[];
};

type CoordinateBbox = {
  minLng: number;
  maxLng: number;
  minLat: number;
  maxLat: number;
};

const ROUTE_COLOR_FALLBACKS = MTA_ROUTE_COLORS;

export function normalizeRouteId(value: string): RouteId {
  const route = String(value || "").trim().toUpperCase();
  if (route === "6D") return "6X";
  if (route === "7D") return "7X";
  if (route === "FD") return "FX";
  if (route === "FS" || route === "GS" || route === "H") return "S";
  if (route === "SIR") return "SI";
  return route;
}

export function normalizeColor(value: string, routeId: RouteId): string {
  const raw = String(value || "").trim().replace(/^#/, "");
  if (/^[0-9a-fA-F]{6}$/.test(raw)) return `#${raw.toUpperCase()}`;
  return ROUTE_COLOR_FALLBACKS[routeId] || "#A7A9AC";
}

function readUInt16(buffer: Buffer, offset: number): number {
  return buffer.readUInt16LE(offset);
}

function readUInt32(buffer: Buffer, offset: number): number {
  return buffer.readUInt32LE(offset);
}

function findEndOfCentralDirectoryOffset(zipBuffer: Buffer): number {
  for (let i = zipBuffer.length - 22; i >= 0; i--) {
    if (readUInt32(zipBuffer, i) === 0x06054b50) return i;
  }
  throw new Error("Could not find ZIP end-of-central-directory record.");
}

function decodeZipPayload(
  compressionMethod: number,
  compressed: Buffer,
  uncompressedSize: number,
  name: string,
): Buffer {
  let data: Buffer;
  if (compressionMethod === 0) {
    data = compressed;
  } else if (compressionMethod === 8) {
    data = inflateRawSync(compressed);
  } else {
    throw new Error(
      `Unsupported ZIP compression method ${compressionMethod} for ${name}.`,
    );
  }
  if (data.length !== uncompressedSize) {
    throw new Error(`Unexpected uncompressed size for ${name}.`);
  }
  return data;
}

function readWantedZipFile(
  zipBuffer: Buffer,
  offset: number,
  wanted: Set<string>,
  entries: Map<string, string>,
): number {
  if (readUInt32(zipBuffer, offset) !== 0x02014b50) {
    throw new Error("Malformed ZIP central directory.");
  }

  const compressionMethod = readUInt16(zipBuffer, offset + 10);
  const compressedSize = readUInt32(zipBuffer, offset + 20);
  const uncompressedSize = readUInt32(zipBuffer, offset + 24);
  const fileNameLength = readUInt16(zipBuffer, offset + 28);
  const extraLength = readUInt16(zipBuffer, offset + 30);
  const commentLength = readUInt16(zipBuffer, offset + 32);
  const localHeaderOffset = readUInt32(zipBuffer, offset + 42);
  const name = zipBuffer
    .subarray(offset + 46, offset + 46 + fileNameLength)
    .toString("utf8");

  if (wanted.has(name)) {
    if (readUInt32(zipBuffer, localHeaderOffset) !== 0x04034b50) {
      throw new Error(`Malformed ZIP local header for ${name}.`);
    }
    const localNameLength = readUInt16(zipBuffer, localHeaderOffset + 26);
    const localExtraLength = readUInt16(zipBuffer, localHeaderOffset + 28);
    const dataOffset = localHeaderOffset + 30 + localNameLength + localExtraLength;
    const compressed = zipBuffer.subarray(dataOffset, dataOffset + compressedSize);
    const data = decodeZipPayload(compressionMethod, compressed, uncompressedSize, name);
    entries.set(name, data.toString("utf8").replace(/^\uFEFF/, ""));
  }

  return 46 + fileNameLength + extraLength + commentLength;
}

export function parseZipEntries(zipBuffer: Buffer, wantedNames: string[]): Map<string, string> {
  const wanted = new Set(wantedNames);
  const entries = new Map<string, string>();
  const eocdOffset = findEndOfCentralDirectoryOffset(zipBuffer);
  const centralDirectorySize = readUInt32(zipBuffer, eocdOffset + 12);
  const centralDirectoryOffset = readUInt32(zipBuffer, eocdOffset + 16);
  let offset = centralDirectoryOffset;
  const end = centralDirectoryOffset + centralDirectorySize;

  while (offset < end) {
    offset += readWantedZipFile(zipBuffer, offset, wanted, entries);
  }

  for (const name of wanted) {
    if (!entries.has(name)) {
      throw new Error(`GTFS zip did not include required file: ${name}`);
    }
  }

  return entries;
}

export function parseCsv(text: string): CsvRow[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
        }
      } else {
        field += char;
      }
      continue;
    }

    if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (char !== "\r") {
      field += char;
    }
  }

  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }

  const [header, ...body] = rows.filter((candidate) =>
    candidate.some((value) => value !== ""),
  );
  if (!header) return [];

  return body.map((values) => {
    const record: CsvRow = {};
    header.forEach((key, index) => {
      record[key] = values[index] ?? "";
    });
    return record;
  });
}

async function ensureGtfsZip(): Promise<Buffer> {
  mkdirSync(cacheDir, { recursive: true });
  if (existsSync(ZIP_PATH)) {
    return readFileSync(ZIP_PATH);
  }

  console.log(`[gtfs] downloading ${GTFS_URL}`);
  const response = await fetch(GTFS_URL);
  if (!response.ok) {
    throw new Error(`Failed to download GTFS zip: ${response.status}`);
  }
  const buffer = Buffer.from(await response.arrayBuffer());
  writeFileSync(ZIP_PATH, buffer);
  return buffer;
}

function requiredZipText(entries: Map<string, string>, name: string): string {
  const text = entries.get(name);
  if (text == null) {
    throw new Error(`GTFS zip did not include required file: ${name}`);
  }
  return text;
}

function buildRoutesById(routesRows: CsvRow[]): Map<string, GtfsRoute> {
  const routes = new Map<string, GtfsRoute>();
  for (const row of routesRows) {
    const rawRouteId = String(row.route_id || "").trim();
    const routeId = normalizeRouteId(rawRouteId);
    if (!routeId) continue;
    routes.set(rawRouteId, {
      rawRouteId,
      routeId,
      displayRoute: normalizeRouteId(row.route_short_name || rawRouteId),
      color: normalizeColor(row.route_color, routeId),
    });
  }
  return routes;
}

function buildPolylineRoutes(tripsRows: CsvRow[], routesByRawId: Map<string, GtfsRoute>): GtfsPolylineRoute[] {
  const polylineRoutes = new Map<string, GtfsPolylineRoute>();

  for (const row of tripsRows) {
    const polylineId = String(row["shape_id"] || "").trim();
    const rawRouteId = String(row.route_id || "").trim();
    if (!polylineId || !rawRouteId) continue;

    const route = routesByRawId.get(rawRouteId) || {
      rawRouteId,
      routeId: normalizeRouteId(rawRouteId),
      displayRoute: normalizeRouteId(rawRouteId),
      color: normalizeColor("", normalizeRouteId(rawRouteId)),
    };
    if (!route.routeId) continue;

    polylineRoutes.set(`${polylineId}::${route.routeId}`, { polylineId, route });
  }

  return [...polylineRoutes.values()];
}

function groupPolylineVertices(polylineRows: CsvRow[]): Map<string, GtfsPolylineVertex[]> {
  const verticesById = new Map<string, GtfsPolylineVertex[]>();

  for (const row of polylineRows) {
    const polylineId = String(row["shape_id"] || "").trim();
    const lat = Number(row["shape_pt_lat"]);
    const lng = Number(row["shape_pt_lon"]);
    const sequence = Number(row["shape_pt_sequence"]);
    if (!polylineId || !Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    if (!(((lng)
    >= NYC_BBOX.minLng &&
    (lng)
        <= NYC_BBOX.maxLng &&
    (lat)
        >= NYC_BBOX.minLat &&
    (lat)
        <= NYC_BBOX.maxLat))) continue;
    const vertices = verticesById.get(polylineId);
    if (vertices) vertices.push({ lat, lng, sequence });
    else verticesById.set(polylineId, [{ lat, lng, sequence }]);
  }

  for (const points of verticesById.values()) {
    points.sort((left, right) => left.sequence - right.sequence);
  }

  return verticesById;
}

export function buildCanonicalFeatureCollection({
  routesRows,
  tripsRows,
  polylineRows,
}: BuildCanonicalInput): CanonicalFeatureCollection {
  const routesByRawId = buildRoutesById(routesRows);
  const polylineRoutes = buildPolylineRoutes(tripsRows, routesByRawId);
  const verticesById = groupPolylineVertices(polylineRows);
  const features: CanonicalFeature[] = [];

  for (const { polylineId, route } of polylineRoutes) {
    const points = verticesById.get(polylineId);
    if (!points || points.length < 2) continue;

    const coordinates: Position[] = points.map((point): Position => [point.lng, point.lat]);
    features.push({
      type: "Feature",
      properties: {
        route_id: route.routeId,
        display_route: route.displayRoute || route.routeId,
        "shape_id": polylineId,
        color: route.color,
      },
      geometry: {
        type: "LineString",
        coordinates,
      },
    });
  }

  const dedupedFeatures = dedupeFeaturesByRouteAndGeometry(features);

  dedupedFeatures.sort((left, right) => {
    const routeCompare = left.properties.route_id.localeCompare(
      right.properties.route_id,
      "en",
      { numeric: true },
    );
    if (routeCompare !== 0) return routeCompare;
    return left.properties["shape_id"].localeCompare(right.properties["shape_id"], "en", {
      numeric: true,
    });
  });

  return {
    type: "FeatureCollection",
    metadata: {
      source: GTFS_URL,
      generated_at: new Date().toISOString(),
      canonical_hash_basis: "routes.txt,trips.txt,shapes.txt",
      phase: "phase-1-canonical-gtfs-regeneration",
      dedupe: {
        strategy: "route-plus-rounded-geometry",
        input_features: features.length,
        dropped_features: features.length - dedupedFeatures.length,
      },
    },
    features: dedupedFeatures,
  };
}

function geometrySignature(coordinates: Position[]): string {
  return coordinates
    .map(([lng, lat]) => `${lng.toFixed(5)},${lat.toFixed(5)}`)
    .join(";");
}

function dedupeFeaturesByRouteAndGeometry(features: CanonicalFeature[]): CanonicalFeature[] {
  const seen = new Set<string>();
  const deduped: CanonicalFeature[] = [];

  for (const feature of features) {
    const key = `${feature.properties.route_id}|${geometrySignature(
      feature.geometry.coordinates,
    )}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(feature);
  }

  return deduped;
}

function featureLengthKm(feature: CanonicalFeature): number {
  let meters = 0;
  const coordinates = feature.geometry.coordinates;
  for (let i = 0; i < coordinates.length - 1; i++) {
    meters += distanceMeters(coordinates[i], coordinates[i + 1]);
  }
  return meters / 1000;
}

function distanceMeters(a: Position, b: Position): number {
  const radius = 6371000;
  const lat1 = (a[1] * Math.PI) / 180;
  const lat2 = (b[1] * Math.PI) / 180;
  const dLat = lat2 - lat1;
  const dLng = ((b[0] - a[0]) * Math.PI) / 180;
  const x =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * radius * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

function requireCanonicalLine(feature: CanonicalFeature): Position[] {
  if (feature.type !== "Feature" || feature.geometry.type !== "LineString") {
    throw new Error(`Invalid canonical feature for route ${feature.properties.route_id || "unknown"}.`);
  }
  const routeId = feature.properties.route_id;
  const polylineId = feature.properties["shape_id"];
  const color = feature.properties.color;
  const coordinates = feature.geometry.coordinates;
  if (!routeId || !polylineId || !color || !Array.isArray(coordinates) || coordinates.length < 2) {
    throw new Error(`Invalid canonical feature for route ${routeId || "unknown"}.`);
  }
  return coordinates;
}

function extendCoordinateBbox(
  bbox: CoordinateBbox,
  coordinates: Position[],
  routeId: RouteId,
  polylineId: string,
): void {
  for (const [lng, lat] of coordinates) {
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
      throw new Error(`Invalid coordinate in ${routeId}/${polylineId}.`);
    }
    bbox.minLng = Math.min(bbox.minLng, lng);
    bbox.maxLng = Math.max(bbox.maxLng, lng);
    bbox.minLat = Math.min(bbox.minLat, lat);
    bbox.maxLat = Math.max(bbox.maxLat, lat);
  }
}

function currentNetworkFeatureCount(text: string): number {
  const parsed: unknown = JSON.parse(text);
  if (parsed == null || Array.isArray(parsed)) return 0;
  const counted = Object.assign({ features: [] }, parsed);
  return Array.isArray(counted.features) ? counted.features.length : 0;
}

function assertNycEnvelope(bbox: CoordinateBbox): void {
  if (
    bbox.minLng < NYC_BBOX.minLng ||
    bbox.maxLng > NYC_BBOX.maxLng ||
    bbox.minLat < NYC_BBOX.minLat ||
    bbox.maxLat > NYC_BBOX.maxLat
  ) {
    throw new Error(
      `Canonical output bbox outside NYC envelope: ${[
        bbox.minLng,
        bbox.minLat,
        bbox.maxLng,
        bbox.maxLat,
      ].join(", ")}`,
    );
  }
}

export function validateFeatureCollection(collection: CanonicalFeatureCollection): Map<RouteId, RouteStats> {
  if (collection.type !== "FeatureCollection") {
    throw new Error("Canonical output is not a FeatureCollection.");
  }
  if (!Array.isArray(collection.features) || collection.features.length === 0) {
    throw new Error("Canonical output has no features.");
  }

  const byRoute = new Map<RouteId, RouteStats>();
  const bbox: CoordinateBbox = {
    minLng: Infinity,
    maxLng: -Infinity,
    minLat: Infinity,
    maxLat: -Infinity,
  };

  for (const feature of collection.features) {
    const coordinates = requireCanonicalLine(feature);
    const routeId = feature.properties.route_id;
    extendCoordinateBbox(bbox, coordinates, routeId, feature.properties["shape_id"]);
    const current = byRoute.get(routeId) || { count: 0, km: 0 };
    current.count += 1;
    current.km += featureLengthKm(feature);
    byRoute.set(routeId, current);
  }

  const missing = EXPECTED_ROUTES.filter((route) => !byRoute.has(route));
  if (missing.length > 0) {
    throw new Error(`Expected routes missing from canonical output: ${missing.join(", ")}`);
  }
  assertNycEnvelope(bbox);
  if (existsSync(CURRENT_NETWORK_PATH)) {
    const currentCount = currentNetworkFeatureCount(readFileSync(CURRENT_NETWORK_PATH, "utf8"));
    const min = Math.floor(currentCount * 0.8);
    const max = Math.ceil(currentCount * 1.2);
    if (currentCount > 0 && (collection.features.length < min || collection.features.length > max)) {
      throw new Error(
        `Canonical feature count ${collection.features.length} is outside +/-20% of current ${currentCount}.`,
      );
    }
  }
  return byRoute;
}

export function sha256(buffer: Buffer): string {
  return createHash("sha256").update(buffer).digest("hex");
}

function printRouteSummary(byRoute: Map<RouteId, RouteStats>): void {
  console.log(`[gtfs] canonical routes: ${byRoute.size}`);
  for (const [route, stats] of [...byRoute.entries()].sort((left, right) =>
    left[0].localeCompare(right[0], "en", { numeric: true }),
  )) {
    console.log(
      `[gtfs]   route ${route.padStart(2, " ")}: ${String(stats.count).padStart(
        3,
        " ",
      )} shapes, ${stats.km.toFixed(1)}km total`,
    );
  }
}

async function main(): Promise<void> {
  mkdirSync(publicDir, { recursive: true });
  const zipBuffer = await ensureGtfsZip();
  const files = parseZipEntries(zipBuffer, [
    "routes.txt",
    "trips.txt",
    "shapes.txt",
  ]);

  const collection = buildCanonicalFeatureCollection({
    routesRows: parseCsv(requiredZipText(files, "routes.txt")),
    tripsRows: parseCsv(requiredZipText(files, "trips.txt")),
    polylineRows: parseCsv(requiredZipText(files, "shapes.txt")),
  });
  collection.metadata.gtfs_zip_sha256 = sha256(zipBuffer);

  const byRoute = validateFeatureCollection(collection);
  const serializedCollection = `${JSON.stringify(collection)}\n`;
  writeFileSync(OUTPUT_PATH, serializedCollection);
  console.log(`[gtfs] wrote ${OUTPUT_PATH}`);
  console.log(`[gtfs] features: ${collection.features.length}`);
  printRouteSummary(byRoute);
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`[gtfs] ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  });
}
