#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const publicDir = resolve(frontendRoot, "public");
const debugDir = resolve(publicDir, "debug");

const DEFAULT_FAMILY_VISUAL_PATH = resolve(
  publicDir,
  "subway-network.family-visual.geojson",
);
const DEFAULT_GROUPS_PATH = resolve(
  publicDir,
  "subway-network.corridor-groups.json",
);
const DEFAULT_VISUAL_PATH = resolve(
  publicDir,
  "subway-network.group-visual.geojson",
);
const DEFAULT_AUDIT_PATH = resolve(debugDir, "corridor-groups.audit.json");
const DEFAULT_ENDPOINTS_PATH = resolve(debugDir, "group-endpoints.geojson");
const DEFAULT_ROUTE_2_RAW_EDGES_PATH = resolve(
  debugDir,
  "route-2.edge-visual.raw.geojson",
);
const DEFAULT_CANONICAL_PATH = resolve(
  publicDir,
  "subway-network.canonical.geojson",
);
const DEFAULT_GTFS_ZIP_PATH = resolve(
  frontendRoot,
  ".gtfs-cache",
  "google_transit.zip",
);

const CORRIDOR_WIDTH_METERS = 50;
const MIN_PARALLEL_COSINE = 0.8;
const SAMPLE_SPACING_METERS = 90;
const MIN_GROUP_SPAN_METERS = 120;
const MIN_RENDER_SEGMENT_METERS = 8;
const FALLBACK_VISUAL_EDGE_METERS = 650;
const VISUAL_EDGE_STITCH_TOLERANCE_METERS = 12;
const ROUTE_2_EDGE_STITCH_EPSILON_METERS = 1.5;

// Lane spacing baked into geometry. Adjacent slots are LANE_WIDTH_METERS apart
// in geographic space (not screen space) — invariant under zoom. Tune by
// taking a screenshot at zoom 14 of a known multi-route trunk and adjusting
// until bundle separation matches the MTA map.
const LANE_WIDTH_METERS = 6;

// Distance over which the perpendicular shift ramps from full lane offset
// down to zero at solo↔group transitions. Prevents visible jumps when a
// route branches off independent track. 30m matches the existing
// transitionLengthMeters on every override.
const TAPER_LENGTH_METERS = 30;

// Cross-family corridor overrides. Single source of truth lives in
// frontend/components/map/subway-corridor-overrides.json so this build script
// and the runtime TS module read identical data. When a route's representative
// line samples inside one of these bounding boxes, the route is forced into the
// override's bundle with the override's lane slot — regardless of family. This
// is how multi-family corridors (DeKalb/Atlantic, 6 Av trunk, etc.) get bundled.
const OVERRIDES_PATH = resolve(
  here,
  "../components/map/subway-corridor-overrides.json",
);
const MANUAL_CORRIDOR_OVERRIDES = JSON.parse(
  readFileSync(OVERRIDES_PATH, "utf8"),
);

function pointInsideOverrideBounds(point, bounds) {
  const [lng, lat] = point;
  return (
    lng >= bounds.minLng &&
    lng <= bounds.maxLng &&
    lat >= bounds.minLat &&
    lat <= bounds.maxLat
  );
}

function findManualOverrideAt(routeId, point) {
  for (const override of MANUAL_CORRIDOR_OVERRIDES) {
    if (!override.routeIds.includes(routeId)) continue;
    if (!pointInsideOverrideBounds(point, override.bounds)) continue;
    return override;
  }
  return null;
}

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

const FAMILY_ORDER = {
  "1-2-3": ["1", "2", "3"],
  "4-5-6": ["4", "5", "6"],
  7: ["7"],
  "A-C-E": ["A", "C", "E"],
  "B-D-F-M": ["B", "D", "F", "M"],
  "N-Q-R-W": ["N", "Q", "R", "W"],
  "J-Z": ["J", "Z"],
  G: ["G"],
  L: ["L"],
  S: ["S"],
  SI: ["SI"],
};

const VISUAL_ROUTE_NORMALIZATION = new Map([
  ["FX", "F"],
  ["7X", "7"],
  ["6X", "6"],
]);

// These services have GTFS direction, express, or terminal variants that are
// not separate visual branches in the product map. They stay in raw lineage
// metadata, but only one representative ribbon enters group derivation.
const SINGLE_BRANCH_VISUAL_ROUTES = new Set([
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "7",
  "A",
  "B",
  "C",
  "D",
  "E",
  "F",
  "G",
  "J",
  "L",
  "M",
  "N",
  "Q",
  "R",
  "S",
  "W",
  "Z",
  "SI",
]);

const ROUTE_Z_ORDER = {
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
  FX: 105,
  M: 110,
  B: 120,
  D: 130,
  N: 140,
  Q: 150,
  R: 160,
  W: 170,
  1: 180,
  4: 190,
  5: 200,
  6: 210,
  "6X": 220,
  2: 230,
  3: 240,
  7: 250,
  "7X": 260,
};

const DEBUG_BBOXES = [
  {
    name: "canal-broadway-lafayette",
    bounds: [-74.0105, 40.714, -73.9885, 40.7305],
  },
  {
    name: "chambers-park-place-wtc",
    bounds: [-74.0175, 40.708, -74.001, 40.7165],
  },
  {
    name: "lower-manhattan-river-crossings",
    bounds: [-74.025, 40.685, -73.965, 40.718],
  },
  {
    name: "dekalb-atlantic-barclays",
    bounds: [-74.0005, 40.674, -73.969, 40.6935],
  },
  {
    name: "eastern-pkwy",
    bounds: [-73.98, 40.661, -73.925, 40.6785],
  },
  {
    name: "coney-island-stillwell",
    bounds: [-74.004, 40.568, -73.968, 40.586],
  },
  {
    name: "bedford-park-blvd",
    bounds: [-73.898, 40.867, -73.875, 40.879],
  },
  {
    name: "59-lex-5av-57",
    bounds: [-73.988, 40.758, -73.952, 40.7715],
  },
];

const ROUTE_2_FOCUS_BBOXES = [
  {
    id: "chambers-park-place-wtc",
    bounds: [-74.0175, 40.708, -74.001, 40.7165],
  },
  {
    id: "nostrand-flatbush-2",
    bounds: [-73.965, 40.632, -73.935, 40.675],
  },
  {
    id: "145st-malcolm-x",
    bounds: [-73.952, 40.815, -73.93, 40.83],
  },
];

function normalizeRouteId(value) {
  const routeId = String(value ?? "")
    .trim()
    .toUpperCase();
  if (routeId === "6D") return "6X";
  if (routeId === "7D") return "7X";
  if (routeId === "FD") return "FX";
  if (routeId === "FS" || routeId === "GS" || routeId === "H") return "S";
  if (routeId === "SIR") return "SI";
  return routeId;
}

function visualRouteIdFor(value) {
  const routeId = normalizeRouteId(value);
  return VISUAL_ROUTE_NORMALIZATION.get(routeId) ?? routeId;
}

function visualRouteNormalizationReason(rawRouteId, visualRouteId) {
  if (rawRouteId === visualRouteId) return "identity";
  if (VISUAL_ROUTE_NORMALIZATION.get(rawRouteId) === visualRouteId)
    return "express-variant-collapse";
  return "normalized-route-id";
}

function canonicalDirectionIdForShape(shapeId) {
  const match = String(shapeId ?? "").match(/\.\.([NS])/i);
  return match ? match[1].toUpperCase() : null;
}

function sanitize(value) {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-|-$/g, "");
}

function isValidCoordinate(value) {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  );
}

function distanceMeters(from, to) {
  const radius = 6371000;
  const lat1 = (from[1] * Math.PI) / 180;
  const lat2 = (to[1] * Math.PI) / 180;
  const dLat = lat2 - lat1;
  const dLng = ((to[0] - from[0]) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * radius * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function meterVector(from, to) {
  const averageLat = (((from[1] + to[1]) / 2) * Math.PI) / 180;
  const metersPerDegreeLat = 111320;
  const metersPerDegreeLng = metersPerDegreeLat * Math.cos(averageLat);
  return [
    (to[0] - from[0]) * metersPerDegreeLng,
    (to[1] - from[1]) * metersPerDegreeLat,
  ];
}

function lineLengthMeters(coordinates) {
  let total = 0;
  for (let index = 1; index < coordinates.length; index += 1) {
    total += distanceMeters(coordinates[index - 1], coordinates[index]);
  }
  return total;
}

function cumulativeDistances(coordinates) {
  const distances = [0];
  for (let index = 1; index < coordinates.length; index += 1) {
    distances.push(
      distances[index - 1] +
        distanceMeters(coordinates[index - 1], coordinates[index]),
    );
  }
  return distances;
}

function interpolateCoordinate(from, to, t) {
  return [from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t];
}

function pointAtDistance(line, distance) {
  const target = Math.max(0, Math.min(line.lengthMeters, distance));
  if (target <= 0) return line.coordinates[0];
  if (target >= line.lengthMeters)
    return line.coordinates[line.coordinates.length - 1];

  for (let index = 1; index < line.coordinates.length; index += 1) {
    const previous = line.cumulative[index - 1];
    const current = line.cumulative[index];
    if (current >= target) {
      const segmentLength = current - previous;
      const t = segmentLength === 0 ? 0 : (target - previous) / segmentLength;
      return interpolateCoordinate(
        line.coordinates[index - 1],
        line.coordinates[index],
        t,
      );
    }
  }

  return line.coordinates[line.coordinates.length - 1];
}

function tangentAtDistance(line, distance) {
  const target = Math.max(0, Math.min(line.lengthMeters, distance));
  let index = 1;
  while (index < line.cumulative.length && line.cumulative[index] < target)
    index += 1;
  index = Math.max(1, Math.min(index, line.coordinates.length - 1));
  const vector = meterVector(
    line.coordinates[index - 1],
    line.coordinates[index],
  );
  const length = Math.hypot(vector[0], vector[1]);
  if (length === 0) return [1, 0];
  return [vector[0] / length, vector[1] / length];
}

const METERS_PER_DEGREE_LAT = 111_320;

function metersPerDegreeLng(lat) {
  return METERS_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180);
}

/**
 * Given a tangent expressed in lng/lat units (degree-space) and the local
 * latitude, return the perpendicular unit direction (rotated 90° clockwise
 * = right-hand of travel) scaled so its magnitude equals 1 meter of
 * perpendicular displacement, in degree-space.
 */
function perpendicularDegreesPerMeter(tangent, lat) {
  const mPerLng = metersPerDegreeLng(lat);
  const tx_m = tangent[0] * mPerLng;
  const ty_m = tangent[1] * METERS_PER_DEGREE_LAT;
  const len_m = Math.hypot(tx_m, ty_m);
  if (len_m < 1e-9) return [0, 0];
  // Rotate 90° clockwise: (x, y) -> (y, -x). Right-hand of travel direction.
  const px_m = ty_m / len_m;
  const py_m = -tx_m / len_m;
  return [px_m / mPerLng, py_m / METERS_PER_DEGREE_LAT];
}

/**
 * Shift a single coordinate perpendicular to the local tangent by
 * `offsetMeters`. Positive offsetMeters = right of travel direction.
 */
function shiftCoordinatePerpendicular(coord, tangent, offsetMeters) {
  if (Math.abs(offsetMeters) < 1e-9) return [coord[0], coord[1]];
  const [pdLng, pdLat] = perpendicularDegreesPerMeter(tangent, coord[1]);
  return [coord[0] + pdLng * offsetMeters, coord[1] + pdLat * offsetMeters];
}

/**
 * Bake a perpendicular offset into every coordinate of a polyline. Tangent at
 * coordinate i is computed from the (i-1 → i+1) chord so adjacent coordinates
 * agree on the perpendicular direction at curves.
 *
 * `taperFn(distanceFromStart, distanceFromEnd, totalLength)` returns a
 * scalar in [0, 1] applied to the offset at that coordinate. Pass `null`
 * for no taper (full offset everywhere).
 */
function bakeLaneOffsetIntoPolyline(coords, offsetMeters, taperFn) {
  if (coords.length < 2 || Math.abs(offsetMeters) < 1e-9) {
    return coords.map((c) => [c[0], c[1]]);
  }

  // Pre-compute cumulative meter-distances along the polyline for the taper.
  const cumMeters = [0];
  for (let i = 1; i < coords.length; i += 1) {
    const dx =
      (coords[i][0] - coords[i - 1][0]) * metersPerDegreeLng(coords[i][1]);
    const dy = (coords[i][1] - coords[i - 1][1]) * METERS_PER_DEGREE_LAT;
    cumMeters.push(cumMeters[i - 1] + Math.hypot(dx, dy));
  }
  const totalMeters = cumMeters[cumMeters.length - 1];

  const out = [];
  for (let i = 0; i < coords.length; i += 1) {
    const prev = coords[Math.max(0, i - 1)];
    const next = coords[Math.min(coords.length - 1, i + 1)];
    const tangent = [next[0] - prev[0], next[1] - prev[1]];

    const taperScale = taperFn
      ? taperFn(cumMeters[i], totalMeters - cumMeters[i], totalMeters)
      : 1;

    out.push(
      shiftCoordinatePerpendicular(
        coords[i],
        tangent,
        offsetMeters * taperScale,
      ),
    );
  }
  return out;
}

function buildTaperFn(isFirstInGroup, isLastInGroup) {
  // Middle of a bundle (no handoffs at either end): no taper.
  if (!isFirstInGroup && !isLastInGroup) return null;
  return (fromStart, fromEnd, _total) => {
    let scale = 1;
    if (isFirstInGroup) {
      const ramp = Math.min(1, fromStart / TAPER_LENGTH_METERS);
      scale = Math.min(scale, ramp);
    }
    if (isLastInGroup) {
      const ramp = Math.min(1, fromEnd / TAPER_LENGTH_METERS);
      scale = Math.min(scale, ramp);
    }
    return scale;
  };
}

function coordinateKey(coordinate, digits = 5) {
  return `${coordinate[0].toFixed(digits)},${coordinate[1].toFixed(digits)}`;
}

function collectionHash(collection) {
  return createHash("sha256").update(JSON.stringify(collection)).digest("hex");
}

function hashJson(value, length = 12) {
  return createHash("sha1")
    .update(JSON.stringify(value))
    .digest("hex")
    .slice(0, length);
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

  for (let index = zipBuffer.length - 22; index >= 0; index -= 1) {
    if (readUInt32(zipBuffer, index) === 0x06054b50) {
      eocdOffset = index;
      break;
    }
  }

  if (eocdOffset < 0)
    throw new Error("Could not find GTFS zip central directory.");

  const centralDirectorySize = readUInt32(zipBuffer, eocdOffset + 12);
  const centralDirectoryOffset = readUInt32(zipBuffer, eocdOffset + 16);
  let offset = centralDirectoryOffset;
  const end = centralDirectoryOffset + centralDirectorySize;

  while (offset < end) {
    if (readUInt32(zipBuffer, offset) !== 0x02014b50) {
      throw new Error("Malformed GTFS zip central directory.");
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
        throw new Error(`Malformed GTFS zip local header for ${name}.`);
      }
      const localNameLength = readUInt16(zipBuffer, localHeaderOffset + 26);
      const localExtraLength = readUInt16(zipBuffer, localHeaderOffset + 28);
      const dataOffset =
        localHeaderOffset + 30 + localNameLength + localExtraLength;
      const compressed = zipBuffer.subarray(
        dataOffset,
        dataOffset + compressedSize,
      );
      const data =
        compressionMethod === 0
          ? compressed
          : compressionMethod === 8
            ? inflateRawSync(compressed)
            : null;
      if (!data)
        throw new Error(
          `Unsupported GTFS zip compression method ${compressionMethod} for ${name}.`,
        );
      if (data.length !== uncompressedSize)
        throw new Error(`Unexpected GTFS zip size for ${name}.`);
      entries.set(name, data.toString("utf8").replace(/^\uFEFF/, ""));
    }

    offset += 46 + fileNameLength + extraLength + commentLength;
  }

  for (const name of wanted) {
    if (!entries.has(name))
      throw new Error(`GTFS zip missing required file: ${name}`);
  }

  return entries;
}

function parseCsvLine(line) {
  const values = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (quoted) {
      if (char === '"') {
        if (line[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        field += char;
      }
      continue;
    }

    if (char === '"') quoted = true;
    else if (char === ",") {
      values.push(field);
      field = "";
    } else {
      field += char;
    }
  }

  values.push(field);
  return values;
}

function parseCsvRecords(text) {
  const lines = text.split(/\r?\n/).filter(Boolean);
  if (lines.length === 0) return [];
  const header = parseCsvLine(lines[0]);
  return lines.slice(1).map((line) => {
    const values = parseCsvLine(line);
    const record = {};
    header.forEach((key, index) => {
      record[key] = values[index] ?? "";
    });
    return record;
  });
}

function routeFamily(routeId, fallback) {
  const visualRouteId = visualRouteIdFor(routeId);
  return (
    fallback ||
    Object.entries(FAMILY_ORDER).find(([, routes]) =>
      routes.includes(visualRouteId),
    )?.[0] ||
    visualRouteId
  );
}

function routeSortKey(visualFamily, routeId) {
  const order = FAMILY_ORDER[visualFamily] ?? [];
  const index = order.indexOf(routeId);
  return index >= 0
    ? index
    : 1000 + routeId.localeCompare(routeId, "en", { numeric: true });
}

function laneOrderFor(visualFamily, routes) {
  return [...new Set([...routes].map(visualRouteIdFor).filter(Boolean))].sort(
    (left, right) => {
      const leftOrder = routeSortKey(visualFamily, left);
      const rightOrder = routeSortKey(visualFamily, right);
      if (leftOrder !== rightOrder) return leftOrder - rightOrder;
      return left.localeCompare(right, "en", { numeric: true });
    },
  );
}

function laneSlotByRoute(laneOrder) {
  const center = (laneOrder.length - 1) / 2;
  const slots = new Map();
  laneOrder.forEach((routeId, index) => {
    slots.set(routeId, index - center);
  });
  return slots;
}

function stableFamilyLaneSlot(visualFamily, routeId) {
  const visualRouteId = visualRouteIdFor(routeId);
  const familyOrder = (FAMILY_ORDER[visualFamily] ?? [visualRouteId])
    .map(visualRouteIdFor)
    .filter(Boolean);
  const index = familyOrder.indexOf(visualRouteId);
  if (index < 0) return 0;
  return index - (familyOrder.length - 1) / 2;
}

function effectiveRenderKeyFor(properties) {
  const visualRouteId =
    properties.visual_route_id ?? visualRouteIdFor(properties.route_id);
  return [
    visualRouteId,
    properties.visual_branch_id,
    `lane:${Number(properties.visual_lane_slot ?? 0)}`,
    `z:${Number(properties.visual_z_order ?? 0)}`,
    `color:${properties.color ?? ""}`,
    `kind:${properties.segment_kind ?? "solo"}`,
    "style:subway-ribbon-v1",
  ].join("|");
}

function cloneCoordinate(coordinate) {
  return [coordinate[0], coordinate[1]];
}

function cloneCoordinates(coordinates) {
  return coordinates.map(cloneCoordinate);
}

function firstCoordinate(feature) {
  return feature.geometry.coordinates[0];
}

function lastCoordinate(feature) {
  return feature.geometry.coordinates[feature.geometry.coordinates.length - 1];
}

function routeBranchKeyForProperties(properties) {
  return `${properties.visual_route_id ?? visualRouteIdFor(properties.route_id)}|${properties.visual_branch_id}`;
}

function isRoute2Properties(properties) {
  return (
    visualRouteIdFor(properties.visual_route_id ?? properties.route_id) === "2"
  );
}

function unionStrings(left = [], right = []) {
  return [...new Set([...left, ...right].map(String).filter(Boolean))];
}

function expandBounds(bounds, meters) {
  const averageLat = (((bounds.minLat + bounds.maxLat) / 2) * Math.PI) / 180;
  const latDelta = meters / 111320;
  const lngDelta = meters / (111320 * Math.cos(averageLat));
  return {
    minLng: bounds.minLng - lngDelta,
    minLat: bounds.minLat - latDelta,
    maxLng: bounds.maxLng + lngDelta,
    maxLat: bounds.maxLat + latDelta,
  };
}

function pointInsideBounds(point, bounds) {
  return (
    point[0] >= bounds.minLng &&
    point[0] <= bounds.maxLng &&
    point[1] >= bounds.minLat &&
    point[1] <= bounds.maxLat
  );
}

function pointInsideDebugBounds(point, bounds) {
  return (
    point[0] >= bounds[0] &&
    point[0] <= bounds[2] &&
    point[1] >= bounds[1] &&
    point[1] <= bounds[3]
  );
}

function bboxNameForCoordinate(coordinate) {
  return (
    DEBUG_BBOXES.find((bbox) => pointInsideDebugBounds(coordinate, bbox.bounds))
      ?.name ?? null
  );
}

function stationIdForStopId(stopId) {
  const value = String(stopId ?? "").trim();
  return value.replace(/[NS]$/i, "");
}

function stopPairKey(fromStopId, toStopId) {
  return [stationIdForStopId(fromStopId), stationIdForStopId(toStopId)]
    .sort()
    .join("~");
}

function boundsFor(coordinates) {
  let minLng = Number.POSITIVE_INFINITY;
  let minLat = Number.POSITIVE_INFINITY;
  let maxLng = Number.NEGATIVE_INFINITY;
  let maxLat = Number.NEGATIVE_INFINITY;
  for (const [lng, lat] of coordinates) {
    minLng = Math.min(minLng, lng);
    minLat = Math.min(minLat, lat);
    maxLng = Math.max(maxLng, lng);
    maxLat = Math.max(maxLat, lat);
  }
  return { minLng, minLat, maxLng, maxLat };
}

function pointToSegmentMatch(point, tangent, start, end) {
  const averageLat = (((point[1] + start[1] + end[1]) / 3) * Math.PI) / 180;
  const metersPerDegreeLat = 111320;
  const metersPerDegreeLng = metersPerDegreeLat * Math.cos(averageLat);
  const px = point[0] * metersPerDegreeLng;
  const py = point[1] * metersPerDegreeLat;
  const sx = start[0] * metersPerDegreeLng;
  const sy = start[1] * metersPerDegreeLat;
  const ex = end[0] * metersPerDegreeLng;
  const ey = end[1] * metersPerDegreeLat;
  const dx = ex - sx;
  const dy = ey - sy;
  const denominator = dx * dx + dy * dy;
  const t =
    denominator === 0
      ? 0
      : Math.max(
          0,
          Math.min(1, ((px - sx) * dx + (py - sy) * dy) / denominator),
        );
  const nearestX = sx + dx * t;
  const nearestY = sy + dy * t;
  const segmentLength = Math.hypot(dx, dy);
  const cosine =
    segmentLength === 0
      ? 0
      : Math.abs((tangent[0] * dx + tangent[1] * dy) / segmentLength);
  return {
    distance: Math.hypot(px - nearestX, py - nearestY),
    cosine,
  };
}

function projectPointToLineDistance(point, line) {
  let best = {
    distance: Number.POSITIVE_INFINITY,
    distanceAlong: 0,
  };

  for (let index = 1; index < line.coordinates.length; index += 1) {
    const start = line.coordinates[index - 1];
    const end = line.coordinates[index];
    const averageLat = (((point[1] + start[1] + end[1]) / 3) * Math.PI) / 180;
    const metersPerDegreeLat = 111320;
    const metersPerDegreeLng = metersPerDegreeLat * Math.cos(averageLat);
    const px = point[0] * metersPerDegreeLng;
    const py = point[1] * metersPerDegreeLat;
    const sx = start[0] * metersPerDegreeLng;
    const sy = start[1] * metersPerDegreeLat;
    const ex = end[0] * metersPerDegreeLng;
    const ey = end[1] * metersPerDegreeLat;
    const dx = ex - sx;
    const dy = ey - sy;
    const denominator = dx * dx + dy * dy;
    const t =
      denominator === 0
        ? 0
        : Math.max(
            0,
            Math.min(1, ((px - sx) * dx + (py - sy) * dy) / denominator),
          );
    const nearestX = sx + dx * t;
    const nearestY = sy + dy * t;
    const distance = Math.hypot(px - nearestX, py - nearestY);
    const segmentLength = line.cumulative[index] - line.cumulative[index - 1];
    const distanceAlong = line.cumulative[index - 1] + segmentLength * t;
    if (distance < best.distance) best = { distance, distanceAlong };
  }

  return best;
}

function nearestParallelDistance(point, tangent, line) {
  if (
    !pointInsideBounds(
      point,
      expandBounds(line.bounds, CORRIDOR_WIDTH_METERS * 1.5),
    )
  ) {
    return { distance: Number.POSITIVE_INFINITY, cosine: 0 };
  }

  let best = { distance: Number.POSITIVE_INFINITY, cosine: 0 };
  for (let index = 1; index < line.coordinates.length; index += 1) {
    const match = pointToSegmentMatch(
      point,
      tangent,
      line.coordinates[index - 1],
      line.coordinates[index],
    );
    if (match.distance < best.distance) best = match;
  }
  return best;
}

function loadCanonicalShapeLines() {
  if (!existsSync(DEFAULT_CANONICAL_PATH)) return new Map();
  const canonical = JSON.parse(readFileSync(DEFAULT_CANONICAL_PATH, "utf8"));
  const shapes = new Map();
  for (const feature of canonical.features ?? []) {
    if (feature.geometry?.type !== "LineString") continue;
    const shapeId = String(feature.properties?.shape_id ?? "");
    const coordinates = feature.geometry.coordinates.filter(isValidCoordinate);
    if (!shapeId || coordinates.length < 2) continue;
    shapes.set(shapeId, {
      shapeId,
      routeId: normalizeRouteId(feature.properties?.route_id),
      visualRouteId: visualRouteIdFor(feature.properties?.route_id),
      coordinates,
      cumulative: cumulativeDistances(coordinates),
      lengthMeters: lineLengthMeters(coordinates),
      bounds: boundsFor(coordinates),
    });
  }
  return shapes;
}

function loadGtfsTextEntries() {
  if (!existsSync(DEFAULT_GTFS_ZIP_PATH)) return null;
  return parseZipEntries(readFileSync(DEFAULT_GTFS_ZIP_PATH), [
    "trips.txt",
    "stops.txt",
    "stop_times.txt",
  ]);
}

function buildStopsByStation(stopsText) {
  const stops = new Map();
  for (const row of parseCsvRecords(stopsText)) {
    const stopId = String(row.stop_id ?? "").trim();
    const stationId = String(
      row.parent_station || stationIdForStopId(stopId),
    ).trim();
    const lat = Number(row.stop_lat);
    const lng = Number(row.stop_lon);
    if (!stopId || !stationId || !Number.isFinite(lat) || !Number.isFinite(lng))
      continue;
    if (!stops.has(stationId) || String(row.location_type ?? "") === "1") {
      stops.set(stationId, {
        stop_id: stationId,
        stop_name: String(row.stop_name ?? stationId),
        coordinate: [lng, lat],
      });
    }
  }
  return stops;
}

function buildTripSelection(tripsText, selectedShapeIds) {
  const tripToShape = new Map();
  const routeByShape = new Map();
  for (const row of parseCsvRecords(tripsText)) {
    const shapeId = String(row.shape_id ?? "").trim();
    if (!selectedShapeIds.has(shapeId) || routeByShape.has(shapeId)) continue;
    const rawRouteId = normalizeRouteId(row.route_id);
    routeByShape.set(shapeId, rawRouteId);
    tripToShape.set(String(row.trip_id ?? ""), shapeId);
  }
  return { tripToShape, routeByShape };
}

function parseStopTimesForTrips(stopTimesText, tripToShape) {
  const stopRowsByShape = new Map();
  const lines = stopTimesText.split(/\r?\n/);
  if (lines.length === 0) return stopRowsByShape;
  const header = parseCsvLine(lines[0]);
  const tripIndex = header.indexOf("trip_id");
  const stopIndex = header.indexOf("stop_id");
  const sequenceIndex = header.indexOf("stop_sequence");

  for (let index = 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line) continue;
    const values = parseCsvLine(line);
    const shapeId = tripToShape.get(values[tripIndex]);
    if (!shapeId) continue;
    if (!stopRowsByShape.has(shapeId)) stopRowsByShape.set(shapeId, []);
    stopRowsByShape.get(shapeId).push({
      stop_id: stationIdForStopId(values[stopIndex]),
      stop_sequence: Number(values[sequenceIndex]),
    });
  }

  for (const rows of stopRowsByShape.values()) {
    rows.sort((left, right) => left.stop_sequence - right.stop_sequence);
  }

  return stopRowsByShape;
}

function buildShapeStopEdges({
  shapeLines,
  stopsByStation,
  stopRowsByShape,
  routeByShape,
}) {
  const edgeCandidatesByVisualRoutePair = new Map();
  const edgesByShape = new Map();

  for (const [shapeId, rows] of stopRowsByShape) {
    const shapeLine = shapeLines.get(shapeId);
    if (!shapeLine) continue;
    const rawRouteId = routeByShape.get(shapeId) ?? shapeLine.routeId;
    const visualRouteId = visualRouteIdFor(rawRouteId);
    const projectionCache = new Map();
    const stationRows = [];
    for (const row of rows) {
      const station = stopsByStation.get(row.stop_id);
      if (!station) continue;
      const previous = stationRows[stationRows.length - 1];
      if (previous?.stop_id === station.stop_id) continue;
      stationRows.push(station);
    }

    const shapeEdges = [];
    for (let index = 1; index < stationRows.length; index += 1) {
      const fromStop = stationRows[index - 1];
      const toStop = stationRows[index];
      const fromProjection =
        projectionCache.get(fromStop.stop_id) ??
        projectPointToLineDistance(fromStop.coordinate, shapeLine);
      const toProjection =
        projectionCache.get(toStop.stop_id) ??
        projectPointToLineDistance(toStop.coordinate, shapeLine);
      projectionCache.set(fromStop.stop_id, fromProjection);
      projectionCache.set(toStop.stop_id, toProjection);

      if (
        Math.abs(toProjection.distanceAlong - fromProjection.distanceAlong) <
        MIN_RENDER_SEGMENT_METERS
      )
        continue;
      const coordinates = sliceLine(
        shapeLine,
        fromProjection.distanceAlong,
        toProjection.distanceAlong,
      );
      if (coordinates.length < 2) continue;
      const pairKey = stopPairKey(fromStop.stop_id, toStop.stop_id);
      const visualEdgeId = `${sanitize(visualRouteId)}:${pairKey.replace("~", "-")}`;
      const candidate = {
        visual_edge_id: visualEdgeId,
        stop_pair_key: pairKey,
        raw_route_id: rawRouteId,
        visual_route_id: visualRouteId,
        shape_id: shapeId,
        canonical_direction_id: canonicalDirectionIdForShape(shapeId),
        from_stop_id: fromStop.stop_id,
        to_stop_id: toStop.stop_id,
        from_stop_name: fromStop.stop_name,
        to_stop_name: toStop.stop_name,
        edge_sequence: index - 1,
        coordinates,
        length_meters: lineLengthMeters(coordinates),
        projection_distance_meters: Math.max(
          fromProjection.distance,
          toProjection.distance,
        ),
      };
      shapeEdges.push(candidate);
      const coverageKey = `${visualRouteId}|${pairKey}`;
      if (!edgeCandidatesByVisualRoutePair.has(coverageKey))
        edgeCandidatesByVisualRoutePair.set(coverageKey, []);
      edgeCandidatesByVisualRoutePair.get(coverageKey).push(candidate);
    }
    edgesByShape.set(shapeId, shapeEdges);
  }

  return { edgeCandidatesByVisualRoutePair, edgesByShape };
}

function loadVisualEdgeTopology(lines) {
  const selectedShapeIds = new Set(
    lines.flatMap(
      (line) => line.rawShapeIds ?? line.sourceShapeIds ?? [line.shapeId],
    ),
  );
  const shapeLines = loadCanonicalShapeLines();
  const gtfsEntries = loadGtfsTextEntries();
  if (!gtfsEntries) {
    return {
      available: false,
      shapeLines,
      edgeCandidatesByVisualRoutePair: new Map(),
      edgesByShape: new Map(),
    };
  }

  const { tripToShape, routeByShape } = buildTripSelection(
    gtfsEntries.get("trips.txt"),
    selectedShapeIds,
  );
  const stopsByStation = buildStopsByStation(gtfsEntries.get("stops.txt"));
  const stopRowsByShape = parseStopTimesForTrips(
    gtfsEntries.get("stop_times.txt"),
    tripToShape,
  );
  const { edgeCandidatesByVisualRoutePair, edgesByShape } = buildShapeStopEdges(
    {
      shapeLines,
      stopsByStation,
      stopRowsByShape,
      routeByShape,
    },
  );

  return {
    available: true,
    shapeLines,
    routeByShape,
    edgeCandidatesByVisualRoutePair,
    edgesByShape,
  };
}

function buildInputLines(familyVisual) {
  const lines = familyVisual.features
    .map((feature, index) => {
      const rawRouteId = normalizeRouteId(
        feature.properties?.route_id ?? feature.properties?.display_route,
      );
      const visualRouteId = visualRouteIdFor(rawRouteId);
      const coordinates =
        feature.geometry?.type === "LineString"
          ? feature.geometry.coordinates.filter(isValidCoordinate)
          : [];
      if (!rawRouteId || !visualRouteId || coordinates.length < 2) return null;
      const lengthMeters = lineLengthMeters(coordinates);
      if (lengthMeters <= 0) return null;
      const visualFamily = routeFamily(
        visualRouteId,
        feature.properties?.visual_family,
      );
      const rawShapeIds = Array.isArray(feature.properties?.source_shape_ids)
        ? feature.properties.source_shape_ids.map(String)
        : [String(feature.properties?.shape_id ?? `${rawRouteId}-${index}`)];
      const canonicalDirectionIds = [
        ...new Set(
          rawShapeIds.map(canonicalDirectionIdForShape).filter(Boolean),
        ),
      ].sort();
      return {
        feature,
        index,
        routeId: visualRouteId,
        rawRouteId,
        visualRouteId,
        displayRoute: String(
          feature.properties?.display_route ??
            visualRouteIdFor(
              feature.properties?.display_route ?? visualRouteId,
            ),
        ),
        color: String(feature.properties?.color ?? "#8a93a6"),
        shapeId: String(
          feature.properties?.shape_id ?? `${rawRouteId}-${index}`,
        ),
        sourceShapeIds: rawShapeIds,
        rawRouteIds: [rawRouteId],
        rawShapeIds,
        canonicalDirectionIds,
        canonicalFeatureCount: Number(
          feature.properties?.canonical_feature_count ?? rawShapeIds.length,
        ),
        collapsedDirectionCount: canonicalDirectionIds.length,
        directionCollapseReason:
          canonicalDirectionIds.length > 1
            ? "family-visual-lineage"
            : "single-direction-lineage",
        visualRouteNormalizationReason: visualRouteNormalizationReason(
          rawRouteId,
          visualRouteId,
        ),
        representativeShapeId: String(
          feature.properties?.representative_shape_id ??
            feature.properties?.shape_id ??
            `${rawRouteId}-${index}`,
        ),
        visualBranchId: String(
          feature.properties?.visual_branch_id ??
            `${sanitize(visualRouteId)}-${index + 1}`,
        ),
        visualFamily,
        coordinates,
        cumulative: cumulativeDistances(coordinates),
        lengthMeters,
        bounds: boundsFor(coordinates),
      };
    })
    .filter(Boolean);
  return collapseInputLinesForVisualRendering(lines);
}

function lineMinLat(line) {
  return Math.min(...line.coordinates.map((coordinate) => coordinate[1]));
}

function lineMaxLat(line) {
  return Math.max(...line.coordinates.map((coordinate) => coordinate[1]));
}

function lineTerminalPairKey(line) {
  const endpoints = [
    coordinateKey(line.coordinates[0], 3),
    coordinateKey(line.coordinates[line.coordinates.length - 1], 3),
  ];
  return endpoints.sort().join("~");
}

function chooseRepresentativeLine(lines, visualRouteId) {
  return [...lines].sort((left, right) => {
    const leftBaseRoute = left.rawRouteId === visualRouteId ? 0 : 1;
    const rightBaseRoute = right.rawRouteId === visualRouteId ? 0 : 1;
    if (leftBaseRoute !== rightBaseRoute) return leftBaseRoute - rightBaseRoute;

    // The 2 and F have GTFS terminal/short-turn variants that overlap the
    // same Manhattan corridors. Use the branch with the southern terminal as
    // the product-visible representative, while preserving all raw lineage.
    if (visualRouteId === "2" || visualRouteId === "F") {
      const minLatDiff = lineMinLat(left) - lineMinLat(right);
      if (Math.abs(minLatDiff) > 0.0005) return minLatDiff;
    }

    const lengthDiff = right.lengthMeters - left.lengthMeters;
    if (Math.abs(lengthDiff) > 1) return lengthDiff;

    const terminalDiff = lineTerminalPairKey(left).localeCompare(
      lineTerminalPairKey(right),
      "en",
      { numeric: true },
    );
    if (terminalDiff !== 0) return terminalDiff;
    return left.shapeId.localeCompare(right.shapeId, "en", { numeric: true });
  })[0];
}

function collapseLineage(lines) {
  return {
    rawRouteIds: [
      ...new Set(
        lines
          .flatMap((line) => line.rawRouteIds ?? [line.rawRouteId])
          .map(normalizeRouteId)
          .filter(Boolean),
      ),
    ].sort(),
    rawShapeIds: [
      ...new Set(
        lines
          .flatMap(
            (line) => line.rawShapeIds ?? line.sourceShapeIds ?? [line.shapeId],
          )
          .map(String)
          .filter(Boolean),
      ),
    ].sort(),
    canonicalDirectionIds: [
      ...new Set(
        lines
          .flatMap((line) => line.canonicalDirectionIds ?? [])
          .map(String)
          .filter(Boolean),
      ),
    ].sort(),
    canonicalFeatureCount: lines.reduce(
      (total, line) => total + Number(line.canonicalFeatureCount ?? 1),
      0,
    ),
  };
}

function withLineage(line, sourceLines, visualBranchId) {
  const lineage = collapseLineage(sourceLines);
  return {
    ...line,
    routeId: line.visualRouteId,
    displayRoute: line.visualRouteId,
    visualBranchId,
    rawRouteIds: lineage.rawRouteIds,
    rawShapeIds: lineage.rawShapeIds,
    sourceShapeIds: lineage.rawShapeIds,
    canonicalDirectionIds: lineage.canonicalDirectionIds,
    canonicalFeatureCount: lineage.canonicalFeatureCount,
    collapsedDirectionCount: lineage.canonicalDirectionIds.length,
    directionCollapseReason:
      sourceLines.length > 1 || lineage.canonicalDirectionIds.length > 1
        ? "visual-service-representative-collapse"
        : line.directionCollapseReason,
    representativeShapeId: line.shapeId,
    visualRouteNormalizationReason: lineage.rawRouteIds.some(
      (routeId) => routeId !== line.visualRouteId,
    )
      ? "express-variant-collapse"
      : "identity",
  };
}

function chooseRepresentativeEdge(candidates, preferredShapeId) {
  return [...candidates].sort((left, right) => {
    const leftPreferred = left.shape_id === preferredShapeId ? 0 : 1;
    const rightPreferred = right.shape_id === preferredShapeId ? 0 : 1;
    if (leftPreferred !== rightPreferred) return leftPreferred - rightPreferred;
    const leftProjection = left.projection_distance_meters;
    const rightProjection = right.projection_distance_meters;
    if (Math.abs(leftProjection - rightProjection) > 1)
      return leftProjection - rightProjection;
    const leftDirection = left.canonical_direction_id === "N" ? 0 : 1;
    const rightDirection = right.canonical_direction_id === "N" ? 0 : 1;
    if (leftDirection !== rightDirection) return leftDirection - rightDirection;
    return left.shape_id.localeCompare(right.shape_id, "en", { numeric: true });
  })[0];
}

function edgeLineFromCandidate(line, candidate, coverage, sequence) {
  const rawRouteIds = [
    ...new Set(coverage.map((entry) => entry.raw_route_id).filter(Boolean)),
  ].sort();
  const rawShapeIds = [
    ...new Set(coverage.map((entry) => entry.shape_id).filter(Boolean)),
  ].sort();
  const canonicalDirectionIds = [
    ...new Set(
      coverage.map((entry) => entry.canonical_direction_id).filter(Boolean),
    ),
  ].sort();
  const coordinates = cloneCoordinates(candidate.coordinates);
  const visualEdgeId = `${sanitize(line.visualRouteId)}:${candidate.stop_pair_key.replace("~", "-")}`;
  return {
    ...line,
    coordinates,
    cumulative: cumulativeDistances(coordinates),
    lengthMeters: lineLengthMeters(coordinates),
    bounds: boundsFor(coordinates),
    shapeId: candidate.shape_id,
    representativeShapeId: candidate.shape_id,
    sourceShapeIds: rawShapeIds,
    rawRouteIds,
    rawShapeIds,
    canonicalDirectionIds,
    canonicalFeatureCount: coverage.length,
    collapsedDirectionCount: canonicalDirectionIds.length,
    directionCollapseReason:
      canonicalDirectionIds.length > 1 || rawShapeIds.length > 1
        ? "visual-edge-direction-collapse"
        : "single-edge-direction",
    visualEdgeIds: [visualEdgeId],
    edgeCount: 1,
    fromStopId: candidate.from_stop_id,
    toStopId: candidate.to_stop_id,
    fromStopName: candidate.from_stop_name,
    toStopName: candidate.to_stop_name,
    edgeSequence: sequence,
    representativeEdgeGeometrySources: [candidate.shape_id],
    edgeGeometryConfidence:
      coverage.length > 1 &&
      Math.max(...coverage.map((entry) => entry.projection_distance_meters)) <
        90
        ? "high"
        : "medium",
    representativeEdgeGeometrySource: candidate.shape_id,
  };
}

function route2ProofBaseLine(routeLines, chainShapeId) {
  const seed =
    routeLines.find((line) => line.rawShapeIds.includes(chainShapeId)) ??
    routeLines[0];
  const lineage = collapseLineage(routeLines);
  return {
    ...seed,
    routeId: "2",
    rawRouteId: "2",
    visualRouteId: "2",
    displayRoute: "2",
    visualBranchId: "2-main",
    rawRouteIds: lineage.rawRouteIds,
    rawShapeIds: lineage.rawShapeIds,
    sourceShapeIds: lineage.rawShapeIds,
    canonicalDirectionIds: lineage.canonicalDirectionIds,
    canonicalFeatureCount: lineage.canonicalFeatureCount,
    collapsedDirectionCount: lineage.canonicalDirectionIds.length,
    directionCollapseReason: "route-2-local-stop-pair-collapse",
    visualRouteNormalizationReason: "identity",
    chainShapeId,
    branchRepresentativeGeometryUsed: false,
    branchRepresentativeWarning: null,
  };
}

function route2ChainCandidateScore(candidate) {
  const first = candidate.edges[0];
  const last = candidate.edges[candidate.edges.length - 1];
  const startsAtFlatbush = /flatbush/i.test(first?.from_stop_name ?? "")
    ? 0
    : 1;
  const endsAtWakefield = /wakefield/i.test(last?.to_stop_name ?? "") ? 0 : 1;
  const northbound =
    canonicalDirectionIdForShape(candidate.shapeId) === "N" ? 0 : 1;
  return [
    startsAtFlatbush,
    endsAtWakefield,
    northbound,
    -candidate.edges.length,
    candidate.shapeId,
  ];
}

function chooseRoute2MainChain(routeLines, topology) {
  const rawShapeIds = collapseLineage(routeLines).rawShapeIds;
  const candidates = rawShapeIds
    .map((shapeId) => ({
      shapeId,
      edges: topology.edgesByShape.get(shapeId) ?? [],
    }))
    .filter((candidate) => candidate.edges.length > 0)
    .sort((left, right) => {
      const leftScore = route2ChainCandidateScore(left);
      const rightScore = route2ChainCandidateScore(right);
      for (let index = 0; index < leftScore.length; index += 1) {
        if (leftScore[index] < rightScore[index]) return -1;
        if (leftScore[index] > rightScore[index]) return 1;
      }
      return 0;
    });
  if (candidates.length === 0) return null;
  return candidates[0];
}

function route2CandidateOrientation(candidate, chainEdge) {
  if (
    candidate.from_stop_id === chainEdge.from_stop_id &&
    candidate.to_stop_id === chainEdge.to_stop_id
  ) {
    return "chain-direction";
  }
  if (
    candidate.from_stop_id === chainEdge.to_stop_id &&
    candidate.to_stop_id === chainEdge.from_stop_id
  ) {
    return "opposite-chain-direction";
  }
  return "undirected-stop-pair";
}

function route2EdgeConfidence(candidates) {
  if (candidates.length <= 1) return "medium";
  const maxProjection = Math.max(
    ...candidates.map((entry) => entry.projection_distance_meters),
  );
  if (maxProjection < 40) return "high";
  if (maxProjection < 90) return "medium";
  return "low";
}

function chooseRoute2RepresentativeEdge(candidates, chainEdge) {
  return [...candidates].sort((left, right) => {
    const leftOrientation =
      route2CandidateOrientation(left, chainEdge) === "chain-direction" ? 0 : 1;
    const rightOrientation =
      route2CandidateOrientation(right, chainEdge) === "chain-direction"
        ? 0
        : 1;
    if (leftOrientation !== rightOrientation)
      return leftOrientation - rightOrientation;

    const leftProjection = left.projection_distance_meters;
    const rightProjection = right.projection_distance_meters;
    if (Math.abs(leftProjection - rightProjection) > 1)
      return leftProjection - rightProjection;

    const leftDirection = left.canonical_direction_id === "N" ? 0 : 1;
    const rightDirection = right.canonical_direction_id === "N" ? 0 : 1;
    if (leftDirection !== rightDirection) return leftDirection - rightDirection;

    return left.shape_id.localeCompare(right.shape_id, "en", { numeric: true });
  })[0];
}

function route2EdgeLineFromCandidate(
  baseLine,
  chainEdge,
  selected,
  candidates,
  sequence,
) {
  const orientation = route2CandidateOrientation(selected, chainEdge);
  const geometryReversed = orientation === "opposite-chain-direction";
  const coordinates = geometryReversed
    ? cloneCoordinates(selected.coordinates).reverse()
    : cloneCoordinates(selected.coordinates);
  const rawRouteIds = [
    ...new Set(candidates.map((entry) => entry.raw_route_id).filter(Boolean)),
  ].sort();
  const rawShapeIds = [
    ...new Set(candidates.map((entry) => entry.shape_id).filter(Boolean)),
  ].sort();
  const candidateDirectionIds = [
    ...new Set(
      candidates.map((entry) => entry.canonical_direction_id).filter(Boolean),
    ),
  ].sort();
  const visualEdgeId = `${sanitize(baseLine.visualRouteId)}:${chainEdge.stop_pair_key.replace("~", "-")}`;
  const edgeLength = lineLengthMeters(coordinates);
  const edgeGeometryConfidence = route2EdgeConfidence(candidates);
  const edgeMetadata = {
    visual_edge_id: visualEdgeId,
    edge_sequence: sequence,
    from_stop_id: chainEdge.from_stop_id,
    to_stop_id: chainEdge.to_stop_id,
    from_stop_name: chainEdge.from_stop_name,
    to_stop_name: chainEdge.to_stop_name,
    raw_shape_ids: rawShapeIds,
    candidate_shape_ids: rawShapeIds,
    candidate_direction_ids: candidateDirectionIds,
    representative_edge_geometry_source: selected.shape_id,
    representative_edge_geometry_sources: [selected.shape_id],
    representative_shape_id: selected.shape_id,
    canonical_direction_id:
      candidateDirectionIds.length === 1 ? candidateDirectionIds[0] : "mixed",
    canonical_direction_count: candidateDirectionIds.length,
    collapsed_direction_count: candidateDirectionIds.length,
    direction_collapse_reason:
      candidateDirectionIds.length > 1 || rawShapeIds.length > 1
        ? "route-2-local-edge-direction-collapse"
        : "route-2-single-local-edge-direction",
    geometry_reversed: geometryReversed,
    geometry_orientation: orientation,
    edge_length_m: Number(edgeLength.toFixed(2)),
    start_coordinate: cloneCoordinate(coordinates[0]),
    end_coordinate: cloneCoordinate(coordinates[coordinates.length - 1]),
    endpoint_snap_distance_m: 0,
    internal_stitch_valid: true,
    edge_geometry_confidence: edgeGeometryConfidence,
    geometry_selection_reason:
      orientation === "chain-direction"
        ? "selected candidate matching 2-main stop-pair direction"
        : orientation === "opposite-chain-direction"
          ? "selected opposite-direction candidate and reversed geometry into 2-main order"
          : "selected candidate for matching undirected stop pair",
    chain_source_shape_id: baseLine.chainShapeId,
    branch_representative_geometry_used: false,
    branch_representative_warning: null,
  };

  return {
    ...baseLine,
    coordinates,
    cumulative: cumulativeDistances(coordinates),
    lengthMeters: edgeLength,
    bounds: boundsFor(coordinates),
    shapeId: selected.shape_id,
    representativeShapeId: selected.shape_id,
    sourceShapeIds: rawShapeIds,
    rawRouteIds,
    rawShapeIds,
    canonicalDirectionIds: candidateDirectionIds,
    canonicalFeatureCount: candidates.length,
    collapsedDirectionCount: candidateDirectionIds.length,
    directionCollapseReason: edgeMetadata.direction_collapse_reason,
    visualEdgeIds: [visualEdgeId],
    edgeCount: 1,
    fromStopId: chainEdge.from_stop_id,
    toStopId: chainEdge.to_stop_id,
    fromStopName: chainEdge.from_stop_name,
    toStopName: chainEdge.to_stop_name,
    edgeSequence: sequence,
    rawGroupSequence: sequence,
    representativeEdgeGeometrySources: [selected.shape_id],
    edgeGeometryConfidence,
    representativeEdgeGeometrySource: selected.shape_id,
    visualEdgeMetadata: [edgeMetadata],
    edgeSequenceRange: [sequence, sequence],
    geometryReversalCount: geometryReversed ? 1 : 0,
    internalStitchValid: true,
    maxInternalSnapDistanceM: 0,
    branchRepresentativeGeometryUsed: false,
    branchRepresentativeWarning: null,
  };
}

function refreshRoute2EdgeMetadata(line, snapDistance = 0, stitchValid = true) {
  const metadata = line.visualEdgeMetadata?.[0];
  if (!metadata) return;
  metadata.start_coordinate = cloneCoordinate(line.coordinates[0]);
  metadata.end_coordinate = cloneCoordinate(
    line.coordinates[line.coordinates.length - 1],
  );
  metadata.endpoint_snap_distance_m = Number(snapDistance.toFixed(3));
  metadata.internal_stitch_valid = stitchValid;
  metadata.edge_length_m = Number(
    lineLengthMeters(line.coordinates).toFixed(2),
  );
}

function stitchRoute2ProofEdgeLines(edgeLines) {
  const stitched = [...edgeLines].sort(
    (left, right) =>
      Number(left.edgeSequence ?? 0) - Number(right.edgeSequence ?? 0),
  );
  for (let index = 0; index < stitched.length; index += 1) {
    const current = stitched[index];
    if (index === 0) {
      refreshRoute2EdgeMetadata(current, 0, true);
      continue;
    }
    const previous = stitched[index - 1];
    const previousEnd = previous.coordinates[previous.coordinates.length - 1];
    const currentStart = current.coordinates[0];
    const snapDistance = distanceMeters(previousEnd, currentStart);
    if (snapDistance <= ROUTE_2_EDGE_STITCH_EPSILON_METERS) {
      const shared = cloneCoordinate(previousEnd);
      current.coordinates[0] = shared;
      current.cumulative = cumulativeDistances(current.coordinates);
      current.lengthMeters = lineLengthMeters(current.coordinates);
      current.bounds = boundsFor(current.coordinates);
      refreshRoute2EdgeMetadata(current, snapDistance, true);
    } else {
      current.internalStitchValid = false;
      current.maxInternalSnapDistanceM = Number(snapDistance.toFixed(3));
      refreshRoute2EdgeMetadata(current, snapDistance, false);
    }
  }
  return stitched;
}

function buildRoute2ProofEdgeLines(routeLines, topology) {
  const chain = chooseRoute2MainChain(routeLines, topology);
  if (!chain) {
    return [];
  }
  const baseLine = route2ProofBaseLine(routeLines, chain.shapeId);
  const route2RawShapeIds = new Set(baseLine.rawShapeIds);
  const edgeLines = [];
  for (const [sequence, chainEdge] of chain.edges.entries()) {
    const coverageKey = `${baseLine.visualRouteId}|${chainEdge.stop_pair_key}`;
    const candidates = (
      topology.edgeCandidatesByVisualRoutePair.get(coverageKey) ?? [chainEdge]
    ).filter((entry) => route2RawShapeIds.has(entry.shape_id));
    const localCandidates = candidates.length > 0 ? candidates : [chainEdge];
    const selected = chooseRoute2RepresentativeEdge(localCandidates, chainEdge);
    edgeLines.push(
      route2EdgeLineFromCandidate(
        baseLine,
        chainEdge,
        selected,
        localCandidates,
        sequence,
      ),
    );
  }
  return stitchRoute2ProofEdgeLines(edgeLines);
}

function fallbackEdgeLines(line) {
  const edges = [];
  let sequence = 0;
  for (
    let start = 0;
    start < line.lengthMeters;
    start += FALLBACK_VISUAL_EDGE_METERS
  ) {
    const end = Math.min(
      line.lengthMeters,
      start + FALLBACK_VISUAL_EDGE_METERS,
    );
    if (end - start < MIN_RENDER_SEGMENT_METERS) continue;
    const coordinates = sliceLine(line, start, end);
    if (coordinates.length < 2) continue;
    const visualEdgeId = `${sanitize(line.visualRouteId)}:${sanitize(line.visualBranchId)}:fallback-${sequence}`;
    edges.push({
      ...line,
      coordinates,
      cumulative: cumulativeDistances(coordinates),
      lengthMeters: lineLengthMeters(coordinates),
      bounds: boundsFor(coordinates),
      visualEdgeIds: [visualEdgeId],
      edgeCount: 1,
      fromStopId: null,
      toStopId: null,
      fromStopName: null,
      toStopName: null,
      edgeSequence: sequence,
      rawGroupSequence: sequence,
      representativeEdgeGeometrySources: [line.representativeShapeId],
      edgeGeometryConfidence: "low",
      representativeEdgeGeometrySource: line.representativeShapeId,
    });
    sequence += 1;
  }
  return edges;
}

function stitchVisualEdgeLines(edgeLines) {
  const stitched = [...edgeLines].sort(
    (left, right) =>
      Number(left.edgeSequence ?? 0) - Number(right.edgeSequence ?? 0),
  );
  for (let index = 1; index < stitched.length; index += 1) {
    const previous = stitched[index - 1];
    const current = stitched[index];
    const previousEnd = previous.coordinates[previous.coordinates.length - 1];
    const currentStart = current.coordinates[0];
    if (
      distanceMeters(previousEnd, currentStart) <=
      VISUAL_EDGE_STITCH_TOLERANCE_METERS
    ) {
      const shared = cloneCoordinate(previousEnd);
      current.coordinates[0] = shared;
      current.cumulative = cumulativeDistances(current.coordinates);
      current.lengthMeters = lineLengthMeters(current.coordinates);
      current.bounds = boundsFor(current.coordinates);
    }
  }
  return stitched;
}

function buildVisualEdgeLines(lines) {
  const topology = loadVisualEdgeTopology(lines);
  const edgeLines = [];

  for (const line of lines) {
    const representativeShapeId = line.representativeShapeId ?? line.shapeId;
    const representativeEdges =
      topology.edgesByShape.get(representativeShapeId) ?? [];
    if (representativeEdges.length === 0) {
      edgeLines.push(...stitchVisualEdgeLines(fallbackEdgeLines(line)));
      continue;
    }

    const lineEdgeLines = [];
    for (const representativeEdge of representativeEdges) {
      const coverageKey = `${line.visualRouteId}|${representativeEdge.stop_pair_key}`;
      const coverage = (
        topology.edgeCandidatesByVisualRoutePair.get(coverageKey) ?? [
          representativeEdge,
        ]
      ).filter((entry) => line.rawShapeIds.includes(entry.shape_id));
      const candidates = coverage.length > 0 ? coverage : [representativeEdge];
      const selected = chooseRepresentativeEdge(
        candidates,
        representativeShapeId,
      );
      lineEdgeLines.push(
        edgeLineFromCandidate(
          line,
          selected,
          candidates,
          representativeEdge.edge_sequence,
        ),
      );
    }
    edgeLines.push(...stitchVisualEdgeLines(lineEdgeLines));
  }

  return edgeLines.sort((left, right) => {
    const routeDiff = left.visualRouteId.localeCompare(
      right.visualRouteId,
      "en",
      { numeric: true },
    );
    if (routeDiff !== 0) return routeDiff;
    const branchDiff = left.visualBranchId.localeCompare(
      right.visualBranchId,
      "en",
      { numeric: true },
    );
    if (branchDiff !== 0) return branchDiff;
    return Number(left.edgeSequence ?? 0) - Number(right.edgeSequence ?? 0);
  });
}

function collapseInputLinesForVisualRendering(lines) {
  const byVisualRoute = new Map();
  for (const line of lines) {
    if (!byVisualRoute.has(line.visualRouteId))
      byVisualRoute.set(line.visualRouteId, []);
    byVisualRoute.get(line.visualRouteId).push(line);
  }

  const collapsed = [];
  const route2ProofSourceLines = [];
  for (const [visualRouteId, routeLines] of byVisualRoute) {
    if (visualRouteId === "2") {
      route2ProofSourceLines.push(...routeLines);
      continue;
    }

    if (SINGLE_BRANCH_VISUAL_ROUTES.has(visualRouteId)) {
      const representative = chooseRepresentativeLine(
        routeLines,
        visualRouteId,
      );
      collapsed.push(
        withLineage(
          representative,
          routeLines,
          `${sanitize(visualRouteId)}-main`,
        ),
      );
      continue;
    }

    for (const line of routeLines) {
      collapsed.push(withLineage(line, [line], line.visualBranchId));
    }
  }

  const collapsedLines = collapsed.sort((left, right) => {
    const routeDiff = left.visualRouteId.localeCompare(
      right.visualRouteId,
      "en",
      { numeric: true },
    );
    if (routeDiff !== 0) return routeDiff;
    return left.visualBranchId.localeCompare(right.visualBranchId, "en", {
      numeric: true,
    });
  });
  const edgeLines = buildVisualEdgeLines(collapsedLines);
  if (route2ProofSourceLines.length > 0) {
    const route2Topology = loadVisualEdgeTopology(route2ProofSourceLines);
    edgeLines.push(
      ...buildRoute2ProofEdgeLines(route2ProofSourceLines, route2Topology),
    );
  }
  return edgeLines.sort((left, right) => {
    const routeDiff = left.visualRouteId.localeCompare(
      right.visualRouteId,
      "en",
      { numeric: true },
    );
    if (routeDiff !== 0) return routeDiff;
    const branchDiff = left.visualBranchId.localeCompare(
      right.visualBranchId,
      "en",
      { numeric: true },
    );
    if (branchDiff !== 0) return branchDiff;
    return Number(left.edgeSequence ?? 0) - Number(right.edgeSequence ?? 0);
  });
}

function candidateRoutesByFamily(lines) {
  const byFamily = new Map();
  for (const line of lines) {
    if (!byFamily.has(line.visualFamily))
      byFamily.set(line.visualFamily, new Map());
    const byRoute = byFamily.get(line.visualFamily);
    if (!byRoute.has(line.routeId)) byRoute.set(line.routeId, []);
    byRoute.get(line.routeId).push(line);
  }
  return byFamily;
}

function membershipAt(line, distance, familyRoutes) {
  const point = pointAtDistance(line, distance);
  const tangent = tangentAtDistance(line, distance);

  // Cross-family override: if a manual override applies at this point,
  // force this route into that override's bundle with that override's
  // lane order. This is how multi-family corridors (DeKalb/Atlantic,
  // 6 Av trunk, etc.) get bundled despite belonging to different families.
  const override = findManualOverrideAt(line.routeId, point);
  if (override) {
    return {
      kind: "manual-override",
      corridorId: override.corridorId,
      memberRoutes: override.laneOrder,
      laneOrder: override.laneOrder,
      laneSlots: override.laneSlots,
      zOrderBase: override.zOrderBase,
    };
  }

  // Existing same-family detection.
  const members = new Set([line.routeId]);
  for (const [routeId, candidates] of familyRoutes) {
    if (routeId === line.routeId) continue;
    for (const candidate of candidates) {
      const match = nearestParallelDistance(point, tangent, candidate);
      if (
        match.distance <= CORRIDOR_WIDTH_METERS &&
        match.cosine >= MIN_PARALLEL_COSINE
      ) {
        members.add(routeId);
        break;
      }
    }
  }

  const familyOrdered = laneOrderFor(line.visualFamily, members);
  return {
    kind: "family",
    corridorId: null,
    memberRoutes: familyOrdered,
    laneOrder: familyOrdered,
    laneSlots: null,
    zOrderBase: null,
  };
}

function groupKeyFor(line, memberRoutes) {
  if (memberRoutes.length < 2) return "solo";
  return `${line.visualFamily}|${memberRoutes.join(",")}`;
}

function segmentStateFor(line, startMeters, endMeters, familyRoutes) {
  const midpoint = (startMeters + endMeters) / 2;
  const membership = membershipAt(line, midpoint, familyRoutes);
  if (membership.memberRoutes.length < 2) {
    return {
      kind: "solo",
      groupKey: "solo",
      memberRoutes: [line.routeId],
      laneOrder: [],
      manualCorridorId: null,
      manualLaneSlot: null,
      manualZOrderBase: null,
    };
  }
  return {
    kind: "group",
    groupKey:
      membership.kind === "manual-override"
        ? `manual:${membership.corridorId}`
        : groupKeyFor(line, membership.memberRoutes),
    memberRoutes: membership.memberRoutes,
    laneOrder: membership.laneOrder,
    manualCorridorId: membership.corridorId,
    manualLaneSlot:
      membership.laneSlots != null
        ? (membership.laneSlots[line.routeId] ?? 0)
        : null,
    manualZOrderBase: membership.zOrderBase,
  };
}

function sampleBoundaries(line) {
  const boundaries = [0];
  for (
    let distance = SAMPLE_SPACING_METERS;
    distance < line.lengthMeters;
    distance += SAMPLE_SPACING_METERS
  ) {
    boundaries.push(distance);
  }
  if (line.lengthMeters > 0) boundaries.push(line.lengthMeters);
  return boundaries;
}

function mergeRuns(runs) {
  const merged = [];
  for (const run of runs) {
    const previous = merged[merged.length - 1];
    if (
      previous &&
      previous.kind === run.kind &&
      previous.groupKey === run.groupKey
    ) {
      previous.endMeters = run.endMeters;
      continue;
    }
    merged.push({ ...run });
  }
  return merged;
}

function stabilizeRuns(runs) {
  const stabilized = runs.map((run) => ({ ...run }));

  for (let index = 1; index < stabilized.length - 1; index += 1) {
    const previous = stabilized[index - 1];
    const current = stabilized[index];
    const next = stabilized[index + 1];
    const currentLength = current.endMeters - current.startMeters;

    // Do not let a short detector miss create an artificial recentering break
    // between two grouped spans. This is a topology repair, not a visual
    // transition: the route stays in its current corridor state until a real
    // handoff boundary can be declared.
    if (
      current.kind === "solo" &&
      previous.kind === "group" &&
      next.kind === "group" &&
      currentLength < MIN_GROUP_SPAN_METERS
    ) {
      const inherited =
        previous.endMeters - previous.startMeters >=
        next.endMeters - next.startMeters
          ? previous
          : next;
      current.kind = "group";
      current.groupKey = inherited.groupKey;
      current.memberRoutes = inherited.memberRoutes;
      current.laneOrder = inherited.laneOrder;
    }
  }

  return mergeRuns(stabilized);
}

function runsForLine(line, familyRoutes) {
  if (line.lengthMeters < MIN_RENDER_SEGMENT_METERS) return [];

  // Phase 5E treats the local stop-pair/fallback edge as the atomic visual
  // collapse unit. Corridor membership can be assigned to the whole edge, but
  // it must not split the edge at fixed-distance sample boundaries because
  // that reintroduces the cap/seam behavior the edge model is designed to
  // remove.
  const state = segmentStateFor(line, 0, line.lengthMeters, familyRoutes);
  return [
    {
      ...state,
      startMeters: 0,
      endMeters: line.lengthMeters,
    },
  ];
}

function sliceLine(line, startMeters, endMeters) {
  const start = Math.max(0, Math.min(line.lengthMeters, startMeters));
  const end = Math.max(start, Math.min(line.lengthMeters, endMeters));
  if (end - start < MIN_RENDER_SEGMENT_METERS) return [];

  const coordinates = [pointAtDistance(line, start)];
  for (let index = 1; index < line.coordinates.length - 1; index += 1) {
    const distance = line.cumulative[index];
    if (distance > start && distance < end)
      coordinates.push(line.coordinates[index]);
  }
  coordinates.push(pointAtDistance(line, end));

  const deduped = [];
  for (const coordinate of coordinates) {
    const previous = deduped[deduped.length - 1];
    if (!previous || distanceMeters(previous, coordinate) > 0.05)
      deduped.push(coordinate);
  }
  return deduped.length >= 2 ? deduped : [];
}

function groupIdFor(groupKey) {
  if (groupKey.startsWith("manual:")) {
    return `group-manual-${sanitize(groupKey.slice("manual:".length))}`;
  }
  const [visualFamily, routes] = groupKey.split("|");
  return `group-${sanitize(visualFamily)}-${routes.split(",").map(sanitize).join("-")}`;
}

function registerGroup(groupsByKey, line, run, coordinates) {
  if (run.kind !== "group") return null;

  const groupId = groupIdFor(run.groupKey);
  const existing = groupsByKey.get(run.groupKey);
  // For manual cross-family overrides, the lane order comes straight from the
  // override (which lists every member in left→right perpendicular order).
  // For same-family detection, fall back to the family-aware ordering helper.
  const laneOrder =
    run.manualCorridorId != null
      ? run.laneOrder
      : laneOrderFor(line.visualFamily, run.memberRoutes);
  if (existing) {
    existing.segment_count += 1;
    existing.total_length_meters += run.endMeters - run.startMeters;
    return existing;
  }

  const group = {
    group_id: groupId,
    visual_family:
      run.manualCorridorId != null
        ? `manual:${run.manualCorridorId}`
        : line.visualFamily,
    member_routes: laneOrder,
    member_visual_routes: laneOrder,
    lane_order: laneOrder,
    lane_order_visual: laneOrder,
    centerline: {
      type: "LineString",
      coordinates,
    },
    anchors: {
      start: coordinateKey(coordinates[0]),
      end: coordinateKey(coordinates[coordinates.length - 1]),
      stations: [],
    },
    confidence: laneOrder.length >= 3 ? "high" : "medium",
    source: run.manualCorridorId != null ? "manual" : "spatial",
    detection: {
      corridor_width_meters: CORRIDOR_WIDTH_METERS,
      min_parallel_cosine: MIN_PARALLEL_COSINE,
      min_span_meters: MIN_GROUP_SPAN_METERS,
    },
    manual_corridor_id: run.manualCorridorId ?? null,
    segment_count: 1,
    total_length_meters: run.endMeters - run.startMeters,
  };
  groupsByKey.set(run.groupKey, group);
  return group;
}

function debugIdFor(line, run, coordinates, segmentIndex) {
  const groupId = run.kind === "group" ? groupIdFor(run.groupKey) : "solo";
  const hash = hashJson({
    visualRouteId: line.visualRouteId,
    visualBranchId: line.visualBranchId,
    visualEdgeIds: line.visualEdgeIds ?? [],
    segmentIndex,
    groupId,
    coordinates: coordinates.map(([lng, lat]) => [
      Number(lng.toFixed(6)),
      Number(lat.toFixed(6)),
    ]),
  });
  return `${line.visualRouteId}:${line.visualBranchId}:${run.kind}:${segmentIndex}:${hash}`;
}

function featureForRun(line, run, coordinates, segmentIndex, group) {
  const laneOrder = run.kind === "group" ? group.lane_order : [];
  // A service has one visual lane within a family corridor. The slot is keyed
  // to normalized route identity, not the sampled member-set for this span, so
  // shape variants and transient detector changes cannot flip the rendered
  // side of a route at arbitrary sample boundaries.
  // Manual cross-family overrides take precedence over family-based slotting.
  const laneSlot =
    run.kind === "group"
      ? run.manualLaneSlot != null
        ? run.manualLaneSlot
        : stableFamilyLaneSlot(line.visualFamily, line.routeId)
      : 0;
  const debugId = debugIdFor(line, run, coordinates, segmentIndex);
  const featureLengthMeters = lineLengthMeters(coordinates);
  const visualZOrder =
    run.manualZOrderBase != null
      ? run.manualZOrderBase + Math.max(0, run.laneOrder.indexOf(line.routeId))
      : (ROUTE_Z_ORDER[line.routeId] ?? 0) +
        (run.kind === "group" ? laneSlot : 0);
  const baseProperties = {
    route_id: line.routeId,
    canonical_route_id: line.rawRouteId,
    visual_route_id: line.visualRouteId,
    visual_route_normalization_reason: line.visualRouteNormalizationReason,
    display_route: line.displayRoute,
    shape_id: line.shapeId,
    source_shape_ids: line.sourceShapeIds,
    raw_route_ids: line.rawRouteIds,
    raw_shape_ids: line.rawShapeIds,
    visual_edge_ids: line.visualEdgeIds ?? [],
    edge_count: line.edgeCount ?? 1,
    from_stop_id: line.fromStopId ?? null,
    to_stop_id: line.toStopId ?? null,
    from_stop_name: line.fromStopName ?? null,
    to_stop_name: line.toStopName ?? null,
    edge_sequence: line.edgeSequence ?? segmentIndex,
    edge_sequence_range: line.edgeSequenceRange ?? [
      line.edgeSequence ?? segmentIndex,
      line.edgeSequence ?? segmentIndex,
    ],
    representative_edge_geometry_source:
      line.representativeEdgeGeometrySource ?? line.representativeShapeId,
    representative_edge_geometry_sources:
      line.representativeEdgeGeometrySources ?? [line.representativeShapeId],
    edge_geometry_confidence: line.edgeGeometryConfidence ?? "low",
    visual_edge_metadata: [],
    geometry_reversal_count: line.geometryReversalCount ?? 0,
    internal_stitch_valid: line.internalStitchValid ?? true,
    max_internal_snap_distance_m: Number(line.maxInternalSnapDistanceM ?? 0),
    branch_representative_geometry_used:
      line.branchRepresentativeGeometryUsed ?? null,
    branch_representative_warning: line.branchRepresentativeWarning ?? null,
    canonical_direction_id:
      line.canonicalDirectionIds.length === 1
        ? line.canonicalDirectionIds[0]
        : "mixed",
    canonical_direction_count: line.canonicalDirectionIds.length,
    collapsed_direction_count: line.collapsedDirectionCount,
    direction_collapse_reason: line.directionCollapseReason,
    representative_shape_id: line.representativeShapeId,
    canonical_feature_count: line.canonicalFeatureCount,
    color: line.color,
    visual_family: line.visualFamily,
    visual_branch_id: line.visualBranchId,
    raw_group_sequence: Number(line.edgeSequence ?? 0) * 1000 + segmentIndex,
    group_sequence: Number(line.edgeSequence ?? 0) * 1000 + segmentIndex,
    group_id: run.kind === "group" ? group.group_id : null,
    merged_group_ids: run.kind === "group" ? [group.group_id] : [],
    group_member_routes: run.kind === "group" ? group.member_routes : [],
    group_member_visual_routes: run.kind === "group" ? group.member_routes : [],
    group_lane_order: laneOrder,
    group_lane_order_visual: laneOrder,
    group_lane_count: run.kind === "group" ? laneOrder.length : 1,
    visual_lane_slot: laneSlot,
    visual_z_order: visualZOrder,
    corridor_id: run.manualCorridorId ?? null,
    segment_kind: run.kind,
    render_style: "subway-ribbon-v1",
    source: "group-corridors",
    debug_id: debugId,
    feature_length_m: Number(featureLengthMeters.toFixed(2)),
    assignment_reason:
      run.kind === "group"
        ? "grouped edge: local visual edge matched a derived corridor group"
        : "solo edge: no active corridor group for this local visual edge",
    handoff_node_id: null,
    handoff_reason: null,
    handoff_kind: null,
    handoff_from_group_id: null,
    handoff_to_group_id: null,
    handoff_from_lane_slot: null,
    handoff_to_lane_slot: null,
    start_handoff_node_id: null,
    start_handoff_reason: null,
    start_handoff_kind: null,
    start_handoff_from_group_id: null,
    start_handoff_to_group_id: null,
    start_handoff_from_lane_slot: null,
    start_handoff_to_lane_slot: null,
    end_handoff_node_id: null,
    end_handoff_reason: null,
    end_handoff_kind: null,
    end_handoff_from_group_id: null,
    end_handoff_to_group_id: null,
    end_handoff_from_lane_slot: null,
    end_handoff_to_lane_slot: null,
  };
  baseProperties.effective_render_key = effectiveRenderKeyFor(baseProperties);
  if (
    Array.isArray(line.visualEdgeMetadata) &&
    line.visualEdgeMetadata.length > 0
  ) {
    baseProperties.visual_edge_metadata = line.visualEdgeMetadata.map(
      (metadata) => ({
        ...metadata,
        group_id: baseProperties.group_id,
        visual_lane_slot: laneSlot,
        visual_z_order: visualZOrder,
        segment_kind: run.kind,
        effective_render_key: baseProperties.effective_render_key,
        assignment_reason: baseProperties.assignment_reason,
        base_color_layer_visible: true,
        base_casing_layer_visible: true,
        base_glow_layer_visible: true,
        highlight_only: false,
      }),
    );
  }

  // Per-edge features intentionally emit CANONICAL coordinates here. The
  // perpendicular lane offset is applied post-merge by
  // `bakeOffsetsOnMergedFeatures()` inside `finalizeVisualFeatures()` so the
  // taper at the endpoints applies only to true bundle entry/exit points
  // (or route terminals), not to every per-stop-pair edge boundary. The
  // historical per-edge bake produced "lines kiss the canonical track at
  // every station" pinch points that visually collapsed bundled trios into
  // a single line at typical viewing zoom. See the design plan for details.
  return {
    type: "Feature",
    id: debugId,
    properties: baseProperties,
    geometry: {
      type: "LineString",
      coordinates,
    },
  };
}

function sequenceRangeForProperties(properties) {
  if (
    Array.isArray(properties.edge_sequence_range) &&
    properties.edge_sequence_range.length === 2
  ) {
    return [
      Number(properties.edge_sequence_range[0]),
      Number(properties.edge_sequence_range[1]),
    ];
  }
  const metadataSequences = (properties.visual_edge_metadata ?? [])
    .map((entry) => Number(entry.edge_sequence))
    .filter(Number.isFinite);
  if (metadataSequences.length > 0) {
    return [Math.min(...metadataSequences), Math.max(...metadataSequences)];
  }
  const sequence = Number(
    properties.edge_sequence ?? properties.raw_group_sequence ?? 0,
  );
  return [sequence, sequence];
}

function route2FeaturesCanMerge(left, right) {
  const leftProperties = left.properties;
  const rightProperties = right.properties;
  const [leftStart, leftEnd] = sequenceRangeForProperties(leftProperties);
  const [rightStart] = sequenceRangeForProperties(rightProperties);
  if (leftProperties.visual_route_id !== rightProperties.visual_route_id)
    return false;
  if (leftProperties.visual_branch_id !== rightProperties.visual_branch_id)
    return false;
  if (
    leftProperties.effective_render_key !== rightProperties.effective_render_key
  )
    return false;
  if (leftProperties.segment_kind !== rightProperties.segment_kind)
    return false;
  if ((leftProperties.group_id ?? null) !== (rightProperties.group_id ?? null))
    return false;
  if (
    Number(leftProperties.visual_lane_slot ?? 0) !==
    Number(rightProperties.visual_lane_slot ?? 0)
  )
    return false;
  if (
    Number(leftProperties.visual_z_order ?? 0) !==
    Number(rightProperties.visual_z_order ?? 0)
  )
    return false;
  if (rightStart !== leftEnd + 1) return false;
  if (leftEnd < leftStart) return false;
  if (
    distanceMeters(lastCoordinate(left), firstCoordinate(right)) >
    ROUTE_2_EDGE_STITCH_EPSILON_METERS
  )
    return false;
  return true;
}

function featuresCanMerge(left, right) {
  const leftProperties = left.properties;
  const rightProperties = right.properties;
  if (
    isRoute2Properties(leftProperties) ||
    isRoute2Properties(rightProperties)
  ) {
    return isRoute2Properties(leftProperties) &&
      isRoute2Properties(rightProperties)
      ? route2FeaturesCanMerge(left, right)
      : false;
  }
  if (
    routeBranchKeyForProperties(leftProperties) !==
    routeBranchKeyForProperties(rightProperties)
  )
    return false;
  if (
    leftProperties.effective_render_key !== rightProperties.effective_render_key
  )
    return false;
  if (
    distanceMeters(lastCoordinate(left), firstCoordinate(right)) >
    VISUAL_EDGE_STITCH_TOLERANCE_METERS
  )
    return false;
  return true;
}

function mergeFeaturePair(left, right) {
  const sharedCoordinate = cloneCoordinate(lastCoordinate(left));
  const mergedCoordinates = cloneCoordinates(left.geometry.coordinates);
  mergedCoordinates[mergedCoordinates.length - 1] = sharedCoordinate;
  const rightCoordinates = cloneCoordinates(right.geometry.coordinates);
  rightCoordinates[0] = sharedCoordinate;
  mergedCoordinates.push(...rightCoordinates.slice(1));

  const leftProperties = left.properties;
  const rightProperties = right.properties;
  const mergingRoute2 =
    isRoute2Properties(leftProperties) && isRoute2Properties(rightProperties);
  const mergedGroupIds = unionStrings(
    leftProperties.merged_group_ids,
    rightProperties.merged_group_ids,
  );
  const mergedMemberRoutes = laneOrderFor(
    leftProperties.visual_family,
    unionStrings(
      leftProperties.group_member_routes,
      rightProperties.group_member_routes,
    ),
  );
  const mergedLaneOrder = laneOrderFor(
    leftProperties.visual_family,
    unionStrings(
      leftProperties.group_lane_order,
      rightProperties.group_lane_order,
    ),
  );
  const mergedVisualEdgeMetadata = [
    ...(Array.isArray(leftProperties.visual_edge_metadata)
      ? leftProperties.visual_edge_metadata
      : []),
    ...(Array.isArray(rightProperties.visual_edge_metadata)
      ? rightProperties.visual_edge_metadata
      : []),
  ].sort(
    (leftEntry, rightEntry) =>
      Number(leftEntry.edge_sequence ?? 0) -
      Number(rightEntry.edge_sequence ?? 0),
  );
  const mergedSequences = mergedVisualEdgeMetadata
    .map((entry) => Number(entry.edge_sequence))
    .filter(Number.isFinite);
  const mergedSequenceRange =
    mergedSequences.length > 0
      ? [Math.min(...mergedSequences), Math.max(...mergedSequences)]
      : sequenceRangeForProperties(leftProperties);
  const mergedSnapDistances = mergedVisualEdgeMetadata
    .map((entry) => Number(entry.endpoint_snap_distance_m ?? 0))
    .filter(Number.isFinite);
  const mergedInternalStitchValid = mergedVisualEdgeMetadata.every(
    (entry) => entry.internal_stitch_valid === true,
  );
  const mergedRepresentativeSources = mergingRoute2
    ? [
        ...(leftProperties.representative_edge_geometry_sources ?? []),
        ...(rightProperties.representative_edge_geometry_sources ?? []),
      ]
        .map(String)
        .filter(Boolean)
    : unionStrings(
        leftProperties.representative_edge_geometry_sources,
        rightProperties.representative_edge_geometry_sources,
      );

  const mergedProperties = {
    ...leftProperties,
    raw_route_ids: unionStrings(
      leftProperties.raw_route_ids,
      rightProperties.raw_route_ids,
    ),
    raw_shape_ids: unionStrings(
      leftProperties.raw_shape_ids,
      rightProperties.raw_shape_ids,
    ),
    source_shape_ids: unionStrings(
      leftProperties.source_shape_ids,
      rightProperties.source_shape_ids,
    ),
    visual_edge_ids: unionStrings(
      leftProperties.visual_edge_ids,
      rightProperties.visual_edge_ids,
    ),
    edge_count: unionStrings(
      leftProperties.visual_edge_ids,
      rightProperties.visual_edge_ids,
    ).length,
    to_stop_id: rightProperties.to_stop_id ?? leftProperties.to_stop_id ?? null,
    to_stop_name:
      rightProperties.to_stop_name ?? leftProperties.to_stop_name ?? null,
    representative_edge_geometry_sources: mergedRepresentativeSources,
    visual_edge_metadata: mergedVisualEdgeMetadata,
    edge_sequence_range: mergedSequenceRange,
    geometry_reversal_count: mergedVisualEdgeMetadata.filter(
      (entry) => entry.geometry_reversed === true,
    ).length,
    internal_stitch_valid: mergedInternalStitchValid,
    max_internal_snap_distance_m:
      mergedSnapDistances.length > 0
        ? Number(Math.max(...mergedSnapDistances).toFixed(3))
        : 0,
    branch_representative_geometry_used: mergingRoute2
      ? false
      : leftProperties.branch_representative_geometry_used,
    branch_representative_warning: mergingRoute2
      ? null
      : leftProperties.branch_representative_warning,
    edge_geometry_confidence:
      leftProperties.edge_geometry_confidence === "low" ||
      rightProperties.edge_geometry_confidence === "low"
        ? "low"
        : leftProperties.edge_geometry_confidence === "medium" ||
            rightProperties.edge_geometry_confidence === "medium"
          ? "medium"
          : "high",
    canonical_direction_count: unionStrings(
      leftProperties.raw_shape_ids
        ?.map(canonicalDirectionIdForShape)
        .filter(Boolean) ?? [],
      rightProperties.raw_shape_ids
        ?.map(canonicalDirectionIdForShape)
        .filter(Boolean) ?? [],
    ).length,
    collapsed_direction_count: Math.max(
      Number(leftProperties.collapsed_direction_count ?? 0),
      Number(rightProperties.collapsed_direction_count ?? 0),
    ),
    merged_debug_ids: unionStrings(leftProperties.merged_debug_ids, [
      leftProperties.debug_id,
      ...(rightProperties.merged_debug_ids ?? []),
      rightProperties.debug_id,
    ]),
    merged_group_ids: mergedGroupIds,
    group_member_routes: mergedMemberRoutes,
    group_member_visual_routes: mergedMemberRoutes,
    group_lane_order: mergedLaneOrder,
    group_lane_order_visual: mergedLaneOrder,
    group_lane_count:
      leftProperties.segment_kind === "group"
        ? Math.max(mergedLaneOrder.length, 2)
        : 1,
    feature_length_m: Number(lineLengthMeters(mergedCoordinates).toFixed(2)),
    assignment_reason:
      mergedGroupIds.length > 0
        ? "merged validated visual edges with compatible render state"
        : "merged validated solo visual edges with compatible render state",
  };
  const mergedDirections = unionStrings(
    leftProperties.raw_shape_ids
      ?.map(canonicalDirectionIdForShape)
      .filter(Boolean) ?? [],
    rightProperties.raw_shape_ids
      ?.map(canonicalDirectionIdForShape)
      .filter(Boolean) ?? [],
  );
  mergedProperties.canonical_direction_id =
    mergedDirections.length === 1 ? mergedDirections[0] : "mixed";
  mergedProperties.collapsed_direction_count = mergedDirections.length;
  mergedProperties.effective_render_key =
    effectiveRenderKeyFor(mergedProperties);

  return {
    ...left,
    properties: mergedProperties,
    geometry: {
      type: "LineString",
      coordinates: mergedCoordinates,
    },
  };
}

function stableDebugIdFor(feature, sequence) {
  const properties = feature.properties;
  const coordinates = feature.geometry.coordinates;
  const hash = hashJson({
    visualRouteId: properties.visual_route_id ?? properties.route_id,
    visualBranchId: properties.visual_branch_id,
    sequence,
    effectiveRenderKey: properties.effective_render_key,
    coordinates: coordinates.map(([lng, lat]) => [
      Number(lng.toFixed(6)),
      Number(lat.toFixed(6)),
    ]),
  });
  return `${properties.visual_route_id ?? properties.route_id}:${properties.visual_branch_id}:${properties.segment_kind}:${sequence}:${hash}`;
}

function mergeContiguousFeatures(features) {
  const byBranch = new Map();
  for (const [index, feature] of features.entries()) {
    const key = routeBranchKeyForProperties(feature.properties);
    if (!byBranch.has(key)) byBranch.set(key, []);
    byBranch.get(key).push({ feature, index });
  }

  const merged = [];
  for (const entries of byBranch.values()) {
    entries.sort((left, right) => {
      const leftSequence = Number(
        left.feature.properties.raw_group_sequence ?? left.index,
      );
      const rightSequence = Number(
        right.feature.properties.raw_group_sequence ?? right.index,
      );
      return leftSequence - rightSequence || left.index - right.index;
    });

    let current = null;
    for (const { feature } of entries) {
      if (!current) {
        current = feature;
        continue;
      }
      if (featuresCanMerge(current, feature)) {
        current = mergeFeaturePair(current, feature);
      } else {
        merged.push(current);
        current = feature;
      }
    }
    if (current) merged.push(current);
  }

  merged.sort((left, right) => {
    const leftRoute = String(left.properties.route_id);
    const rightRoute = String(right.properties.route_id);
    if (leftRoute !== rightRoute)
      return leftRoute.localeCompare(rightRoute, "en", { numeric: true });
    const leftBranch = String(left.properties.visual_branch_id);
    const rightBranch = String(right.properties.visual_branch_id);
    if (leftBranch !== rightBranch)
      return leftBranch.localeCompare(rightBranch, "en", { numeric: true });
    return (
      Number(left.properties.raw_group_sequence ?? 0) -
      Number(right.properties.raw_group_sequence ?? 0)
    );
  });

  return merged;
}

function handoffReasonFor(previous, next) {
  const previousKind = previous.properties.segment_kind;
  const nextKind = next.properties.segment_kind;
  if (previousKind === "solo" && nextKind === "group") {
    return { reason: "detected-corridor-entry", kind: "merge" };
  }
  if (previousKind === "group" && nextKind === "solo") {
    return { reason: "detected-corridor-exit", kind: "divergence" };
  }
  return {
    reason: "station-adjacent-membership-change",
    kind: "membership-change",
  };
}

function applyHandoff(previous, next) {
  const coordinate = cloneCoordinate(lastCoordinate(previous));
  next.geometry.coordinates[0] = coordinate;
  const nodeId = `handoff-${sanitize(previous.properties.visual_route_id ?? previous.properties.route_id)}-${sanitize(previous.properties.visual_branch_id)}-${coordinateKey(coordinate, 5)}`;
  const { reason, kind } = handoffReasonFor(previous, next);
  const fromGroup = previous.properties.group_id ?? null;
  const toGroup = next.properties.group_id ?? null;
  const fromLane = Number(previous.properties.visual_lane_slot ?? 0);
  const toLane = Number(next.properties.visual_lane_slot ?? 0);

  Object.assign(previous.properties, {
    handoff_node_id: nodeId,
    handoff_reason: reason,
    handoff_kind: kind,
    handoff_from_group_id: fromGroup,
    handoff_to_group_id: toGroup,
    handoff_from_lane_slot: fromLane,
    handoff_to_lane_slot: toLane,
    end_handoff_node_id: nodeId,
    end_handoff_reason: reason,
    end_handoff_kind: kind,
    end_handoff_from_group_id: fromGroup,
    end_handoff_to_group_id: toGroup,
    end_handoff_from_lane_slot: fromLane,
    end_handoff_to_lane_slot: toLane,
  });

  Object.assign(next.properties, {
    handoff_node_id: nodeId,
    handoff_reason: reason,
    handoff_kind: kind,
    handoff_from_group_id: fromGroup,
    handoff_to_group_id: toGroup,
    handoff_from_lane_slot: fromLane,
    handoff_to_lane_slot: toLane,
    start_handoff_node_id: nodeId,
    start_handoff_reason: reason,
    start_handoff_kind: kind,
    start_handoff_from_group_id: fromGroup,
    start_handoff_to_group_id: toGroup,
    start_handoff_from_lane_slot: fromLane,
    start_handoff_to_lane_slot: toLane,
  });
}

function annotateBranches(features) {
  const byBranch = new Map();
  for (const feature of features) {
    const key = routeBranchKeyForProperties(feature.properties);
    if (!byBranch.has(key)) byBranch.set(key, []);
    byBranch.get(key).push(feature);
  }

  for (const branchFeatures of byBranch.values()) {
    branchFeatures.sort(
      (left, right) =>
        Number(left.properties.raw_group_sequence ?? 0) -
        Number(right.properties.raw_group_sequence ?? 0),
    );

    for (let index = 0; index < branchFeatures.length; index += 1) {
      const feature = branchFeatures[index];
      feature.properties.group_sequence = index;
      feature.properties.feature_length_m = Number(
        lineLengthMeters(feature.geometry.coordinates).toFixed(2),
      );
      feature.properties.effective_render_key = effectiveRenderKeyFor(
        feature.properties,
      );
      const debugId = stableDebugIdFor(feature, index);
      feature.id = debugId;
      feature.properties.debug_id = debugId;
    }

    if (branchFeatures.length > 0) {
      const first = branchFeatures[0];
      const last = branchFeatures[branchFeatures.length - 1];
      const startNodeId = `terminal-${sanitize(first.properties.visual_route_id ?? first.properties.route_id)}-${sanitize(first.properties.visual_branch_id)}-start`;
      const endNodeId = `terminal-${sanitize(last.properties.visual_route_id ?? last.properties.route_id)}-${sanitize(last.properties.visual_branch_id)}-end`;
      Object.assign(first.properties, {
        start_handoff_node_id: startNodeId,
        start_handoff_reason: "terminal",
        start_handoff_kind: "terminal",
        start_handoff_from_group_id: null,
        start_handoff_to_group_id: first.properties.group_id ?? null,
        start_handoff_from_lane_slot: 0,
        start_handoff_to_lane_slot: Number(
          first.properties.visual_lane_slot ?? 0,
        ),
      });
      Object.assign(last.properties, {
        end_handoff_node_id: endNodeId,
        end_handoff_reason: "terminal",
        end_handoff_kind: "terminal",
        end_handoff_from_group_id: last.properties.group_id ?? null,
        end_handoff_to_group_id: null,
        end_handoff_from_lane_slot: Number(
          last.properties.visual_lane_slot ?? 0,
        ),
        end_handoff_to_lane_slot: 0,
      });
    }

    for (let index = 1; index < branchFeatures.length; index += 1) {
      const previous = branchFeatures[index - 1];
      const current = branchFeatures[index];
      const sharedCoordinate = cloneCoordinate(lastCoordinate(previous));
      if (
        distanceMeters(sharedCoordinate, firstCoordinate(current)) <=
        VISUAL_EDGE_STITCH_TOLERANCE_METERS
      ) {
        previous.geometry.coordinates[
          previous.geometry.coordinates.length - 1
        ] = sharedCoordinate;
        current.geometry.coordinates[0] = sharedCoordinate;
      }

      const renderStateChanged =
        previous.properties.group_id !== current.properties.group_id ||
        Number(previous.properties.visual_lane_slot ?? 0) !==
          Number(current.properties.visual_lane_slot ?? 0) ||
        previous.properties.segment_kind !== current.properties.segment_kind ||
        previous.properties.effective_render_key !==
          current.properties.effective_render_key;

      if (renderStateChanged) applyHandoff(previous, current);
    }
  }

  return features;
}

function endpointPropertiesFor(feature, endpointKind, neighbor) {
  const properties = feature.properties;
  const neighborProperties = neighbor?.properties ?? {};
  const isStart = endpointKind === "start";
  const coordinate = isStart
    ? firstCoordinate(feature)
    : lastCoordinate(feature);
  const handoffPrefix = isStart ? "start" : "end";

  return {
    route_id: properties.route_id,
    visual_route_id: properties.visual_route_id,
    visual_route_normalization_reason:
      properties.visual_route_normalization_reason,
    display_route: properties.display_route,
    raw_route_ids: properties.raw_route_ids ?? [],
    raw_shape_ids:
      properties.raw_shape_ids ?? properties.source_shape_ids ?? [],
    visual_edge_ids: properties.visual_edge_ids ?? [],
    edge_count: properties.edge_count ?? null,
    from_stop_id: properties.from_stop_id ?? null,
    to_stop_id: properties.to_stop_id ?? null,
    from_stop_name: properties.from_stop_name ?? null,
    to_stop_name: properties.to_stop_name ?? null,
    edge_sequence: properties.edge_sequence ?? null,
    representative_edge_geometry_sources:
      properties.representative_edge_geometry_sources ?? [],
    edge_geometry_confidence: properties.edge_geometry_confidence ?? null,
    canonical_direction_id: properties.canonical_direction_id ?? null,
    canonical_direction_count: properties.canonical_direction_count ?? null,
    collapsed_direction_count: properties.collapsed_direction_count ?? null,
    direction_collapse_reason: properties.direction_collapse_reason ?? null,
    representative_shape_id: properties.representative_shape_id ?? null,
    visual_branch_id: properties.visual_branch_id,
    debug_id: properties.debug_id,
    endpoint_kind: endpointKind,
    group_sequence: properties.group_sequence,
    group_id: properties.group_id ?? null,
    segment_kind: properties.segment_kind,
    visual_lane_slot: properties.visual_lane_slot,
    visual_z_order: properties.visual_z_order,
    effective_render_key: properties.effective_render_key,
    neighbor_debug_id: neighborProperties.debug_id ?? null,
    neighbor_group_id: neighborProperties.group_id ?? null,
    neighbor_lane_slot: neighborProperties.visual_lane_slot ?? null,
    neighbor_effective_render_key:
      neighborProperties.effective_render_key ?? null,
    handoff_node_id: properties[`${handoffPrefix}_handoff_node_id`] ?? null,
    handoff_reason: properties[`${handoffPrefix}_handoff_reason`] ?? null,
    handoff_kind: properties[`${handoffPrefix}_handoff_kind`] ?? null,
    handoff_from_group_id:
      properties[`${handoffPrefix}_handoff_from_group_id`] ?? null,
    handoff_to_group_id:
      properties[`${handoffPrefix}_handoff_to_group_id`] ?? null,
    handoff_from_lane_slot:
      properties[`${handoffPrefix}_handoff_from_lane_slot`] ?? null,
    handoff_to_lane_slot:
      properties[`${handoffPrefix}_handoff_to_lane_slot`] ?? null,
    source: "group-corridors-endpoint",
    feature_length_m: properties.feature_length_m,
    bbox_name: bboxNameForCoordinate(coordinate),
  };
}

function buildEndpointArtifact(features, generatedAt, sourceHash) {
  const byBranch = new Map();
  for (const feature of features) {
    const key = routeBranchKeyForProperties(feature.properties);
    if (!byBranch.has(key)) byBranch.set(key, []);
    byBranch.get(key).push(feature);
  }

  const endpoints = [];
  for (const branchFeatures of byBranch.values()) {
    branchFeatures.sort(
      (left, right) =>
        Number(left.properties.group_sequence) -
        Number(right.properties.group_sequence),
    );
    for (let index = 0; index < branchFeatures.length; index += 1) {
      const feature = branchFeatures[index];
      const previous = branchFeatures[index - 1] ?? null;
      const next = branchFeatures[index + 1] ?? null;
      endpoints.push({
        type: "Feature",
        properties: endpointPropertiesFor(feature, "start", previous),
        geometry: {
          type: "Point",
          coordinates: cloneCoordinate(firstCoordinate(feature)),
        },
      });
      endpoints.push({
        type: "Feature",
        properties: endpointPropertiesFor(feature, "end", next),
        geometry: {
          type: "Point",
          coordinates: cloneCoordinate(lastCoordinate(feature)),
        },
      });
    }
  }

  return {
    type: "FeatureCollection",
    metadata: {
      generated_at: generatedAt,
      source: "build-corridor-groups",
      mode: "group-corridors-endpoints",
      family_visual_hash: sourceHash,
      endpoint_count: endpoints.length,
      visual_feature_count: features.length,
    },
    features: endpoints,
  };
}

function featureIntersectsBounds(feature, bounds) {
  return (feature.geometry?.coordinates ?? []).some((coordinate) =>
    pointInsideDebugBounds(coordinate, bounds),
  );
}

function route2FeatureHasCompleteEdgeLineage(feature) {
  const properties = feature.properties ?? {};
  const edgeIds = properties.visual_edge_ids ?? [];
  const metadata = Array.isArray(properties.visual_edge_metadata)
    ? properties.visual_edge_metadata
    : [];
  const metadataIds = new Set(
    metadata.map((entry) => String(entry.visual_edge_id ?? "")).filter(Boolean),
  );
  return (
    isRoute2Properties(properties) &&
    properties.branch_representative_geometry_used === false &&
    metadata.length === edgeIds.length &&
    edgeIds.every((edgeId) => metadataIds.has(edgeId)) &&
    properties.internal_stitch_valid === true &&
    Number.isFinite(Number(properties.max_internal_snap_distance_m))
  );
}

function route2RawEdgeFeatureFor(feature) {
  const properties = feature.properties ?? {};
  const metadata = properties.visual_edge_metadata?.[0] ?? {};
  const coordinates = cloneCoordinates(feature.geometry.coordinates);
  const start = coordinates[0];
  const end = coordinates[coordinates.length - 1];
  return {
    type: "Feature",
    id: metadata.visual_edge_id ?? properties.debug_id,
    properties: {
      ...metadata,
      route_id: properties.route_id,
      visual_route_id: properties.visual_route_id,
      visual_branch_id: properties.visual_branch_id,
      display_route: properties.display_route,
      raw_route_ids: properties.raw_route_ids ?? [],
      visual_edge_ids: properties.visual_edge_ids ?? [],
      edge_count: properties.edge_count ?? 1,
      group_sequence: properties.group_sequence,
      group_id: properties.group_id ?? null,
      visual_lane_slot: properties.visual_lane_slot,
      visual_z_order: properties.visual_z_order,
      segment_kind: properties.segment_kind,
      effective_render_key: properties.effective_render_key,
      assignment_reason: properties.assignment_reason,
      debug_id: properties.debug_id,
      feature_length_m: properties.feature_length_m,
      endpoints: {
        start: cloneCoordinate(start),
        end: cloneCoordinate(end),
      },
      source: "group-corridors-route-2-raw-edge",
    },
    geometry: {
      type: "LineString",
      coordinates,
    },
  };
}

function buildRoute2RawEdgeArtifact(features, generatedAt, sourceHash) {
  const route2Features = features
    .filter((feature) => isRoute2Properties(feature.properties ?? {}))
    .sort(
      (left, right) =>
        Number(left.properties.edge_sequence ?? 0) -
        Number(right.properties.edge_sequence ?? 0),
    );
  return {
    type: "FeatureCollection",
    metadata: {
      generated_at: generatedAt,
      source: "build-corridor-groups",
      mode: "route-2-edge-visual-raw",
      family_visual_hash: sourceHash,
      feature_count: route2Features.length,
    },
    features: route2Features.map(route2RawEdgeFeatureFor),
  };
}

function route2FocusFailures(features) {
  const failures = [];
  const route2Features = features.filter((feature) =>
    isRoute2Properties(feature.properties ?? {}),
  );
  for (const bbox of ROUTE_2_FOCUS_BBOXES) {
    const focusFeatures = route2Features.filter((feature) =>
      featureIntersectsBounds(feature, bbox.bounds),
    );
    if (focusFeatures.length === 0) {
      failures.push({
        bbox_id: bbox.id,
        type: "route-2-missing-from-focus-bbox",
      });
      continue;
    }
    for (const feature of focusFeatures) {
      if (!route2FeatureHasCompleteEdgeLineage(feature)) {
        failures.push({
          bbox_id: bbox.id,
          type: "route-2-incomplete-focus-lineage",
          debug_id: feature.properties?.debug_id ?? null,
        });
      }
    }
  }
  return failures;
}

function buildRoute2Summary(finalFeatures, route2RawEdgeArtifact) {
  const route2FinalFeatures = finalFeatures.filter((feature) =>
    isRoute2Properties(feature.properties ?? {}),
  );
  const rawShapeIds = new Set(
    route2FinalFeatures.flatMap(
      (feature) => feature.properties?.raw_shape_ids ?? [],
    ),
  );
  const visualEdgeIds = new Set(
    route2FinalFeatures.flatMap(
      (feature) => feature.properties?.visual_edge_ids ?? [],
    ),
  );
  const mixedDirectionFeatures = route2FinalFeatures.filter(
    (feature) => feature.properties?.canonical_direction_id === "mixed",
  );
  const completeEdgeLineageFeatures = route2FinalFeatures.filter(
    route2FeatureHasCompleteEdgeLineage,
  );
  const reversalCount = route2FinalFeatures.reduce(
    (total, feature) =>
      total + Number(feature.properties?.geometry_reversal_count ?? 0),
    0,
  );
  const longestFinalFeature = route2FinalFeatures.reduce(
    (longest, feature) =>
      Math.max(longest, Number(feature.properties?.feature_length_m ?? 0)),
    0,
  );
  const maxSnapDistance = route2FinalFeatures.reduce(
    (largest, feature) =>
      Math.max(
        largest,
        Number(feature.properties?.max_internal_snap_distance_m ?? 0),
      ),
    0,
  );
  return {
    raw_shape_count: rawShapeIds.size,
    visual_edge_count: visualEdgeIds.size,
    raw_edge_feature_count: route2RawEdgeArtifact.features.length,
    final_merged_feature_count: route2FinalFeatures.length,
    longest_final_feature_m: Number(longestFinalFeature.toFixed(2)),
    mixed_direction_feature_count: mixedDirectionFeatures.length,
    complete_edge_lineage_feature_count: completeEdgeLineageFeatures.length,
    geometry_reversal_count: reversalCount,
    max_internal_snap_distance_m: Number(maxSnapDistance.toFixed(3)),
    focus_bbox_failures: route2FocusFailures(route2FinalFeatures),
  };
}

/**
 * Apply the perpendicular lane offset to each merged feature's coordinates.
 *
 * Runs AFTER mergeContiguousFeatures() so the bake operates on already-merged
 * corridor segments. Taper applies only at each merged feature's true
 * endpoints — i.e. route terminals or actual bundle entry/exit points — not
 * at every per-stop-pair edge boundary. This is what eliminates the
 * "pinch at every station" visual artifact.
 *
 * Solo features (laneSlot = 0) and features without a finite numeric slot
 * pass through unchanged. Features whose geometry is not a LineString also
 * pass through unchanged (defensive — the build script only emits
 * LineStrings, but this keeps the helper safe under future changes).
 */
function bakeOffsetsOnMergedFeatures(features) {
  // Build an index of every grouped feature's endpoints, keyed by
  // (routeId, coordKey, slot). Two adjacent merged features that share a
  // coordinate AND have the same lane slot represent a logically continuous
  // line at the same offset — we should NOT taper at their shared join,
  // because tapering both ends to canonical creates a visible "kissing"
  // artifact at corridor boundaries (e.g. where B transitions from a family
  // bundle into 6av-orange-trunk at Canal St).
  const endpointKey = (routeId, coord, slot) =>
    `${routeId}|${coord[0].toFixed(6)},${coord[1].toFixed(6)}|${slot.toFixed(2)}`;

  return features.map((feature) => {
    if (feature.geometry?.type !== "LineString") return feature;

    const laneSlot = Number(feature.properties?.visual_lane_slot ?? 0);
    if (!Number.isFinite(laneSlot) || Math.abs(laneSlot) < 1e-9) {
      return feature;
    }

    const segmentKind = feature.properties?.segment_kind;
    if (segmentKind !== "group") return feature;

    const routeId = String(feature.properties?.route_id ?? "");
    const coords = feature.geometry.coordinates;
    if (!routeId || coords.length < 2) return feature;

    // Count occurrences of each endpoint coord across all same-slot features
    // for this route. A start coord appears once for THIS feature; if it
    // appears more than once total, that means another feature shares the
    // same endpoint with the same slot → they connect at full offset,
    // suppress the taper at this end.
    const startKey = endpointKey(routeId, coords[0], laneSlot);
    const endKey = endpointKey(routeId, coords[coords.length - 1], laneSlot);
    let startCount = 0;
    let endCount = 0;
    for (const f of features) {
      if (f.geometry?.type !== "LineString") continue;
      if (f.properties?.segment_kind !== "group") continue;
      if (String(f.properties?.route_id ?? "") !== routeId) continue;
      const fSlot = Number(f.properties?.visual_lane_slot ?? 0);
      if (!Number.isFinite(fSlot) || fSlot.toFixed(2) !== laneSlot.toFixed(2))
        continue;
      const fCoords = f.geometry.coordinates;
      if (fCoords.length < 2) continue;
      if (endpointKey(routeId, fCoords[0], fSlot) === startKey) startCount += 1;
      if (endpointKey(routeId, fCoords[fCoords.length - 1], fSlot) === startKey)
        startCount += 1;
      if (endpointKey(routeId, fCoords[0], fSlot) === endKey) endCount += 1;
      if (endpointKey(routeId, fCoords[fCoords.length - 1], fSlot) === endKey)
        endCount += 1;
    }
    // startCount/endCount include this feature's own contribution (1 for
    // start, 1 for end). A neighbor adds another count. So count >= 2 means
    // a same-slot neighbor shares this endpoint.
    const taperStart = startCount < 2;
    const taperEnd = endCount < 2;

    const offsetMeters = laneSlot * LANE_WIDTH_METERS;
    const baked = bakeLaneOffsetIntoPolyline(
      coords,
      offsetMeters,
      buildTaperFn(taperStart, taperEnd),
    );

    return {
      ...feature,
      geometry: {
        ...feature.geometry,
        coordinates: baked,
      },
    };
  });
}

function finalizeVisualFeatures(features) {
  // Order matters:
  //   1. mergeContiguousFeatures  — stitches same-slot edges into one feature per
  //                                 contiguous corridor segment.
  //   2. bakeOffsetsOnMergedFeatures — applies the perpendicular offset to each
  //                                 merged feature's coords, with taper only at
  //                                 the merged feature's true endpoints.
  //   3. annotateBranches         — adds handoff metadata and snaps adjacent
  //                                 features' shared endpoints together. The
  //                                 endpoints are already at canonical position
  //                                 (offset = 0 at the taper boundary), so
  //                                 snapping is a no-op against the bake.
  return annotateBranches(
    bakeOffsetsOnMergedFeatures(mergeContiguousFeatures(features)),
  );
}

function buildCorridorGroups(familyVisual) {
  const lines = buildInputLines(familyVisual);
  const byFamily = candidateRoutesByFamily(lines);
  const groupsByKey = new Map();
  const outputFeatures = [];
  const auditEntries = [];

  for (const line of lines) {
    const familyRoutes = byFamily.get(line.visualFamily) ?? new Map();
    const runs = runsForLine(line, familyRoutes);
    let segmentIndex = 0;

    for (const run of runs) {
      const coordinates = sliceLine(line, run.startMeters, run.endMeters);
      if (coordinates.length < 2) continue;
      const group = registerGroup(groupsByKey, line, run, coordinates);
      outputFeatures.push(
        featureForRun(line, run, coordinates, segmentIndex, group),
      );
      auditEntries.push({
        route_id: line.routeId,
        visual_route_id: line.visualRouteId,
        raw_route_ids: line.rawRouteIds,
        raw_shape_ids: line.rawShapeIds,
        visual_edge_ids: line.visualEdgeIds ?? [],
        edge_sequence: line.edgeSequence ?? null,
        from_stop_id: line.fromStopId ?? null,
        to_stop_id: line.toStopId ?? null,
        visual_branch_id: line.visualBranchId,
        segment_index: segmentIndex,
        segment_kind: run.kind,
        group_id: group?.group_id ?? null,
        member_routes: group?.member_routes ?? [],
        length_meters: Math.round(run.endMeters - run.startMeters),
      });
      segmentIndex += 1;
    }
  }

  const finalFeatures = finalizeVisualFeatures(outputFeatures);
  const routes = new Set();
  for (const feature of finalFeatures) {
    routes.add(normalizeRouteId(feature.properties.route_id));
    routes.add(visualRouteIdFor(feature.properties.visual_route_id));
    for (const rawRouteId of feature.properties.raw_route_ids ?? [])
      routes.add(normalizeRouteId(rawRouteId));
  }
  const missingRoutes = EXPECTED_ROUTES.filter(
    (routeId) => !routes.has(routeId),
  );
  if (missingRoutes.length > 0) {
    throw new Error(
      `group visual output is missing expected routes: ${missingRoutes.join(", ")}`,
    );
  }

  const groups = [...groupsByKey.values()].sort((left, right) =>
    left.group_id.localeCompare(right.group_id, "en", { numeric: true }),
  );

  const generatedAt = new Date().toISOString();
  const sourceHash = collectionHash(familyVisual);
  const endpointArtifact = buildEndpointArtifact(
    finalFeatures,
    generatedAt,
    sourceHash,
  );
  const route2RawEdgeArtifact = buildRoute2RawEdgeArtifact(
    outputFeatures,
    generatedAt,
    sourceHash,
  );
  const route2Summary = buildRoute2Summary(
    finalFeatures,
    route2RawEdgeArtifact,
  );
  const finalAuditEntries = finalFeatures.map((feature) => ({
    route_id: feature.properties.route_id,
    visual_route_id: feature.properties.visual_route_id,
    raw_route_ids: feature.properties.raw_route_ids ?? [],
    raw_shape_ids: feature.properties.raw_shape_ids ?? [],
    visual_edge_ids: feature.properties.visual_edge_ids ?? [],
    edge_count: feature.properties.edge_count ?? null,
    from_stop_id: feature.properties.from_stop_id ?? null,
    to_stop_id: feature.properties.to_stop_id ?? null,
    from_stop_name: feature.properties.from_stop_name ?? null,
    to_stop_name: feature.properties.to_stop_name ?? null,
    edge_sequence: feature.properties.edge_sequence ?? null,
    visual_branch_id: feature.properties.visual_branch_id,
    group_sequence: feature.properties.group_sequence,
    segment_kind: feature.properties.segment_kind,
    group_id: feature.properties.group_id,
    merged_group_ids: feature.properties.merged_group_ids ?? [],
    visual_lane_slot: feature.properties.visual_lane_slot,
    effective_render_key: feature.properties.effective_render_key,
    handoff_node_id: feature.properties.handoff_node_id,
    handoff_reason: feature.properties.handoff_reason,
    length_meters: Math.round(feature.properties.feature_length_m),
    bbox_start: bboxNameForCoordinate(firstCoordinate(feature)),
    bbox_end: bboxNameForCoordinate(lastCoordinate(feature)),
  }));

  return {
    groupsArtifact: {
      generated_at: generatedAt,
      source: "build-corridor-groups",
      family_visual_hash: sourceHash,
      options: {
        corridor_width_meters: CORRIDOR_WIDTH_METERS,
        min_parallel_cosine: MIN_PARALLEL_COSINE,
        sample_spacing_meters: SAMPLE_SPACING_METERS,
        min_group_span_meters: MIN_GROUP_SPAN_METERS,
      },
      groups,
    },
    visualArtifact: {
      type: "FeatureCollection",
      metadata: {
        generated_at: generatedAt,
        source: "build-corridor-groups",
        mode: "group-corridors",
        family_visual_hash: sourceHash,
        input_features: familyVisual.features.length,
        raw_output_features: outputFeatures.length,
        output_features: finalFeatures.length,
        group_count: groups.length,
        endpoint_count: endpointArtifact.features.length,
        route_2_summary: route2Summary,
      },
      features: finalFeatures,
    },
    endpointArtifact,
    route2RawEdgeArtifact,
    auditArtifact: {
      generated_at: generatedAt,
      source: "build-corridor-groups",
      route_2_summary: route2Summary,
      groups: groups.map((group) => ({
        group_id: group.group_id,
        visual_family: group.visual_family,
        member_routes: group.member_routes,
        member_visual_routes: group.member_visual_routes,
        lane_order: group.lane_order,
        lane_order_visual: group.lane_order_visual,
        segment_count: group.segment_count,
        total_length_meters: Math.round(group.total_length_meters),
      })),
      raw_segments: auditEntries,
      segments: finalAuditEntries,
    },
  };
}

export function writeCorridorGroups({
  familyVisualPath = DEFAULT_FAMILY_VISUAL_PATH,
  groupsPath = DEFAULT_GROUPS_PATH,
  visualPath = DEFAULT_VISUAL_PATH,
  auditPath = DEFAULT_AUDIT_PATH,
  endpointsPath = DEFAULT_ENDPOINTS_PATH,
  route2RawEdgesPath = DEFAULT_ROUTE_2_RAW_EDGES_PATH,
} = {}) {
  const familyVisual = JSON.parse(readFileSync(familyVisualPath, "utf8"));
  const {
    groupsArtifact,
    visualArtifact,
    endpointArtifact,
    route2RawEdgeArtifact,
    auditArtifact,
  } = buildCorridorGroups(familyVisual);

  mkdirSync(dirname(groupsPath), { recursive: true });
  mkdirSync(dirname(visualPath), { recursive: true });
  mkdirSync(dirname(auditPath), { recursive: true });
  mkdirSync(dirname(endpointsPath), { recursive: true });
  mkdirSync(dirname(route2RawEdgesPath), { recursive: true });
  writeFileSync(groupsPath, `${JSON.stringify(groupsArtifact)}\n`);
  writeFileSync(visualPath, `${JSON.stringify(visualArtifact)}\n`);
  writeFileSync(endpointsPath, `${JSON.stringify(endpointArtifact)}\n`);
  writeFileSync(
    route2RawEdgesPath,
    `${JSON.stringify(route2RawEdgeArtifact)}\n`,
  );
  writeFileSync(auditPath, `${JSON.stringify(auditArtifact, null, 2)}\n`);

  const manualCorridorIds = new Set(
    MANUAL_CORRIDOR_OVERRIDES.map((override) => override.corridorId),
  );
  const manualCorridorCount = visualArtifact.features.filter((feature) =>
    manualCorridorIds.has(feature.properties.corridor_id),
  ).length;

  return {
    groupsPath,
    visualPath,
    auditPath,
    endpointsPath,
    route2RawEdgesPath,
    groupCount: groupsArtifact.groups.length,
    featureCount: visualArtifact.features.length,
    endpointCount: endpointArtifact.features.length,
    route2RawEdgeCount: route2RawEdgeArtifact.features.length,
    routeCount: new Set(
      visualArtifact.features.map((feature) => feature.properties.route_id),
    ).size,
    manualCorridorCount,
  };
}

if (
  process.argv[1] &&
  fileURLToPath(import.meta.url) === resolve(process.argv[1])
) {
  const result = writeCorridorGroups();
  console.log(`[corridor-groups] wrote ${result.groupsPath}`);
  console.log(`[corridor-groups] wrote ${result.visualPath}`);
  console.log(`[corridor-groups] wrote ${result.endpointsPath}`);
  console.log(`[corridor-groups] wrote ${result.route2RawEdgesPath}`);
  console.log(`[corridor-groups] groups: ${result.groupCount}`);
  console.log(`[corridor-groups] features: ${result.featureCount}`);
  console.log(`[corridor-groups] endpoints: ${result.endpointCount}`);
  console.log(
    `[corridor-groups] route 2 raw edges: ${result.route2RawEdgeCount}`,
  );
  console.log(`[corridor-groups] routes: ${result.routeCount}`);
  console.log(
    `[corridor-groups] manual cross-family corridor segments: ${result.manualCorridorCount}`,
  );
}

export {
  bakeLaneOffsetIntoPolyline,
  shiftCoordinatePerpendicular,
  perpendicularDegreesPerMeter,
  buildTaperFn,
  LANE_WIDTH_METERS,
  TAPER_LENGTH_METERS,
};
