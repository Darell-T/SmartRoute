#!/usr/bin/env node

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";
import { createHash } from "node:crypto";
import { MTA_ROUTE_COLORS } from "./build/mta-colors.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const publicDir = resolve(frontendRoot, "public");
const cacheDir = resolve(frontendRoot, ".gtfs-cache");

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
];

// Single source of truth lives in lib/mta-colors.json.
const ROUTE_COLOR_FALLBACKS = MTA_ROUTE_COLORS;

function normalizeRouteId(value) {
  const route = String(value || "").trim().toUpperCase();
  if (route === "6D") return "6X";
  if (route === "7D") return "7X";
  if (route === "FD") return "FX";
  if (route === "FS" || route === "GS" || route === "H") return "S";
  if (route === "SIR") return "SI";
  return route;
}

function normalizeColor(value, routeId) {
  const raw = String(value || "").trim().replace(/^#/, "");
  if (/^[0-9a-fA-F]{6}$/.test(raw)) return `#${raw.toUpperCase()}`;
  return ROUTE_COLOR_FALLBACKS[routeId] || "#A7A9AC";
}

function readUInt16(buffer, offset) {
  return buffer.readUInt16LE(offset);
}

function readUInt32(buffer, offset) {
  return buffer.readUInt32LE(offset);
}

function parseZipEntries(zipBuffer, wantedNames) {
  const wanted = new Set(wantedNames);
  const entries = new Map();
  let eocdOffset = -1;

  for (let i = zipBuffer.length - 22; i >= 0; i--) {
    if (readUInt32(zipBuffer, i) === 0x06054b50) {
      eocdOffset = i;
      break;
    }
  }

  if (eocdOffset < 0) {
    throw new Error("Could not find ZIP end-of-central-directory record.");
  }

  const centralDirectorySize = readUInt32(zipBuffer, eocdOffset + 12);
  const centralDirectoryOffset = readUInt32(zipBuffer, eocdOffset + 16);
  let offset = centralDirectoryOffset;
  const end = centralDirectoryOffset + centralDirectorySize;

  while (offset < end) {
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
      const dataOffset =
        localHeaderOffset + 30 + localNameLength + localExtraLength;
      const compressed = zipBuffer.subarray(
        dataOffset,
        dataOffset + compressedSize,
      );
      let data;
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
      entries.set(name, data.toString("utf8").replace(/^\uFEFF/, ""));
    }

    offset += 46 + fileNameLength + extraLength + commentLength;
  }

  for (const name of wanted) {
    if (!entries.has(name)) {
      throw new Error(`GTFS zip did not include required file: ${name}`);
    }
  }

  return entries;
}

function parseCsv(text) {
  const rows = [];
  let row = [];
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
    const record = {};
    header.forEach((key, index) => {
      record[key] = values[index] ?? "";
    });
    return record;
  });
}

async function ensureGtfsZip() {
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

function buildRoutesById(routesRows) {
  const routes = new Map();
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

function buildShapeRoutes(tripsRows, routesByRawId) {
  const shapeRoutes = new Map();

  for (const row of tripsRows) {
    const shapeId = String(row.shape_id || "").trim();
    const rawRouteId = String(row.route_id || "").trim();
    if (!shapeId || !rawRouteId) continue;

    const route = routesByRawId.get(rawRouteId) || {
      rawRouteId,
      routeId: normalizeRouteId(rawRouteId),
      displayRoute: normalizeRouteId(rawRouteId),
      color: normalizeColor("", normalizeRouteId(rawRouteId)),
    };
    if (!route.routeId) continue;

    const key = `${shapeId}::${route.routeId}`;
    shapeRoutes.set(key, { shapeId, route });
  }

  return [...shapeRoutes.values()];
}

function groupShapePoints(shapesRows) {
  const shapes = new Map();

  for (const row of shapesRows) {
    const shapeId = String(row.shape_id || "").trim();
    const lat = Number(row.shape_pt_lat);
    const lng = Number(row.shape_pt_lon);
    const sequence = Number(row.shape_pt_sequence);
    if (!shapeId || !Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    if (
      lng < NYC_BBOX.minLng ||
      lng > NYC_BBOX.maxLng ||
      lat < NYC_BBOX.minLat ||
      lat > NYC_BBOX.maxLat
    ) {
      continue;
    }
    if (!shapes.has(shapeId)) shapes.set(shapeId, []);
    shapes.get(shapeId).push({ lat, lng, sequence });
  }

  for (const points of shapes.values()) {
    points.sort((a, b) => a.sequence - b.sequence);
  }

  return shapes;
}

function buildCanonicalFeatureCollection({ routesRows, tripsRows, shapesRows }) {
  const routesByRawId = buildRoutesById(routesRows);
  const shapeRoutes = buildShapeRoutes(tripsRows, routesByRawId);
  const shapes = groupShapePoints(shapesRows);
  const features = [];

  for (const { shapeId, route } of shapeRoutes) {
    const points = shapes.get(shapeId);
    if (!points || points.length < 2) continue;

    const coordinates = points.map((point) => [point.lng, point.lat]);
    features.push({
      type: "Feature",
      properties: {
        route_id: route.routeId,
        display_route: route.displayRoute || route.routeId,
        shape_id: shapeId,
        color: route.color,
      },
      geometry: {
        type: "LineString",
        coordinates,
      },
    });
  }

  const dedupedFeatures = dedupeFeaturesByRouteAndGeometry(features);

  dedupedFeatures.sort((a, b) => {
    const routeCompare = a.properties.route_id.localeCompare(
      b.properties.route_id,
      "en",
      { numeric: true },
    );
    if (routeCompare !== 0) return routeCompare;
    return a.properties.shape_id.localeCompare(b.properties.shape_id, "en", {
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

function geometrySignature(coordinates) {
  return coordinates
    .map(([lng, lat]) => `${lng.toFixed(5)},${lat.toFixed(5)}`)
    .join(";");
}

function dedupeFeaturesByRouteAndGeometry(features) {
  const seen = new Set();
  const deduped = [];

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

function featureLengthKm(feature) {
  let meters = 0;
  const coordinates = feature.geometry.coordinates;
  for (let i = 0; i < coordinates.length - 1; i++) {
    meters += distanceMeters(coordinates[i], coordinates[i + 1]);
  }
  return meters / 1000;
}

function distanceMeters(a, b) {
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

function validateFeatureCollection(collection) {
  if (collection.type !== "FeatureCollection") {
    throw new Error("Canonical output is not a FeatureCollection.");
  }
  if (!Array.isArray(collection.features) || collection.features.length === 0) {
    throw new Error("Canonical output has no features.");
  }

  const byRoute = new Map();
  let minLng = Infinity;
  let maxLng = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;

  for (const feature of collection.features) {
    const routeId = feature?.properties?.route_id;
    const shapeId = feature?.properties?.shape_id;
    const color = feature?.properties?.color;
    const coordinates = feature?.geometry?.coordinates;

    if (
      feature?.type !== "Feature" ||
      feature?.geometry?.type !== "LineString" ||
      !routeId ||
      !shapeId ||
      !color ||
      !Array.isArray(coordinates) ||
      coordinates.length < 2
    ) {
      throw new Error(`Invalid canonical feature for route ${routeId || "unknown"}.`);
    }

    for (const [lng, lat] of coordinates) {
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
        throw new Error(`Invalid coordinate in ${routeId}/${shapeId}.`);
      }
      minLng = Math.min(minLng, lng);
      maxLng = Math.max(maxLng, lng);
      minLat = Math.min(minLat, lat);
      maxLat = Math.max(maxLat, lat);
    }

    const current = byRoute.get(routeId) || { count: 0, km: 0 };
    current.count += 1;
    current.km += featureLengthKm(feature);
    byRoute.set(routeId, current);
  }

  const missing = EXPECTED_ROUTES.filter((route) => !byRoute.has(route));
  if (missing.length > 0) {
    throw new Error(`Expected routes missing from canonical output: ${missing.join(", ")}`);
  }

  if (
    minLng < NYC_BBOX.minLng ||
    maxLng > NYC_BBOX.maxLng ||
    minLat < NYC_BBOX.minLat ||
    maxLat > NYC_BBOX.maxLat
  ) {
    throw new Error(
      `Canonical output bbox outside NYC envelope: ${[
        minLng,
        minLat,
        maxLng,
        maxLat,
      ].join(", ")}`,
    );
  }

  if (existsSync(CURRENT_NETWORK_PATH)) {
    const current = JSON.parse(readFileSync(CURRENT_NETWORK_PATH, "utf8"));
    const currentCount = current.features?.length ?? 0;
    const nextCount = collection.features.length;
    const min = Math.floor(currentCount * 0.8);
    const max = Math.ceil(currentCount * 1.2);
    if (currentCount > 0 && (nextCount < min || nextCount > max)) {
      throw new Error(
        `Canonical feature count ${nextCount} is outside +/-20% of current ${currentCount}.`,
      );
    }
  }

  return byRoute;
}

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

function printRouteSummary(byRoute) {
  console.log(`[gtfs] canonical routes: ${byRoute.size}`);
  for (const [route, stats] of [...byRoute.entries()].sort((a, b) =>
    a[0].localeCompare(b[0], "en", { numeric: true }),
  )) {
    console.log(
      `[gtfs]   route ${route.padStart(2, " ")}: ${String(stats.count).padStart(
        3,
        " ",
      )} shapes, ${stats.km.toFixed(1)}km total`,
    );
  }
}

async function main() {
  mkdirSync(publicDir, { recursive: true });
  const zipBuffer = await ensureGtfsZip();
  const files = parseZipEntries(zipBuffer, [
    "routes.txt",
    "trips.txt",
    "shapes.txt",
  ]);

  const collection = buildCanonicalFeatureCollection({
    routesRows: parseCsv(files.get("routes.txt")),
    tripsRows: parseCsv(files.get("trips.txt")),
    shapesRows: parseCsv(files.get("shapes.txt")),
  });
  collection.metadata.gtfs_zip_sha256 = sha256(zipBuffer);

  const byRoute = validateFeatureCollection(collection);
  const serializedCollection = `${JSON.stringify(collection)}\n`;
  writeFileSync(OUTPUT_PATH, serializedCollection);
  console.log(`[gtfs] wrote ${OUTPUT_PATH}`);
  console.log(`[gtfs] features: ${collection.features.length}`);
  printRouteSummary(byRoute);
}

main().catch((error) => {
  console.error(`[gtfs] ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
