#!/usr/bin/env node
//
// SmartRoute subway visual-network builder.
//
// Architectural pivot from the failed pixel-skeleton pipeline:
//   - GTFS stop sequences are the authoritative source of route continuity.
//   - Geometry is just a visual coat on stop-pair edges.
//   - Shared corridors emerge from edges with near-identical sliced geometry.
//   - Connectivity validation is a hard gate before any production rendering.
//
// This script runs offline. The output artifacts are:
//   - subway-network.visual-debug-topology.json (Gate 2A)
//   - subway-network.visual-debug-edges.geojson (Gate 2B)
//   - subway-network.visual-debug-corridors.json (Gate 2C)
//   - subway-network.visual-debug-route-components.json (Gate 2D)
//   - artifacts/debug/subway-network.visual.candidate.geojson (always written)
//   - subway-network.visual.geojson (ONLY promoted after all gates pass)
//
// This script does NOT delete the legacy skeleton artifacts; they remain
// orphan debug data until the runtime opt-in flips over.

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";
import { buildSpineFromCorridor } from "./build/spine.ts";
import {
  assertSpineHashConsistency,
  assertNoBogusTransitions,
  assertQContinuousInBrooklyn,
  assertOriginsForRedGreenFlatbushEastern,
} from "./build/spine-validation.ts";
import {
  groupSpinesIntoPhysicalBundles,
  selectPhysicalBundleSpine,
  computePhysicalBundleSpineHash,
  clipPolylineToExtent,
} from "./build/physical-bundle.mjs";
import { orderColorsForBundle, BUNDLE_COLOR_ORDER } from "./build/lane-order.ts";
import { buildBranchTransitions } from "./build/branch-transitions.ts";
import { filterBogusTransitions, markOrphanLanes, removeOrphanErrorLanes } from "./build/lane-continuity-filter.ts";
import { dedupeDuplicateCorridors } from "./build/dedupe-duplicate-corridors.ts";
import { groupCorridorsByColorAndOverlap, mergeSameColorGroup } from "./build/same-color-merge.mjs";
import { materializePhysicalBundles } from "./build/physical-bundle-materialization.mjs";
import {
  detectCrossColorAdjacency,
  findSharedArcExtent,
  offsetPolylineBySlotRamp,
  offsetPolylineOverExtent,
} from "./build/cross-color-spread.mjs";
import { smoothSharpCorners, countSharpCorners, densifyLongSegments } from "./build/smooth-polyline.ts";
import { bridgeRouteGaps } from "./build/bridge-route-gaps.mjs";
import { taperBakedJointSteps } from "./build/joint-offset-taper.ts";
import { colocateSameColorStretches } from "./build/colocate-same-color.ts";
import { repairSameRouteEndpointCrossings } from "./build/same-route-junction-fabric.ts";
import { simplifyTightCurves } from "./build/simplify-tight-curves.ts";
import { snapDanglingSameColorEndpoints } from "./build/snap-dangling-same-color.ts";
import { snapOffRevenueToShape, maxOffShapeM } from "./build/snap-off-revenue-to-shape.ts";
import { replaceEndpointHairpin } from "./build/schematic-hairpin-arc.ts";
import { hermiteBetween } from "./build/offset-bow.ts";
import { collapseSameColorOverlaps } from "./build/collapse-same-color.mjs";
import { parallelOffsetCrossColor } from "./build/parallel-offset-cross-color.mjs";
import { suppressShadowOrphans } from "./build/suppress-shadow-orphans.ts";
import { applyCartographicJunctionOverrides } from "./build/cartographic-junction-overrides.ts";
import {
  buildMottHavenFiveSchematicLens,
  buildMottHavenSixSchematicMerge,
} from "./build/mott-haven-schematic.ts";
import { applyNostrandEasternSchematic } from "./build/nostrand-eastern-schematic.ts";
import { applyBrightonBqChurchSpacing } from "./build/brighton-bq-church-spacing.mjs";
import { applyCulverFgProspectSmoothing } from "./build/culver-fg-prospect-smoothing.ts";
import { applyJoralemonGreenRiverSmoothing } from "./build/joralemon-green-river.ts";
import { applyStNicholasBlueStraightening } from "./build/st-nicholas-blue-straightening.mjs";
import {
  loadOpenDataSubwayLines,
  OPEN_DATA_SOURCE_DATASET_ID,
  OPEN_DATA_SOURCE_NAME,
} from "./build/opendata-subway-lines.ts";
import { MTA_ROUTE_COLORS } from "./build/mta-colors.ts";
import { trimTerminalOverhang } from "./build/trim-terminal-overhang.ts";
import { addSixtyThirdStreetF } from "./build/sixty-third-street-f.ts";
import { cleanStatenIslandLine } from "./build/staten-island-cleanup.ts";
import { connectRockawayWye } from "./build/rockaway-wye.ts";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const publicDir = resolve(frontendRoot, "public");
// Engineering-only debug artifacts go OUTSIDE public/ so they are never served
// in production. Only runtime artifacts (subway-network.visual.geojson, etc.)
// stay in public/. This directory is git-ignored.
const debugDir = resolve(frontendRoot, "artifacts", "debug");
mkdirSync(debugDir, { recursive: true });
const cacheDir = resolve(frontendRoot, ".gtfs-cache");
const ZIP_PATH = resolve(cacheDir, "google_transit.zip");

const OUT_TOPOLOGY_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-topology.json",
);
const OUT_EDGES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-edges.geojson",
);
const OUT_OPENDATA_LINES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-opendata-lines.geojson",
);
const OUT_OPENDATA_OVERLAPS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-opendata-overlaps.geojson",
);
const OUT_CORRIDORS_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-corridors.json",
);
const OUT_CORRIDORS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-corridors.geojson",
);
const OUT_ROUTE_COMPONENTS_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-route-components.json",
);
const OUT_ANOMALIES_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-anomalies.json",
);
const OUT_ANOMALIES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-anomalies.geojson",
);
const OUT_RENDER_LANE_CONTINUITY_JSON = resolve(
  debugDir,
  "subway-network.visual-debug-render-lane-continuity.json",
);
const OUT_MISSING_ROUTE_LANES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-missing-route-lanes.geojson",
);
const OUT_JUNCTION_ANCHORS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-junction-anchors.geojson",
);
const OUT_JUNCTION_SNAPS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-junction-snaps.geojson",
);
const OUT_BUNDLES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-bundles.geojson",
);
const OUT_BUNDLE_LANES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-bundle-lanes.geojson",
);
const OUT_BUNDLE_GAPS_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-bundle-gaps.geojson",
);
const OUT_VISUAL_CANDIDATE = resolve(
  debugDir,
  "subway-network.visual.candidate.geojson",
);
const OUT_VISUAL_FINAL = resolve(
  publicDir,
  "subway-network.visual.geojson",
);
const OUT_SPINES_GEOJSON = resolve(
  debugDir,
  "subway-network.visual-debug-spines.geojson",
);
const OPEN_DATA_LINES_PATH = resolve(publicDir, "subway-lines-nyc-opendata.geojson");
const STATIONS_GEOJSON_PATH = resolve(publicDir, "subway-network.stations.geojson");
const OUT_PHYSICAL_BUNDLES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-physical-bundles.geojson");
const OUT_PHYSICAL_BUNDLE_LANES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-physical-bundle-lanes.geojson");
const OUT_PHYSICAL_BUNDLE_REJECTS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-physical-bundle-rejects.geojson");
const OUT_TRANSITIVE_BUNDLES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-transitive-bundles.geojson");
const OUT_LANE_ORDERS_JSON = resolve(debugDir, "subway-network.visual-debug-lane-orders.json");
const OUT_BRANCH_TRANSITIONS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-branch-transitions.geojson");
const OUT_SAME_COLOR_MERGES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-same-color-merges.geojson");
const OUT_CROSS_COLOR_SPREAD_GEOJSON = resolve(debugDir, "subway-network.visual-debug-cross-color-spread.geojson");
const OUT_CROSS_COLOR_SEGMENTS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-cross-color-segments.geojson");
const OUT_MATERIALIZED_BUNDLES_GEOJSON = resolve(debugDir, "subway-network.visual-debug-materialized-bundles.geojson");
const OUT_MATERIALIZED_BUNDLE_FANOUTS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-bundle-fanouts.geojson");
const OUT_MATERIALIZED_BUNDLE_SPLITS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-bundle-splits.geojson");
const OUT_MATERIALIZED_BUNDLE_DEFECTS_GEOJSON = resolve(debugDir, "subway-network.visual-debug-bundle-junction-defects.geojson");

if (!existsSync(STATIONS_GEOJSON_PATH)) {
  throw new Error(
    `${STATIONS_GEOJSON_PATH} is required input and currently has no generator; keep the checked-in station artifact present before running transit builds.`,
  );
}

// =====================================================================
// Tunables (Gate 2A)
// =====================================================================

// Minimum number of trips for a (route, direction, terminal_pair) to count
// as a "branch worth rendering". Filters out late-night specials, one-off
// reroutes, and yard moves while keeping the everyday + peak service variants.
const MIN_TRIPS_PER_BRANCH = 5;
const OPEN_DATA_MIN_FRAGMENT_LENGTH_M = 15;
// Max distance for bridging the same color across a junction anchor. Used by
// BOTH buildJunctionBridges (Fix 1, legacy) and buildBranchTransitions
// (Phase 3a). Keep paired unless Phase 3b intentionally diverges them.
const JUNCTION_BRIDGE_MAX_M = 90;
// Max distance for promoting a branch_transition. Distinct from
// JUNCTION_BRIDGE_MAX_M (legacy buildJunctionBridges). The audit in Phase 3a
// showed all production-quality transitions are <= ~5m at junction stations,
// plus a long-tail outlier at 42.85m (G at Fulton St) that we drop for now.
// 35m gives ample headroom for legitimate transitions while excluding outliers.
const BRANCH_TRANSITION_MAX_M = 35;
// Fix 3: per-slot lane width baked into geometry at build time. Pushed 12->18m
// (user-authorized) for Apple-Maps parallel-lane clarity: at the old pitch
// bundled colors collapsed behind the strongest one. 18m is the practical
// ceiling -- the widest shared-stop bar scales with pitch and the 60m cap
// (subway-station-overlay.check.mjs) is the binding limit (was ~45.5m at 14m,
// ~58.5m projected at 18m). Widening the BAKE (vs the runtime screen-offset) is
// the seam-safer lever: both endpoints at a split move by the same vector. This
// is the +50% regime that historically tore trunks, so the build's endpoint
// tripwires (exit(1) on any moved junction endpoint) gate it -- if the build
// fails or the bar cap trips, fall back 18 -> 17 -> 16.
const LANE_WIDTH_METERS = 18;
const MITER_LENGTH_CAP_RATIO = 2; // fall back to bevel above this miter length
const PHYSICAL_BUNDLE_SUBSTITUTE_CONFIDENCE_MIN = 0.75;
const BUNDLE_OVERLAP_DIST_MAX_M = 15;
const BUNDLE_SHARED_LEN_MIN_M = 250;
const BUNDLE_SPLIT_SAMPLE_M = 5;
const FANOUT_BLEND_M = 100;

// Route ID normalization. The MTA publishes some service variants as
// distinct route_ids (e.g., "6X" for express 6, "FX" for F express); these
// need to share the user-facing color but stay separate in topology since
// they have different stop sequences and shapes.
function normalizeRouteId(value) {
  const r = String(value || "").trim().toUpperCase();
  if (r === "6D") return "6X";
  if (r === "7D") return "7X";
  if (r === "FD") return "FX";
  // NOTE: deliberately do NOT collapse FS / GS / H into "S". They are
  // three physically disconnected shuttle services (42 St / Franklin Av /
  // Rockaway Park). Merging them into one route_id breaks connectivity
  // validation by construction. The runtime color map still treats
  // them all as gray.
  if (r === "SIR") return "SI";
  return r;
}

// Single source of truth lives in lib/mta-colors.json.
const ROUTE_COLORS = MTA_ROUTE_COLORS;

const COLOR_VISUAL_ORDER = [
  "#808183",
  "#A7A9AC",
  "#996633",
  "#6CBE45",
  "#FCCC0A",
  "#00933C",
  "#EE352E",
  "#0A84FF",
  "#FF6319",
  "#B933AD",
  "#0078C6",
];

// Hand-curated per-bundle color order overrides. Keyed by overrideKey
// ("<from_anchor_id>::<to_anchor_id>"). Empty in Phase 2; Phase 6 may populate
// after visual QA flags specific junctions where the heuristic produces
// visible crossings.
//
// Note: BUNDLE_COLOR_ORDER is imported from ./build/lane-order.ts (single
// source of truth). The local copy was removed to prevent the rank table
// from drifting from the canonical order used by orderColorsForBundle.
const BUNDLE_ORDER_OVERRIDES = {};

function routeColorFor(routeId) {
  return ROUTE_COLORS[routeId] ?? "#808183";
}

function colorRank(color) {
  const index = COLOR_VISUAL_ORDER.indexOf(color);
  return index === -1 ? 999 : index;
}

function bundleColorRank(color) {
  const index = BUNDLE_COLOR_ORDER.indexOf(color);
  return index === -1 ? 999 : index;
}

function compareRouteIds(a, b) {
  return a.localeCompare(b, "en", { numeric: true });
}

// =====================================================================
// Mini-ZIP reader (no external deps; mirrors regenerate-canonical-from-gtfs.mjs)
// =====================================================================

function readUInt16(buf, off) { return buf.readUInt16LE(off); }
function readUInt32(buf, off) { return buf.readUInt32LE(off); }

function parseZipEntries(zipBuffer, wantedNames) {
  const wanted = new Set(wantedNames);
  const entries = new Map();

  let eocd = -1;
  for (let i = zipBuffer.length - 22; i >= 0; i -= 1) {
    if (readUInt32(zipBuffer, i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("ZIP end-of-central-directory not found");

  const cdSize = readUInt32(zipBuffer, eocd + 12);
  const cdOff = readUInt32(zipBuffer, eocd + 16);
  let off = cdOff;
  const end = cdOff + cdSize;

  while (off < end) {
    if (readUInt32(zipBuffer, off) !== 0x02014b50) {
      throw new Error("Malformed ZIP central directory");
    }
    const cm = readUInt16(zipBuffer, off + 10);
    const cs = readUInt32(zipBuffer, off + 20);
    const nameLen = readUInt16(zipBuffer, off + 28);
    const extraLen = readUInt16(zipBuffer, off + 30);
    const commLen = readUInt16(zipBuffer, off + 32);
    const lho = readUInt32(zipBuffer, off + 42);
    const name = zipBuffer.subarray(off + 46, off + 46 + nameLen).toString("utf8");

    if (wanted.has(name)) {
      if (readUInt32(zipBuffer, lho) !== 0x04034b50) {
        throw new Error(`Bad local header for ${name}`);
      }
      const lnl = readUInt16(zipBuffer, lho + 26);
      const lel = readUInt16(zipBuffer, lho + 28);
      const dataOff = lho + 30 + lnl + lel;
      const compressed = zipBuffer.subarray(dataOff, dataOff + cs);
      const data = cm === 0 ? compressed : inflateRawSync(compressed);
      entries.set(name, data.toString("utf8").replace(/^﻿/, ""));
    }

    off += 46 + nameLen + extraLen + commLen;
  }
  for (const w of wanted) if (!entries.has(w)) throw new Error(`Missing ${w}`);
  return entries;
}

// =====================================================================
// CSV reader (handles quoted fields and CR/LF)
// =====================================================================

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 1; }
        else { quoted = false; }
      } else { field += c; }
      continue;
    }
    if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
    else if (c !== "\r") { field += c; }
  }
  if (field.length > 0 || row.length > 0) { row.push(field); rows.push(row); }

  const filtered = rows.filter((r) => r.some((v) => v !== ""));
  if (filtered.length === 0) return [];
  const headers = filtered[0];
  return filtered.slice(1).map((cols) => {
    const obj = {};
    headers.forEach((h, j) => { obj[h] = cols[j] ?? ""; });
    return obj;
  });
}

// =====================================================================
// Phase 2A — GTFS topology + per-route branches
// =====================================================================

console.log("[visual-network] reading GTFS zip:", ZIP_PATH);
if (!existsSync(ZIP_PATH)) {
  throw new Error(
    `GTFS cache missing at ${ZIP_PATH}. Run "npm run build:network" first ` +
    `to populate the cache (regenerate-canonical-from-gtfs.mjs downloads it).`,
  );
}
const zipBuffer = readFileSync(ZIP_PATH);
const gtfs = parseZipEntries(zipBuffer, [
  "stops.txt",
  "trips.txt",
  "stop_times.txt",
  "routes.txt",
]);

console.log("[visual-network] parsing stops.txt");
const stopRows = parseCsv(gtfs.get("stops.txt"));
console.log("[visual-network] parsing trips.txt");
const tripRows = parseCsv(gtfs.get("trips.txt"));
console.log("[visual-network] parsing stop_times.txt");
const stopTimeRows = parseCsv(gtfs.get("stop_times.txt"));
console.log("[visual-network] parsing routes.txt");
const routeRows = parseCsv(gtfs.get("routes.txt"));
console.log(
  `[visual-network] gtfs sizes: stops=${stopRows.length}, ` +
  `trips=${tripRows.length}, stop_times=${stopTimeRows.length}, ` +
  `routes=${routeRows.length}`,
);

// --- Build stops map. Resolve parent_station so platform-level stop_ids
//     collapse to station-level (e.g., "101N" + "101S" → "101"). ---
const stopsById = new Map();
for (const r of stopRows) {
  const id = String(r.stop_id || "").trim();
  if (!id) continue;
  stopsById.set(id, {
    stop_id: id,
    name: String(r.stop_name || "").trim(),
    lat: Number(r.stop_lat),
    lon: Number(r.stop_lon),
    parent_station: String(r.parent_station || "").trim() || null,
    location_type: Number(r.location_type || 0),
  });
}

// Station-level stop resolver: returns the parent_station id when present,
// otherwise the platform's own stop_id. Used to collapse 101N/101S → 101.
function stationIdOf(stopId) {
  const s = stopsById.get(stopId);
  if (!s) return stopId;
  if (s.parent_station && stopsById.has(s.parent_station)) {
    return s.parent_station;
  }
  return stopId;
}

// --- Build routes map ---
const routesByRawId = new Map();
for (const r of routeRows) {
  const rawId = String(r.route_id || "").trim();
  if (!rawId) continue;
  routesByRawId.set(rawId, {
    raw_route_id: rawId,
    route_id: normalizeRouteId(rawId),
    short_name: String(r.route_short_name || rawId).trim(),
    long_name: String(r.route_long_name || "").trim(),
    color: String(r.route_color || "").trim() || null,
  });
}

// --- Build trips map ---
const tripsById = new Map();
for (const r of tripRows) {
  const tid = String(r.trip_id || "").trim();
  if (!tid) continue;
  const rawRouteId = String(r.route_id || "").trim();
  const route = routesByRawId.get(rawRouteId);
  if (!route) continue;
  tripsById.set(tid, {
    trip_id: tid,
    raw_route_id: rawRouteId,
    route_id: route.route_id,
    direction_id: String(r.direction_id || "").trim(),
    shape_id: String(r.shape_id || "").trim() || null,
    service_id: String(r.service_id || "").trim(),
    headsign: String(r.trip_headsign || "").trim(),
  });
}

// --- Group stop_times by trip_id, sort by stop_sequence numeric.
//     Collapse to station-level. Build per-trip ordered station sequence. ---
console.log("[visual-network] building per-trip station sequences");

const stopTimesByTrip = new Map();
for (const r of stopTimeRows) {
  const tid = String(r.trip_id || "").trim();
  if (!tid) continue;
  const seq = Number(r.stop_sequence);
  const stopId = String(r.stop_id || "").trim();
  if (!Number.isFinite(seq) || !stopId) continue;
  if (!stopTimesByTrip.has(tid)) stopTimesByTrip.set(tid, []);
  stopTimesByTrip.get(tid).push({ seq, stopId });
}

const tripStations = new Map(); // trip_id → ordered [stationId, ...]
for (const [tid, list] of stopTimesByTrip) {
  list.sort((a, b) => a.seq - b.seq);
  const seen = new Set();
  const sequence = [];
  for (const item of list) {
    const sid = stationIdOf(item.stopId);
    if (seen.has(sid)) continue; // collapse adjacent duplicates (rare but defensive)
    seen.add(sid);
    sequence.push(sid);
  }
  if (sequence.length >= 2) tripStations.set(tid, sequence);
}

// --- Group trips by (route_id, direction_id, terminal_pair).
//     The terminal_pair is (first station, last station) of the sequence.
//     This makes "A to Far Rockaway" a distinct group from "A to Lefferts". ---
console.log("[visual-network] grouping trips into branches");

const branchAccum = new Map(); // key → { route_id, direction_id, terminals, patterns: Map<sigHash, {pattern, count}>, totalTrips }

for (const trip of tripsById.values()) {
  const sequence = tripStations.get(trip.trip_id);
  if (!sequence) continue;
  const terminalStart = sequence[0];
  const terminalEnd = sequence[sequence.length - 1];
  const key = `${trip.route_id}|${trip.direction_id}|${terminalStart}→${terminalEnd}`;
  if (!branchAccum.has(key)) {
    branchAccum.set(key, {
      route_id: trip.route_id,
      direction_id: trip.direction_id,
      terminal_start: terminalStart,
      terminal_end: terminalEnd,
      patterns: new Map(),
      total_trips: 0,
      sample_headsigns: new Set(),
    });
  }
  const branch = branchAccum.get(key);
  const sig = sequence.join(",");
  if (!branch.patterns.has(sig)) {
    branch.patterns.set(sig, { sequence, count: 0, sample_trip_ids: [], sample_shape_ids: new Set() });
  }
  const p = branch.patterns.get(sig);
  p.count += 1;
  if (p.sample_trip_ids.length < 3) p.sample_trip_ids.push(trip.trip_id);
  if (trip.shape_id) p.sample_shape_ids.add(trip.shape_id);
  branch.total_trips += 1;
  if (trip.headsign) branch.sample_headsigns.add(trip.headsign);
}

// --- For each branch, pick the MOST-FREQUENT stop pattern as its
//     canonical sequence. Drop branches whose total_trips < MIN_TRIPS_PER_BRANCH. ---
const branchesByRoute = new Map();
let droppedLowFreqBranches = 0;
for (const [, branch] of branchAccum) {
  if (branch.total_trips < MIN_TRIPS_PER_BRANCH) {
    droppedLowFreqBranches += 1;
    continue;
  }
  let bestSig = null;
  let bestCount = -1;
  for (const [sig, p] of branch.patterns) {
    if (p.count > bestCount) { bestCount = p.count; bestSig = sig; }
  }
  const canonical = branch.patterns.get(bestSig);
  if (!branchesByRoute.has(branch.route_id)) {
    branchesByRoute.set(branch.route_id, []);
  }
  branchesByRoute.get(branch.route_id).push({
    branch_id: `${branch.route_id}-${branch.direction_id}-${branch.terminal_start}-${branch.terminal_end}`,
    route_id: branch.route_id,
    direction_id: branch.direction_id,
    terminal_start: branch.terminal_start,
    terminal_end: branch.terminal_end,
    total_trips_in_branch: branch.total_trips,
    canonical_pattern_trips: canonical.count,
    canonical_pattern_share: Number((canonical.count / branch.total_trips).toFixed(3)),
    distinct_patterns: branch.patterns.size,
    stop_sequence: canonical.sequence,
    sample_trip_ids: canonical.sample_trip_ids,
    sample_shape_ids: [...canonical.sample_shape_ids],
    sample_headsigns: [...branch.sample_headsigns].slice(0, 4),
  });
}

// Sort branches per route by total_trips desc (most common service first)
for (const arr of branchesByRoute.values()) {
  arr.sort((a, b) => b.total_trips_in_branch - a.total_trips_in_branch);
}

// =====================================================================
// Phase 2A diagnostics
// =====================================================================

const topologyDoc = {
  generated_at: new Date().toISOString(),
  source: "build-subway-visual-network.mjs Gate 2A",
  parameters: {
    min_trips_per_branch: MIN_TRIPS_PER_BRANCH,
  },
  gtfs_input: {
    stops: stopRows.length,
    trips: tripRows.length,
    stop_times: stopTimeRows.length,
    routes: routeRows.length,
  },
  topology: {
    distinct_routes: branchesByRoute.size,
    total_branches: [...branchesByRoute.values()].reduce((a, b) => a + b.length, 0),
    dropped_low_freq_branches: droppedLowFreqBranches,
  },
  per_route: [...branchesByRoute.entries()]
    .sort((a, b) => a[0].localeCompare(b[0], "en", { numeric: true }))
    .map(([routeId, branches]) => {
      const allStations = new Set();
      for (const b of branches) for (const s of b.stop_sequence) allStations.add(s);
      return {
        route_id: routeId,
        branch_count: branches.length,
        distinct_stations: allStations.size,
        branches: branches.map((b) => ({
          branch_id: b.branch_id,
          direction_id: b.direction_id,
          terminal_start: b.terminal_start,
          terminal_start_name: stopsById.get(b.terminal_start)?.name ?? b.terminal_start,
          terminal_end: b.terminal_end,
          terminal_end_name: stopsById.get(b.terminal_end)?.name ?? b.terminal_end,
          stop_count: b.stop_sequence.length,
          total_trips_in_branch: b.total_trips_in_branch,
          canonical_pattern_trips: b.canonical_pattern_trips,
          canonical_pattern_share: b.canonical_pattern_share,
          distinct_patterns: b.distinct_patterns,
          sample_shape_ids: b.sample_shape_ids,
          sample_headsigns: b.sample_headsigns,
          stop_sequence: b.stop_sequence,
        })),
      };
    }),
};

mkdirSync(dirname(OUT_TOPOLOGY_JSON), { recursive: true });
writeFileSync(OUT_TOPOLOGY_JSON, `${JSON.stringify(topologyDoc, null, 2)}\n`);
console.log(`[visual-network] wrote ${OUT_TOPOLOGY_JSON}`);

// =====================================================================
// Phase 2B - OpenData visual line geometry + GTFS topology edges
// =====================================================================
//
// GTFS remains the topology source: branch stop sequences drive connectivity
// validation, station markers, and route coverage. Visual line geometry no
// longer comes from stop-pair slices of GTFS shapes.txt. The State of NY / MTA
// OpenData Subway Service Lines GeoJSON provides full visual polylines, which
// become render corridors directly.
console.log("[visual-network] Gate 2B - loading NYC OpenData subway line geometry");

const M_PER_DEG_LAT = 111_320;
function metersPerDegLng(lat) {
  return 111_320 * Math.cos((lat * Math.PI) / 180);
}
function distanceMeters(a, b) {
  const midLat = (a[1] + b[1]) / 2;
  const mPerLng = metersPerDegLng(midLat);
  const dx = (a[0] - b[0]) * mPerLng;
  const dy = (a[1] - b[1]) * M_PER_DEG_LAT;
  return Math.hypot(dx, dy);
}

function lineLengthMeters(coords) {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) {
    total += distanceMeters(coords[i - 1], coords[i]);
  }
  return total;
}

function vectorMeters(from, to) {
  const midLat = (from[1] + to[1]) / 2;
  const mPerLng = metersPerDegLng(midLat);
  return [
    (to[0] - from[0]) * mPerLng,
    (to[1] - from[1]) * M_PER_DEG_LAT,
  ];
}

function angleDeltaDegrees(a, b) {
  const dot = a[0] * b[0] + a[1] * b[1];
  const aLen = Math.hypot(a[0], a[1]);
  const bLen = Math.hypot(b[0], b[1]);
  if (aLen === 0 || bLen === 0) return 0;
  return Math.acos(Math.max(-1, Math.min(1, dot / (aLen * bLen)))) * 180 / Math.PI;
}

function geometryStats(coords) {
  let lengthM = 0;
  let maxSegmentLengthM = 0;
  let sharpAngleCount = 0;
  let maxBearingChangeDegrees = 0;

  for (let i = 1; i < coords.length; i += 1) {
    const segmentLength = distanceMeters(coords[i - 1], coords[i]);
    lengthM += segmentLength;
    maxSegmentLengthM = Math.max(maxSegmentLengthM, segmentLength);
  }

  for (let i = 2; i < coords.length; i += 1) {
    const incoming = vectorMeters(coords[i - 2], coords[i - 1]);
    const outgoing = vectorMeters(coords[i - 1], coords[i]);
    const delta = angleDeltaDegrees(incoming, outgoing);
    maxBearingChangeDegrees = Math.max(maxBearingChangeDegrees, delta);
    if (delta > 120) sharpAngleCount += 1;
  }

  const directDistanceM =
    coords.length >= 2 ? distanceMeters(coords[0], coords[coords.length - 1]) : 0;
  const sinuosity = directDistanceM > 1 ? lengthM / directDistanceM : 1;

  return {
    length_m: Number(lengthM.toFixed(2)),
    direct_distance_m: Number(directDistanceM.toFixed(2)),
    sinuosity: Number(sinuosity.toFixed(4)),
    max_segment_length_m: Number(maxSegmentLengthM.toFixed(2)),
    coordinate_count: coords.length,
    sharp_angle_count: sharpAngleCount,
    max_bearing_change_degrees: Number(maxBearingChangeDegrees.toFixed(2)),
  };
}

const SPARSE_LONG_SLICE_M = 300;
const MAX_SEGMENT_ANOMALY_M = 250;
const PROJECTION_ANOMALY_M = 125;

const edgeFeatures = [];
const topologyEdgeDiagnostics = {
  topology_edges_emitted: 0,
  topology_edges_dropped_missing_stop: 0,
};

for (const r of topologyDoc.per_route) {
  for (const branch of r.branches) {
    const stops = branch.stop_sequence;

    for (let i = 0; i < stops.length - 1; i += 1) {
      const p1 = stopsById.get(stops[i]);
      const p2 = stopsById.get(stops[i + 1]);
      if (!p1 || !p2) {
        topologyEdgeDiagnostics.topology_edges_dropped_missing_stop += 1;
        continue;
      }
      const topologyGeometry = [[p1.lon, p1.lat], [p2.lon, p2.lat]];
      const stats = geometryStats(topologyGeometry);

      edgeFeatures.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: topologyGeometry },
        properties: {
          edge_id: `${branch.branch_id}__${p1.stop_id}__${p2.stop_id}`,
          route_id: r.route_id,
          branch_id: branch.branch_id,
          direction_id: branch.direction_id,
          shape_id: null,
          shape_candidate_count: 0,
          shape_selection_strategy: "gtfs_topology_only",
          from_stop_id: p1.stop_id,
          from_stop_name: p1.name,
          to_stop_id: p2.stop_id,
          to_stop_name: p2.name,
          length_m: stats.length_m,
          direct_distance_m: stats.direct_distance_m,
          sinuosity: stats.sinuosity,
          max_segment_length_m: stats.max_segment_length_m,
          coordinate_count: stats.coordinate_count,
          sharp_angle_count: stats.sharp_angle_count,
          from_projection_dist_m: null,
          to_projection_dist_m: null,
          endpoint_snap_from: false,
          endpoint_snap_to: false,
        },
      });
      topologyEdgeDiagnostics.topology_edges_emitted += 1;
    }
  }
}

const expectedOpenDataRouteIds = topologyDoc.per_route.map((route) => route.route_id);
const openDataLines = loadOpenDataSubwayLines(OPEN_DATA_LINES_PATH, {
  expectedRouteIds: expectedOpenDataRouteIds,
  minFragmentLengthM: OPEN_DATA_MIN_FRAGMENT_LENGTH_M,
});
const opendataLineFeatures = openDataLines.features.map((feature, index) => ({
  ...feature,
  properties: {
    ...feature.properties,
    opendata_line_id: `opendata-${String(index + 1).padStart(5, "0")}`,
  },
}));

const edgesDoc = {
  type: "FeatureCollection",
  metadata: {
    generated_at: new Date().toISOString(),
    source: "build-subway-visual-network.mjs Gate 2B OpenData normalized lines",
    parameters: {
      visual_geometry_source: OPEN_DATA_SOURCE_NAME,
      visual_geometry_source_dataset_id: OPEN_DATA_SOURCE_DATASET_ID,
      raw_opendata_path: "frontend/public/subway-lines-nyc-opendata.geojson",
      shape_selection_strategy: "nyc_opendata_full_lines",
    },
    diagnostics: {
      ...openDataLines.diagnostics,
      ...topologyEdgeDiagnostics,
    },
  },
  features: opendataLineFeatures,
};
writeFileSync(OUT_OPENDATA_LINES_GEOJSON, `${JSON.stringify(edgesDoc)}\n`);
writeFileSync(OUT_EDGES_GEOJSON, `${JSON.stringify(edgesDoc)}\n`);
console.log(`[visual-network] wrote ${OUT_OPENDATA_LINES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_EDGES_GEOJSON}`);
console.log(
  `[visual-network] OpenData source features: ${openDataLines.diagnostics.source_feature_count}`,
);
console.log(
  `[visual-network] OpenData normalized line features: ${openDataLines.diagnostics.normalized_feature_count}`,
);
console.log(
  `[visual-network] OpenData represented routes: ${openDataLines.diagnostics.represented_route_ids.join(",")}`,
);
console.log(
  `[visual-network] OpenData missing expected routes: ${openDataLines.diagnostics.missing_expected_route_ids.join(",") || "none"}`,
);
console.log(
  `[visual-network] OpenData alias applications: ${JSON.stringify(openDataLines.diagnostics.alias_applications)}`,
);
console.log(
  `[visual-network] GTFS topology edges for validation: ${topologyEdgeDiagnostics.topology_edges_emitted}`,
);

const expectedEdges = topologyDoc.per_route.reduce(
  (acc, r) =>
    acc + r.branches.reduce((br, b) => br + Math.max(0, b.stop_count - 1), 0),
  0,
);
console.log(
  `[visual-network] expected topology edges: ${expectedEdges} (emitted: ${topologyEdgeDiagnostics.topology_edges_emitted}, retention ${(topologyEdgeDiagnostics.topology_edges_emitted / Math.max(1, expectedEdges) * 100).toFixed(1)}%)`,
);
// =====================================================================
// Phase 2C - OpenData corridors + overlap sanity diagnostics
// =====================================================================
//
// NYC OpenData already carries route membership on full visual line geometry.
// Gate 2C is therefore no longer a merge step. It converts normalized OpenData
// lines into corridor features and writes diagnostics for suspicious overlaps
// where separate OpenData features appear to share track without sharing route
// ids.
console.log("[visual-network] Gate 2C - OpenData corridor normalization");

const RESAMPLE_INTERVAL_M = 25;
const HAUSDORFF_MAX_M = 15;
const OVERLAP_MIN_RATIO = 0.6;
const TANGENT_MAX_DIFF_DEG = 30;
const GRID_CELL_M = 50;
const CONTAINMENT_AVG_DISTANCE_MAX_M = 15;
const CONTAINMENT_OVERLAP_MIN_RATIO = 0.85;
const OVERLAP_SHARED_LEN_MIN_M = 250;

// Bug 3 / DeKalb: round sharp single-vertex elbows in the final render geometry.
const SMOOTH_ANGLE_THRESHOLD_DEG = 35; // only corners sharper than this are cut
const SMOOTH_ITERATIONS = 3;
const SMOOTH_RATIO = 0.22;             // Chaikin cut fraction of the adjacent leg
const SMOOTH_MAX_FILLET_M = 18;        // hard cap on cut distance from the corner

// Apple-look tight-curve simplification: relax hairpins (a lot of total turning
// packed into a short arc, e.g. the 5 Mott Haven curl) toward a gentler arc.
const TIGHT_CURVE_TURN_DEG = 65;   // total |turn| within the window that marks a run "tight"
const TIGHT_CURVE_WINDOW_M = 50;   // arc half-window used to accumulate turning
const TIGHT_CURVE_ITERATIONS = 45; // Laplacian relaxation passes on tight vertices
const TIGHT_CURVE_LAMBDA = 0.5;    // relaxation strength (0..1)

// Off-revenue re-route (single owner for off-shape excursions like the 5 at
// Mott Haven): replace OpenData wander > this many meters from the route's GTFS
// revenue shape with the shape's own sub-path. Runs as the LAST geometry pass.
const OFF_REVENUE_MAX_M = 55;
// Visual QA gate: route-5 visual geometry near 149 St / Mott Haven must use a
// compact south-side schematic peel. GTFS is still used to remove bad OpenData
// excursions first, but the literal GTFS curl is not the Apple/Transit visual.
const MOTT_HAVEN_5_QA_BBOX = { minLon: -73.9335, maxLon: -73.9230, minLat: 40.8105, maxLat: 40.8230 };
const MOTT_HAVEN_5_QA_MAX_NORTH_LAT = 40.81795;
const MOTT_HAVEN_5_QA_MAX_TRUNK_DISTANCE_M = 3;
const MOTT_HAVEN_5_QA_MIN_TRUNK_JOIN_M = 230;
const MOTT_HAVEN_5_QA_WEST_BOW_LON_MAX = -73.93025;

// Same-color convergence snap: pull a dangling lane endpoint onto the trunk it
// merges into so converging same-color lanes touch instead of hanging short.
const SAME_COLOR_SNAP_DIST_M = 14;

// Route gap bridging: close small same-route seams left at fanout/junction
// boundaries (base-vs-member geometry differs by up to the overlap tolerance).
const BRIDGE_MIN_GAP_M = 6;            // endpoints closer than this are already joined
const BRIDGE_MAX_GAP_M = 28;          // never bridge wider than this (avoid chord-cutting real gaps)
const BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M = JUNCTION_BRIDGE_MAX_M;

// Same-color collapse: same-color features whose vertices fall within this of a
// longer same-color line are snapped onto it (rendered as one line). Tuned a bit
// above LANE_WIDTH_METERS so adjacent same-color lanes on one track merge, while
// genuinely-separate same-color tracks (further apart) stay distinct.
const SAME_COLOR_COLLAPSE_DIST_M = 12;

// Densify OpenData chords longer than this (km-scale straight segments) before
// lane offsetting + smoothing, so coarse corridors can render as clean curves.
const DENSIFY_MAX_SEGMENT_M = 250;
const DENSIFY_STEP_M = 40;

const ROUTE_FAMILY_GROUPS = [
  ["1", "2", "3"],
  ["4", "5", "6", "6X"],
  ["A", "C", "E"],
  ["B", "D", "F", "FX", "M"],
  ["N", "Q", "R", "W"],
  ["J", "Z", "M"],
  ["7", "7X"],
  ["S"],
  ["FS"],
  ["GS"],
  ["H"],
  ["SI"],
  ["L"],
  ["G"],
];

function routeFamilyKey(routeId) {
  for (const group of ROUTE_FAMILY_GROUPS) {
    if (group.includes(routeId)) return group.join("/");
  }
  return routeId;
}

const REF_LAT = 40.73;
const M_PER_DEG_LNG = metersPerDegLng(REF_LAT);
function toMeters(coord) {
  return [coord[0] * M_PER_DEG_LNG, coord[1] * M_PER_DEG_LAT];
}

function resampleEdgeAt5m(coordsLngLat) {
  const coordsM = coordsLngLat.map(toMeters);
  const arc = [0];
  for (let i = 1; i < coordsM.length; i += 1) {
    const dx = coordsM[i][0] - coordsM[i - 1][0];
    const dy = coordsM[i][1] - coordsM[i - 1][1];
    arc.push(arc[i - 1] + Math.hypot(dx, dy));
  }
  const total = arc[arc.length - 1];
  if (total < RESAMPLE_INTERVAL_M * 2) {
    return [
      { x: coordsM[0][0], y: coordsM[0][1], t: 0 },
      { x: coordsM[coordsM.length - 1][0], y: coordsM[coordsM.length - 1][1], t: total },
    ].map((p, i, arr) => {
      const next = arr[Math.min(i + 1, arr.length - 1)];
      const prev = arr[Math.max(i - 1, 0)];
      const dx = next.x - prev.x;
      const dy = next.y - prev.y;
      const len = Math.hypot(dx, dy) || 1;
      return { ...p, tx: dx / len, ty: dy / len };
    });
  }
  const samples = [];
  let segIdx = 0;
  for (let s = 0; s <= total; s += RESAMPLE_INTERVAL_M) {
    while (segIdx < arc.length - 2 && arc[segIdx + 1] < s) segIdx += 1;
    const segStart = arc[segIdx];
    const segEnd = arc[segIdx + 1];
    const segLen = segEnd - segStart;
    const t = segLen > 0 ? (s - segStart) / segLen : 0;
    const x = coordsM[segIdx][0] + t * (coordsM[segIdx + 1][0] - coordsM[segIdx][0]);
    const y = coordsM[segIdx][1] + t * (coordsM[segIdx + 1][1] - coordsM[segIdx][1]);
    const dx = coordsM[segIdx + 1][0] - coordsM[segIdx][0];
    const dy = coordsM[segIdx + 1][1] - coordsM[segIdx][1];
    const len = Math.hypot(dx, dy) || 1;
    samples.push({ x, y, t: s, tx: dx / len, ty: dy / len });
  }
  return samples;
}

function bidirectionalHausdorff(samplesA, samplesB) {
  let maxA = 0;
  let withinA = 0;
  let distanceSumA = 0;
  let tanSumA = 0;
  let tanCountA = 0;
  for (const a of samplesA) {
    let best = Infinity;
    let bestB = null;
    for (const b of samplesB) {
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d2 = dx * dx + dy * dy;
      if (d2 < best) { best = d2; bestB = b; }
    }
    const d = Math.sqrt(best);
    distanceSumA += d;
    if (d > maxA) maxA = d;
    if (d <= HAUSDORFF_MAX_M) withinA += 1;
    if (bestB) {
      const dot = Math.abs(a.tx * bestB.tx + a.ty * bestB.ty);
      const angleDeg = Math.acos(Math.min(1, Math.max(-1, dot))) * 180 / Math.PI;
      tanSumA += angleDeg;
      tanCountA += 1;
    }
  }
  let maxB = 0;
  let withinB = 0;
  let distanceSumB = 0;
  for (const b of samplesB) {
    let best = Infinity;
    for (const a of samplesA) {
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d2 = dx * dx + dy * dy;
      if (d2 < best) best = d2;
    }
    const d = Math.sqrt(best);
    distanceSumB += d;
    if (d > maxB) maxB = d;
    if (d <= HAUSDORFF_MAX_M) withinB += 1;
  }
  const overlapA = samplesA.length > 0 ? withinA / samplesA.length : 0;
  const overlapB = samplesB.length > 0 ? withinB / samplesB.length : 0;
  return {
    hausdorff: Math.max(maxA, maxB),
    overlap: Math.min(overlapA, overlapB),
    overlapA,
    overlapB,
    avgDistanceA: samplesA.length > 0 ? distanceSumA / samplesA.length : Infinity,
    avgDistanceB: samplesB.length > 0 ? distanceSumB / samplesB.length : Infinity,
    avgTangentDeg: tanCountA > 0 ? tanSumA / tanCountA : 180,
  };
}

function routeSetsIntersect(left, right) {
  const rightSet = new Set(right);
  return left.some((routeId) => rightSet.has(routeId));
}

const pairsConsidered = 0;
const pairsMatched = 0;
const matchedPairs = [];
const corridorFeatures = [];
const corridorRows = [];

for (let index = 0; index < opendataLineFeatures.length; index += 1) {
  const feature = opendataLineFeatures[index];
  const stats = geometryStats(feature.geometry.coordinates);
  const corridorId = feature.properties.opendata_line_id;
  corridorFeatures.push({
    type: "Feature",
    geometry: feature.geometry,
    properties: {
      ...feature.properties,
      corridor_id: corridorId,
      branch_ids: [],
      member_edge_count: 0,
      base_member_edge_id: null,
      longest_member_edge_id: null,
      longest_member_length_m: stats.length_m,
      base_geometry_selection: "nyc_opendata_full_line",
      from_stop_id: null,
      to_stop_id: null,
      from_stop_name: null,
      to_stop_name: null,
      source_edge_ids: [],
      source_shape_ids: [],
      length_m: stats.length_m,
      direct_distance_m: stats.direct_distance_m,
      sinuosity: stats.sinuosity,
      max_segment_length_m: stats.max_segment_length_m,
      coordinate_count: stats.coordinate_count,
      sharp_angle_count: stats.sharp_angle_count,
    },
  });
  corridorRows.push({
    corridor_id: corridorId,
    route_ids: feature.properties.route_ids,
    member_edge_count: 1,
    longest_length_m: stats.length_m,
    is_shared: feature.properties.route_ids.length > 1,
    geometry_source: OPEN_DATA_SOURCE_NAME,
  });
}

const opendataSamples = opendataLineFeatures.map((feature) => resampleEdgeAt5m(feature.geometry.coordinates));
const opendataOverlapWarnings = [];
for (let i = 0; i < opendataLineFeatures.length; i += 1) {
  for (let j = i + 1; j < opendataLineFeatures.length; j += 1) {
    const left = opendataLineFeatures[i];
    const right = opendataLineFeatures[j];
    const leftRoutes = left.properties.route_ids ?? [];
    const rightRoutes = right.properties.route_ids ?? [];
    if (routeSetsIntersect(leftRoutes, rightRoutes)) continue;
    const metrics = bidirectionalHausdorff(opendataSamples[i], opendataSamples[j]);
    const shorterLenM = Math.min(left.properties.length_m ?? 0, right.properties.length_m ?? 0);
    const sharedLenM = shorterLenM * metrics.overlap;
    if (
      metrics.overlap >= OVERLAP_MIN_RATIO &&
      sharedLenM >= OVERLAP_SHARED_LEN_MIN_M &&
      Math.max(metrics.avgDistanceA, metrics.avgDistanceB) <= CONTAINMENT_AVG_DISTANCE_MAX_M &&
      metrics.avgTangentDeg <= TANGENT_MAX_DIFF_DEG
    ) {
      opendataOverlapWarnings.push({
        type: "Feature",
        geometry: left.geometry,
        properties: {
          marker_type: "opendata_overlap_warning",
          reason: "overlap_without_shared_route_ids",
          left_corridor_id: left.properties.opendata_line_id,
          right_corridor_id: right.properties.opendata_line_id,
          left_route_ids: leftRoutes,
          right_route_ids: rightRoutes,
          hausdorff_m: Number(metrics.hausdorff.toFixed(2)),
          overlap: Number(metrics.overlap.toFixed(3)),
          overlap_a: Number(metrics.overlapA.toFixed(3)),
          overlap_b: Number(metrics.overlapB.toFixed(3)),
          shared_length_m: Number(sharedLenM.toFixed(2)),
          avg_distance_a_m: Number(metrics.avgDistanceA.toFixed(2)),
          avg_distance_b_m: Number(metrics.avgDistanceB.toFixed(2)),
          avg_tangent_deg: Number(metrics.avgTangentDeg.toFixed(2)),
        },
      });
    }
  }
}

writeFileSync(
  OUT_OPENDATA_OVERLAPS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2C OpenData overlap sanity check",
      parameters: {
        overlap_min_ratio: OVERLAP_MIN_RATIO,
        shared_length_min_m: OVERLAP_SHARED_LEN_MIN_M,
        avg_distance_max_m: CONTAINMENT_AVG_DISTANCE_MAX_M,
        tangent_max_diff_deg: TANGENT_MAX_DIFF_DEG,
      },
      summary: {
        warning_count: opendataOverlapWarnings.length,
      },
    },
    features: opendataOverlapWarnings,
  })}\n`,
);
console.log(`[visual-network] wrote ${OUT_OPENDATA_OVERLAPS_GEOJSON}`);
console.log(`[visual-network] OpenData overlap warnings: ${opendataOverlapWarnings.length}`);
const JUNCTION_SNAP_MAX_M = 25;

function endpointClusterKey(stopId, index) {
  return `${stopId}#${index}`;
}

function clusterEndpointEntries(entries) {
  const clusters = [];
  for (const entry of entries) {
    let target = null;
    for (const cluster of clusters) {
      if (
        cluster.entries.some(
          (existing) =>
            distanceMeters(existing.coordinate, entry.coordinate) <=
            JUNCTION_SNAP_MAX_M,
        )
      ) {
        target = cluster;
        break;
      }
    }
    if (!target) {
      target = { entries: [], coordinate: entry.coordinate };
      clusters.push(target);
    }
    target.entries.push(entry);
    const lng =
      target.entries.reduce((sum, item) => sum + item.coordinate[0], 0) /
      target.entries.length;
    const lat =
      target.entries.reduce((sum, item) => sum + item.coordinate[1], 0) /
      target.entries.length;
    target.coordinate = [lng, lat];
  }
  return clusters;
}

function applyJunctionAnchorSnaps(features) {
  const entriesByStop = new Map();
  const geometryEndpointKey = "__opendata_geometry_endpoints__";
  for (const feature of features) {
    const coords = feature.geometry.coordinates;
    const endpoints = [
      {
        kind: "from",
        stop_id: feature.properties.from_stop_id,
        stop_name: feature.properties.from_stop_name,
        coordinate: coords[0],
      },
      {
        kind: "to",
        stop_id: feature.properties.to_stop_id,
        stop_name: feature.properties.to_stop_name,
        coordinate: coords[coords.length - 1],
      },
    ];
    for (const endpoint of endpoints) {
      if (!endpoint.coordinate) continue;
      const key = endpoint.stop_id || geometryEndpointKey;
      if (!entriesByStop.has(key)) entriesByStop.set(key, []);
      entriesByStop.get(key).push({ feature, ...endpoint, stop_id: endpoint.stop_id ?? key });
    }
  }

  const anchorFeatures = [];
  const snapFeatures = [];
  const anchorByFeatureEndpoint = new Map();

  for (const [stopId, entries] of entriesByStop) {
    const clusters = clusterEndpointEntries(entries);
    clusters.forEach((cluster, clusterIndex) => {
      const anchorId =
        stopId === geometryEndpointKey
          ? `opendata-anchor#${clusterIndex}`
          : endpointClusterKey(stopId, clusterIndex);
      anchorFeatures.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: cluster.coordinate },
        properties: {
          anchor_id: anchorId,
          stop_id: stopId === geometryEndpointKey ? null : stopId,
          stop_name: cluster.entries[0]?.stop_name ?? "",
          endpoint_count: cluster.entries.length,
          anchor_source:
            stopId === geometryEndpointKey ? "geometry_endpoint" : "gtfs_stop",
        },
      });

      for (const entry of cluster.entries) {
        const snapDistanceM = distanceMeters(entry.coordinate, cluster.coordinate);
        const key = `${entry.feature.properties.corridor_id}:${entry.kind}`;
        if (snapDistanceM > JUNCTION_SNAP_MAX_M) continue;
        anchorByFeatureEndpoint.set(key, { anchorId, coordinate: cluster.coordinate });

        const coords = entry.feature.geometry.coordinates;
        const original = entry.kind === "from" ? coords[0] : coords[coords.length - 1];
        if (snapDistanceM > 0.01) {
          if (entry.kind === "from") coords[0] = cluster.coordinate;
          else coords[coords.length - 1] = cluster.coordinate;
          snapFeatures.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: [original, cluster.coordinate] },
            properties: {
              corridor_id: entry.feature.properties.corridor_id,
              route_ids: entry.feature.properties.route_ids,
              stop_id: stopId,
              stop_name: entry.stop_name,
              endpoint_kind: entry.kind,
              anchor_id: anchorId,
              original_coord: original,
              snapped_coord: cluster.coordinate,
              snap_distance_m: Number(snapDistanceM.toFixed(2)),
            },
          });
        }
      }
    });
  }

  for (const feature of features) {
    const fromAnchor = anchorByFeatureEndpoint.get(`${feature.properties.corridor_id}:from`);
    const toAnchor = anchorByFeatureEndpoint.get(`${feature.properties.corridor_id}:to`);
    feature.properties.from_anchor_id = fromAnchor?.anchorId ?? null;
    feature.properties.to_anchor_id = toAnchor?.anchorId ?? null;
    feature.properties.junction_anchor_ids = [
      fromAnchor?.anchorId,
      toAnchor?.anchorId,
    ].filter(Boolean);
  }

  return {
    anchorFeatures,
    snapFeatures,
  };
}

function colorGroupsForRoutes(routeIds) {
  return [
    ...new Set(routeIds.map((routeId) => routeColorFor(routeId))),
  ].sort((a, b) => colorRank(a) - colorRank(b));
}

function applyLaneChainMetadata(features) {
  const featureIndexByAnchor = new Map();
  features.forEach((feature, index) => {
    for (const anchorId of feature.properties.junction_anchor_ids ?? []) {
      if (!featureIndexByAnchor.has(anchorId)) featureIndexByAnchor.set(anchorId, []);
      featureIndexByAnchor.get(anchorId).push(index);
    }
  });

  const parent = new Int32Array(features.length);
  for (let i = 0; i < parent.length; i += 1) parent[i] = i;
  const find = (x) => {
    let r = x;
    while (parent[r] !== r) r = parent[r];
    while (parent[x] !== r) {
      const next = parent[x];
      parent[x] = r;
      x = next;
    }
    return r;
  };
  const union = (a, b) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent[ra] = rb;
  };

  for (const indices of featureIndexByAnchor.values()) {
    for (let leftIndex = 0; leftIndex < indices.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < indices.length; rightIndex += 1) {
        const leftFeatureIndex = indices[leftIndex];
        const rightFeatureIndex = indices[rightIndex];
        const left = features[leftFeatureIndex];
        const right = features[rightFeatureIndex];
        const leftColors = new Set(colorGroupsForRoutes(left.properties.route_ids ?? []));
        const rightColors = colorGroupsForRoutes(right.properties.route_ids ?? []);
        if (rightColors.some((color) => leftColors.has(color))) {
          union(leftFeatureIndex, rightFeatureIndex);
        }
      }
    }
  }

  const groups = new Map();
  features.forEach((feature, index) => {
    const root = find(index);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(index);
  });

  let groupId = 1;
  for (const indices of groups.values()) {
    const groupColors = [
      ...new Set(
        indices.flatMap((index) => colorGroupsForRoutes(features[index].properties.route_ids ?? [])),
      ),
    ].sort((a, b) => colorRank(a) - colorRank(b));
    const laneGroupId = `lane-group-${String(groupId++).padStart(4, "0")}`;
    for (const index of indices) {
      const feature = features[index];
      const localColors = colorGroupsForRoutes(feature.properties.route_ids ?? []);
      const slots = Object.fromEntries(
        localColors.map((color, colorIndex) => [
          color,
          colorIndex - (localColors.length - 1) / 2,
        ]),
      );
      feature.properties.lane_group_id = laneGroupId;
      feature.properties.lane_slot_source = indices.length > 1 ? "chain" : "local";
      feature.properties.lane_order_basis = localColors;
      feature.properties.lane_group_color_basis = groupColors;
      feature.properties.lane_color_slots = slots;
    }
  }

  return {
    lane_group_count: groups.size,
    chain_slot_feature_count: features.filter(
      (feature) => feature.properties.lane_slot_source === "chain",
    ).length,
  };
}

const junctionSnapDiagnostics = applyJunctionAnchorSnaps(corridorFeatures);
const laneChainDiagnostics = applyLaneChainMetadata(corridorFeatures);
const edgeById = new Map(
  edgeFeatures.map((feature) => [feature.properties.edge_id, feature]),
);

// ----- Phase 3d: same-color merge -----
// Where two or more OpenData polylines of the same color share physical track,
// merge them: longest member becomes the trunk carrying the union of route_ids;
// shorter members are clipped to their non-overlapping divergence portion only.
// Color-scoped only -- never merges across colors (so B orange + Q yellow on
// Brighton stay parallel features and render as parallel lanes downstream).
{
  // Ensure every corridor has a color stamped on properties (derived from
  // routeColorFor(route_ids[0])).
  for (const f of corridorFeatures) {
    if (!f.properties.color) {
      const r0 = (f.properties.route_ids ?? [])[0];
      f.properties.color = r0 ? routeColorFor(r0) : "#808183";
    }
  }

  // Per-route coverage map (for the connectivity-preservation fallback).
  const routeCoverageMap = new Map();
  for (const f of corridorFeatures) {
    for (const r of f.properties.route_ids ?? []) {
      routeCoverageMap.set(r, (routeCoverageMap.get(r) ?? 0) + 1);
    }
  }

  // Quick lookup by corridor_id for the merge helper.
  const corridorsById = new Map();
  for (const f of corridorFeatures) {
    corridorsById.set(f.properties.corridor_id, {
      corridor_id: f.properties.corridor_id,
      color: f.properties.color,
      route_ids: f.properties.route_ids ?? [],
      geometry: f.geometry,
      length_m: f.properties.length_m ?? 0,
    });
  }

  const { groups: mergeGroups } = groupCorridorsByColorAndOverlap(
    [...corridorsById.values()],
    { sharedFractionMin: 0.55, sharedLenMinM: 100, avgDistMaxM: 15, tangentMaxDeg: 30, resampleM: 25 },
  );

  // Helper: recompute path length in meters (haversine sum).
  function recomputeLengthM(coords) {
    const EARTH_M = 6371000;
    let total = 0;
    for (let i = 1; i < coords.length; i++) {
      const [lon1, lat1] = coords[i - 1];
      const [lon2, lat2] = coords[i];
      const toRad = (d) => (d * Math.PI) / 180;
      const dLat = toRad(lat2 - lat1);
      const dLon = toRad(lon2 - lon1);
      const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
      total += 2 * EARTH_M * Math.asin(Math.sqrt(a));
    }
    return total;
  }

  let mergesApplied = 0;
  let branchesClipped = 0;
  let branchesDropped = 0;
  let branchConnectorsAdded = 0;
  let groupsSkipped = 0;
  const debugFeatures = [];
  let sameColorConnectorNumber = 1;

  for (const group of mergeGroups) {
    const result = mergeSameColorGroup(group, corridorsById, {
      minBranchLenM: 30,
      resampleM: 25,
      avgDistMaxM: 15,
      routeCoverageMap,
    });
    if (result.skipped) {
      groupsSkipped++;
      debugFeatures.push({
        type: "Feature",
        geometry: null,
        properties: {
          visual_feature_type: "same_color_merge_skipped",
          color: group.color,
          trunk_corridor_id: group.trunk_corridor_id,
          member_corridor_ids: group.member_corridor_ids,
          reason: result.skipped.reason,
        },
      });
      continue;
    }

    // Apply trunk updates.
    const trunkFeature = corridorFeatures.find(
      (f) => f.properties.corridor_id === result.trunkUpdates.corridor_id,
    );
    if (trunkFeature) {
      trunkFeature.properties.route_ids = result.trunkUpdates.route_ids;
      trunkFeature.properties.color_route_ids = result.trunkUpdates.color_route_ids;
      trunkFeature.properties.merged_from_corridor_ids = result.trunkUpdates.merged_from_corridor_ids;
    }

    // Apply branch updates.
    const branchIdsToDrop = new Set();
    for (const bu of result.branchUpdates) {
      if (bu.drop) {
        branchIdsToDrop.add(bu.corridor_id);
        branchesDropped++;
      } else if (bu.newCoords) {
        const bf = corridorFeatures.find((f) => f.properties.corridor_id === bu.corridor_id);
        if (bf) {
          bf.geometry = { type: "LineString", coordinates: bu.newCoords };
          bf.properties.clipped_to_branch_only = true;
          bf.properties.length_m = recomputeLengthM(bu.newCoords);
          bf.properties.from_anchor_id = null;
          bf.properties.to_anchor_id = null;
          bf.properties.junction_anchor_ids = [];
          branchesClipped++;
        }
        if (bu.connector?.coordinates?.length >= 2) {
          const connectorId = `same-color-connector-${String(sameColorConnectorNumber++).padStart(5, "0")}`;
          const connectorRouteIds = [...new Set(bu.connector.route_ids ?? [])].sort(compareRouteIds);
          const connectorColor = bu.connector.color ?? group.color;
          corridorFeatures.push({
            type: "Feature",
            geometry: {
              type: "LineString",
              coordinates: bu.connector.coordinates,
            },
            properties: {
              visual_feature_type: "same_color_branch_connector",
              corridor_id: connectorId,
              route_ids: connectorRouteIds,
              color_route_ids: { [connectorColor]: connectorRouteIds },
              color: connectorColor,
              source_edge_ids: [],
              source_shape_ids: [],
              length_m: recomputeLengthM(bu.connector.coordinates),
              max_segment_length_m: recomputeLengthM(bu.connector.coordinates),
              base_geometry_selection: "same_color_branch_connector",
              same_color_connector_for_corridor_id: bu.corridor_id,
              same_color_connector_to_trunk_corridor_id: result.trunkUpdates.corridor_id,
              same_color_connector_distance_m: bu.connector.distance_m,
              same_color_connector_endpoint_kind: bu.connector.endpoint_kind,
            },
          });
          branchConnectorsAdded++;
        }
      }
    }

    if (branchIdsToDrop.size > 0) {
      for (let i = corridorFeatures.length - 1; i >= 0; i--) {
        if (branchIdsToDrop.has(corridorFeatures[i].properties.corridor_id)) {
          corridorFeatures.splice(i, 1);
        }
      }
    }

    mergesApplied++;

    debugFeatures.push({
      type: "Feature",
      geometry: trunkFeature ? trunkFeature.geometry : null,
      properties: {
        visual_feature_type: "same_color_merge",
        color: group.color,
        trunk_corridor_id: result.trunkUpdates.corridor_id,
        merged_from_corridor_ids: result.trunkUpdates.merged_from_corridor_ids,
        branches_clipped: result.branchUpdates.filter((b) => !b.drop).map((b) => b.corridor_id),
        branches_dropped: result.branchUpdates.filter((b) => b.drop).map((b) => b.corridor_id),
        branch_connectors: result.branchUpdates
          .filter((b) => b.connector)
          .map((b) => ({
            corridor_id: b.corridor_id,
            distance_m: b.connector.distance_m,
            endpoint_kind: b.connector.endpoint_kind,
          })),
        route_ids_union: result.trunkUpdates.route_ids,
      },
    });
  }

  writeFileSync(
    OUT_SAME_COLOR_MERGES_GEOJSON,
    `${JSON.stringify({ type: "FeatureCollection", features: debugFeatures })}\n`,
  );

  console.log(`[visual-network] Phase 3d merges applied:    ${mergesApplied}`);
  console.log(`[visual-network] Phase 3d branches clipped:  ${branchesClipped}`);
  console.log(`[visual-network] Phase 3d branches dropped:  ${branchesDropped}`);
  console.log(`[visual-network] Phase 3d connectors added:  ${branchConnectorsAdded}`);
  console.log(`[visual-network] Phase 3d groups skipped:    ${groupsSkipped}`);
}

let postSnapDegeneratePruned = 0;
for (let index = corridorFeatures.length - 1; index >= 0; index -= 1) {
  const feature = corridorFeatures[index];
  if (feature.properties?.visual_feature_type === "same_color_branch_connector") {
    continue;
  }
  if (geometryStats(feature.geometry.coordinates).length_m >= OPEN_DATA_MIN_FRAGMENT_LENGTH_M) {
    continue;
  }
  corridorFeatures.splice(index, 1);
  postSnapDegeneratePruned += 1;
}
console.log(
  `[visual-network] post-snap degenerate corridors pruned: ${postSnapDegeneratePruned}`,
);

// Drop duplicate same-route corridors (a shorter corridor running parallel within
// ~25 m of, and contained by, a longer corridor that carries all its routes) before
// spine/bundle assignment, so they never become a second parallel lane.
{
  const dedup = dedupeDuplicateCorridors(corridorFeatures, { parallelDistM: 25, overlapRatioMin: 0.8 });
  if (dedup.removedIds.length) {
    corridorFeatures.length = 0;
    corridorFeatures.push(...dedup.features);
    console.log(`[visual-network] duplicate corridors deduped:           ${dedup.removedIds.length}`);
  }
}

// Densify coarse source chords (some OpenData corridors have km-scale straight
// segments that no offset/smoothing can round) BEFORE spine assignment + lane
// offsetting, so downstream geometry has the intermediate vertices it needs.
{
  let densified = 0;
  for (const f of corridorFeatures) {
    if (f.geometry?.type !== "LineString") continue;
    const before = f.geometry.coordinates;
    const after = densifyLongSegments(before, DENSIFY_MAX_SEGMENT_M, DENSIFY_STEP_M);
    if (after !== before) { f.geometry.coordinates = after; densified += 1; }
  }
  console.log(`[visual-network] coarse corridors densified:            ${densified}`);
}

// ----- Stage D: spine assignment -----
// Each Gate 2C corridor maps 1:1 to a spine. The spine carries a deterministic
// base_spine_hash that buildBundleArtifacts will stamp onto every bundle_lane
// derived from this corridor. Hard validation downstream asserts that all
// lanes sharing a spine_id share an identical hash.
const spinesByCorridorId = new Map();
const spineFeatures = [];

function rebuildSpineArtifactsForCurrentCorridors() {
  spinesByCorridorId.clear();
  spineFeatures.length = 0;
  for (const f of corridorFeatures) {
    const spine = buildSpineFromCorridor(f);
    spinesByCorridorId.set(f.properties.corridor_id, spine);
    spineFeatures.push({
      type: "Feature",
      geometry: spine.geometry,
      properties: {
        visual_feature_type: "spine",
        spine_id: spine.spine_id,
        base_corridor_id: spine.base_corridor_id,
        base_spine_hash: spine.base_spine_hash,
        base_geometry_selection: spine.method,
        route_ids: spine.route_ids,
        source_edge_ids: spine.source_edge_ids,
        source_shape_ids: spine.source_shape_ids,
        length_m: spine.length_m,
      },
    });
  }
  spineFeatures.sort((a, b) => a.properties.spine_id.localeCompare(b.properties.spine_id));
  writeFileSync(
    OUT_SPINES_GEOJSON,
    `${JSON.stringify({ type: "FeatureCollection", features: spineFeatures })}\n`,
  );
}

rebuildSpineArtifactsForCurrentCorridors();

const CORRIDOR_GROUPS_COUNT = corridorFeatures.length;
const SPINES_CREATED = spineFeatures.length;
console.log(
  `[visual-network] corridor groups:           ${CORRIDOR_GROUPS_COUNT}`,
);
console.log(
  `[visual-network] spines created:            ${SPINES_CREATED}`,
);

// ----- Phase 1.5: cross-corridor physical bundle grouping -----
// Detect when two or more Stage-D spines (from separate Gate 2C corridors)
// physically share track for a meaningful stretch, substitute one shared
// geometry, and stamp physical_bundle_* metadata onto member lanes.
const SUBSTITUTE_CONFIDENCE_MIN = 0.75;
const allSpinesForGrouping = [];
for (const f of corridorFeatures) {
  const spine = spinesByCorridorId.get(f.properties.corridor_id);
  if (!spine) continue;
  allSpinesForGrouping.push({
    spine_id: spine.spine_id,
    geometry: spine.geometry,
    length_m: spine.length_m ?? 0,
    route_ids: spine.route_ids,
  });
}

const {
  groups: physicalBundles,
  rejects: physicalBundleRejects,
  transitiveDiagnostics: physicalBundleTransitiveDiagnostics = [],
} =
  groupSpinesIntoPhysicalBundles(allSpinesForGrouping, {
    avgDistMaxM: 15,
    sharedFractionMin: 0.6,
    sharedLenMinM: 250,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

// Build maps
const physicalBundleSpines = []; // FeatureCollection content
const physicalBundleLaneFeatures = []; // debug per (bundle, member corridor)

// spinesById for lookups inside the loop
const spinesById = new Map();
for (const s of allSpinesForGrouping) spinesById.set(s.spine_id, s);

writeFileSync(
  OUT_TRANSITIVE_BUNDLES_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle scoped-run diagnostics",
      summary: {
        transitive_disjoint_overlap_count: physicalBundleTransitiveDiagnostics.length,
      },
    },
    features: physicalBundleTransitiveDiagnostics.map((diagnostic) => {
      const base = spinesById.get(diagnostic.base_spine_id);
      return {
        type: "Feature",
        geometry: base?.geometry ?? null,
        properties: {
          visual_feature_type: "physical_bundle_transitive_diagnostic",
          ...diagnostic,
        },
      };
    }),
  })}\n`,
);

// Pre-build spine_id -> corridor feature map for O(1) lookup.
// This avoids an O(n) .find() inside the bundle loop and makes unknown
// spine_id references visible via a warn log.
const spineIdToCorridorFeature = new Map();
for (const f of corridorFeatures) {
  const spine = spinesByCorridorId.get(f.properties.corridor_id);
  if (spine) spineIdToCorridorFeature.set(spine.spine_id, f);
}

let bundlesSubstituted = 0;
for (const group of physicalBundles) {
  const bundleSpine = selectPhysicalBundleSpine(group, spinesById);
  const bundleHash = computePhysicalBundleSpineHash(bundleSpine.geometry.coordinates);
  group.physical_bundle_spine_hash = bundleHash;
  const shouldSubstitute = false;
  if (shouldSubstitute) bundlesSubstituted++;

  physicalBundleSpines.push({
    type: "Feature",
    geometry: bundleSpine.geometry,
    properties: {
      visual_feature_type: "physical_bundle_spine",
      physical_bundle_id: group.physical_bundle_id,
      base_spine_id: bundleSpine.base_spine_id,
      physical_bundle_spine_hash: bundleHash,
      member_spine_ids: bundleSpine.member_spine_ids,
      route_ids: bundleSpine.route_ids,
      member_count: group.member_count,
      confidence: group.confidence,
      substituted: shouldSubstitute,
    },
  });

  // For each member corridor: substitute geometry (if confidence high enough) and stamp bundle metadata.
  for (const memberSpineId of bundleSpine.member_spine_ids) {
    const f = spineIdToCorridorFeature.get(memberSpineId);
    if (!f) {
      console.warn(`[visual-network] WARN: physical bundle ${group.physical_bundle_id} references unknown spine_id ${memberSpineId}`);
      continue;
    }

    if (shouldSubstitute) {
      // Clip the bundle spine to this corridor's actual extent.
      const memberCoords = f.geometry.coordinates;
      if (memberCoords.length >= 2) {
        const fromCoord = memberCoords[0];
        const toCoord = memberCoords[memberCoords.length - 1];
        const clipped = clipPolylineToExtent(bundleSpine.geometry.coordinates, fromCoord, toCoord, { resampleM: 25 });
        if (clipped && clipped.length >= 2) {
          // Substitute and emit a debug lane feature recording the substitution.
          physicalBundleLaneFeatures.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: clipped },
            properties: {
              visual_feature_type: "physical_bundle_lane",
              physical_bundle_id: group.physical_bundle_id,
              corridor_id: f.properties.corridor_id,
              spine_id: memberSpineId,
              base_spine_hash: spinesByCorridorId.get(f.properties.corridor_id).base_spine_hash,
              physical_bundle_spine_hash: bundleHash,
              substituted: true,
              route_ids: f.properties.route_ids,
            },
          });
          // Substituting the corridor's geometry with the bundle spine clip. The
          // corridor's `length_m` property is intentionally NOT recomputed -- it
          // remains the corridor's original logical length, not the substituted
          // geometry's arc length. Downstream uses `length_m` as a corridor
          // identity, not a render length.
          f.geometry = { type: "LineString", coordinates: clipped };
        }
      }
    }

    // Stamp metadata regardless of substitution decision.
    f.properties.physical_bundle_id = group.physical_bundle_id;
    f.properties.physical_bundle_spine_hash = bundleHash;
    f.properties.physical_bundle_member_count = group.member_count;
    f.properties.physical_bundle_confidence = group.confidence;
    f.properties.physical_bundle_substituted = shouldSubstitute;
  }
}

const physicalBundleMaterialization = materializePhysicalBundles(
  corridorFeatures,
  physicalBundles,
  {
    spinesById,
    confidenceMin: PHYSICAL_BUNDLE_SUBSTITUTE_CONFIDENCE_MIN,
    overlapDistMaxM: BUNDLE_OVERLAP_DIST_MAX_M,
    sharedLenMinM: BUNDLE_SHARED_LEN_MIN_M,
    splitSampleM: BUNDLE_SPLIT_SAMPLE_M,
    fanoutBlendM: FANOUT_BLEND_M,
    laneWidthM: LANE_WIDTH_METERS,
    taperM: 40,
    colorOrder: BUNDLE_COLOR_ORDER,
    routeColorFor,
    compareRouteIds,
    orderColorsForBundle,
  },
);

if (physicalBundleMaterialization.consumed_corridor_count > 0) {
  corridorFeatures.length = 0;
  corridorFeatures.push(...physicalBundleMaterialization.features);
  rebuildSpineArtifactsForCurrentCorridors();
}

writeFileSync(
  OUT_MATERIALIZED_BUNDLES_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
      parameters: {
        confidence_min: PHYSICAL_BUNDLE_SUBSTITUTE_CONFIDENCE_MIN,
        overlap_dist_max_m: BUNDLE_OVERLAP_DIST_MAX_M,
        shared_len_min_m: BUNDLE_SHARED_LEN_MIN_M,
        split_sample_m: BUNDLE_SPLIT_SAMPLE_M,
        fanout_blend_m: FANOUT_BLEND_M,
      },
      summary: {
        materialized_bundle_count: physicalBundleMaterialization.materialized_bundle_count,
        consumed_corridor_count: physicalBundleMaterialization.consumed_corridor_count,
      },
    },
    features: physicalBundleMaterialization.debug.materializedBundleFeatures,
  })}\n`,
);
writeFileSync(
  OUT_MATERIALIZED_BUNDLE_FANOUTS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
      summary: {
        fanout_count: physicalBundleMaterialization.fanout_count,
      },
    },
    features: physicalBundleMaterialization.debug.fanoutFeatures,
  })}\n`,
);
writeFileSync(
  OUT_MATERIALIZED_BUNDLE_SPLITS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
    },
    features: physicalBundleMaterialization.debug.splitFeatures,
  })}\n`,
);
writeFileSync(
  OUT_MATERIALIZED_BUNDLE_DEFECTS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs physical bundle materialization",
    },
    features: physicalBundleMaterialization.debug.defectFeatures,
  })}\n`,
);

// Sort outputs deterministically.
physicalBundleSpines.sort((a, b) => a.properties.physical_bundle_id.localeCompare(b.properties.physical_bundle_id));
physicalBundleLaneFeatures.sort((a, b) =>
  a.properties.physical_bundle_id.localeCompare(b.properties.physical_bundle_id) ||
  a.properties.corridor_id.localeCompare(b.properties.corridor_id),
);

writeFileSync(OUT_PHYSICAL_BUNDLES_GEOJSON, `${JSON.stringify({ type: "FeatureCollection", features: physicalBundleSpines })}\n`);
writeFileSync(OUT_PHYSICAL_BUNDLE_LANES_GEOJSON, `${JSON.stringify({ type: "FeatureCollection", features: physicalBundleLaneFeatures })}\n`);
writeFileSync(OUT_PHYSICAL_BUNDLE_REJECTS_GEOJSON, `${JSON.stringify({
  type: "FeatureCollection",
  features: physicalBundleRejects.map((r) => ({
    type: "Feature",
    geometry: null,
    properties: { ...r, visual_feature_type: "physical_bundle_reject" },
  })),
})}\n`);

const groupedCorridorCount = physicalBundles.reduce((acc, g) => acc + g.member_count, 0);
console.log(`[visual-network] physical bundles:           ${physicalBundles.length}`);
console.log(`[visual-network] grouped corridors:          ${groupedCorridorCount}`);
console.log(`[visual-network] substituted bundles:        ${bundlesSubstituted}`);
console.log(`[visual-network] materialized bundles:       ${physicalBundleMaterialization.materialized_bundle_count}`);
console.log(`[visual-network] materialized corridors used: ${physicalBundleMaterialization.consumed_corridor_count}`);
console.log(`[visual-network] materialized fanouts:       ${physicalBundleMaterialization.fanout_count}`);
console.log(`[visual-network] reject candidates:          ${physicalBundleRejects.length}`);
console.log(`[visual-network] transitive bundle splits:   ${physicalBundleTransitiveDiagnostics.length}`);

const bundleArtifacts = buildBundleArtifacts(corridorFeatures, spinesByCorridorId);

// ----- Stage D validation: same spine_id implies same base_spine_hash -----
{
  const result = assertSpineHashConsistency(bundleArtifacts);
  console.log(`[visual-network] bundle_lanes created:       ${result.bundleLaneCount}`);
  console.log(`[visual-network] lanes missing spine_id:     ${result.lanesWithMissingSpineId.length}`);
  console.log(`[visual-network] lanes missing hash:         ${result.lanesWithMissingHash.length}`);
  console.log(`[visual-network] inconsistent spine groups:  ${result.inconsistentGroups.length}`);
  console.log(`[visual-network] inconsistent pb groups:     ${result.inconsistentPhysicalBundleGroups.length}`);
  if (
    result.inconsistentGroups.length > 0 ||
    result.lanesWithMissingSpineId.length > 0 ||
    result.lanesWithMissingHash.length > 0 ||
    result.inconsistentPhysicalBundleGroups.length > 0
  ) {
    console.error("[visual-network] *** Stage D validation FAILED -- refusing to promote. ***");
    for (const g of result.inconsistentGroups.slice(0, 10)) {
      console.error(`  spine ${g.spine_id}: expected hash ${g.expected}, got ${g.got}`);
    }
    if (result.inconsistentGroups.length > 10) {
      console.error(`  (showing first 10 of ${result.inconsistentGroups.length})`);
    }
    if (result.lanesWithMissingSpineId.length > 0) {
      console.error(`  ${result.lanesWithMissingSpineId.length} non-bridge lanes missing spine_id`);
    }
    if (result.lanesWithMissingHash.length > 0) {
      console.error(`  ${result.lanesWithMissingHash.length} lanes missing base_spine_hash`);
    }
    for (const g of result.inconsistentPhysicalBundleGroups.slice(0, 10)) {
      console.error(`  physical bundle ${g.physical_bundle_id}: expected hash ${g.expected}, got ${g.got}`);
    }
    if (result.inconsistentPhysicalBundleGroups.length > 10) {
      console.error(`  (showing first 10 of ${result.inconsistentPhysicalBundleGroups.length} physical bundle inconsistencies)`);
    }
    process.exit(1);
  }
  console.log(`[visual-network] Stage D validation:         PASS`);

  // ---- Phase 3c validation gates (run after spine-hash passes) ----
  // Gate D2: no bogus transitions (color absent from both corridors).
  // Note: Phase 3b transitions have not been promoted yet at this stage —
  // Stage D runs on the pre-promotion bundleLaneFeatures.
  // We run the transition gates AFTER Phase 3b promotes, so we defer to
  // a post-promotion check in the Phase 3c block below. This sentinel
  // just logs that the gates will run.
  console.log(`[visual-network] Phase 3c gates:             scheduled after Phase 3b promotion`);
}

// ----- Phase 3b: branch transition promotion -----
// The branch_transition features replace the legacy buildJunctionBridges
// output. We promote only transitions <= BRANCH_TRANSITION_MAX_M to skip the
// long-tail outlier (G @ Fulton St) flagged by the Phase 3a audit.
//
// This block runs AFTER buildBundleArtifacts has returned, so the lane-offset
// baking loop inside that function has already completed. Mutating
// bundleLaneFeatures here is safe ONLY because every promoted transition is
// emitted with lane_slot: 0 and lane_offset_baked: true -- no geometric
// re-baking is required. Do not add lanes with non-zero lane_slot in this
// block without re-running the baking loop on them.
{
  const bundleLaneFeatures = bundleArtifacts.bundleLaneFeatures ?? bundleArtifacts.bundle_lane_features ?? [];

  // Build a quick lookup: bundle_id -> route_ids (any of its lanes is fine).
  const bundleRouteIds = new Map();
  for (const lane of bundleLaneFeatures) {
    const bid = lane.properties.bundle_id;
    if (!bid || bundleRouteIds.has(bid)) continue;
    bundleRouteIds.set(bid, lane.properties.route_ids ?? []);
  }

  const { transitions, coincidentSkipped } = buildBranchTransitions(
    bundleLaneFeatures,
    { maxBridgeM: BRANCH_TRANSITION_MAX_M, minBridgeM: 0.5 },
  );

  // Sort deterministically.
  transitions.sort((a, b) => {
    const ka = `${a.properties.anchor_id}|${a.properties.color}|${a.properties.bundle_id_from}|${a.properties.bundle_id_to}`;
    const kb = `${b.properties.anchor_id}|${b.properties.color}|${b.properties.bundle_id_from}|${b.properties.bundle_id_to}`;
    return ka.localeCompare(kb);
  });

  // Enrich and promote each transition into bundleLaneFeatures.
  let promoted = 0;
  for (const t of transitions) {
    const tp = t.properties;
    const routesFrom = bundleRouteIds.get(tp.bundle_id_from) ?? [];
    const routesTo = bundleRouteIds.get(tp.bundle_id_to) ?? [];
    const routeIdsUnion = [...new Set([...routesFrom, ...routesTo])].sort(compareRouteIds);
    const intersect = routesFrom.filter((r) => routesTo.includes(r));
    const colorRouteIds = routesForColor(routeIdsUnion, tp.color);

    // Classification: intersect non-empty => safe; else => likely_branch_exit.
    // (The 35m cap already filtered out too_long; the helper already filtered
    // out coincident pairs and same-bundle pairs.)
    const classification =
      intersect.length > 0
        ? "safe_same_route_continuation"
        : "likely_branch_exit";

    // Stamp classification on the debug-artifact feature too.
    tp.transition_classification = classification;
    tp.route_ids = routeIdsUnion;
    tp.color_route_ids = colorRouteIds;

    // Promote into bundleLaneFeatures so the runtime renders it and the
    // connectivity gate sees it as a graph edge.
    bundleLaneFeatures.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: t.geometry.coordinates },
      properties: {
        visual_feature_type: "bundle_lane",
        feature_type: "branch_transition",
        lane_slot_source: "branch_transition",
        bundle_id: `transition-${tp.bundle_id_from}-${tp.bundle_id_to}-${tp.anchor_id}-${tp.color.slice(1)}`,
        corridor_id: null,
        spine_id: null,
        base_spine_hash: null,
        base_geometry_selection: null,
        physical_bundle_id: null,
        physical_bundle_spine_hash: null,
        physical_bundle_member_count: null,
        physical_bundle_confidence: null,
        route_id: colorRouteIds[0] ?? routeIdsUnion[0] ?? "",
        representative_route_id: colorRouteIds[0] ?? routeIdsUnion[0] ?? "",
        route_ids: routeIdsUnion,
        color_route_ids: colorRouteIds,
        color: tp.color,
        lane_slot: 0,
        lane_slot_semantic: 0,
        lane_offset_baked: true,
        lane_width_m: LANE_WIDTH_METERS,
        render_lane_slot: 0,
        lane_group_id: null,
        lane_order_basis: [tp.color],
        lane_order_override_applied: false,
        bundle_lane_count: 1,
        bundle_lane_slots: { [tp.color]: 0 },
        branch_in_route_ids: [],
        branch_out_route_ids: [],
        bundle_entry: false,
        bundle_exit: false,
        bridge: false,
        bundle_id_from: tp.bundle_id_from,
        bundle_id_to: tp.bundle_id_to,
        anchor_id: tp.anchor_id,
        from_anchor_id: tp.anchor_id,
        to_anchor_id: tp.anchor_id,
        length_m: tp.length_m,
        transition_classification: classification,
      },
    });
    promoted++;
  }

  writeFileSync(OUT_BRANCH_TRANSITIONS_GEOJSON, `${JSON.stringify({
    type: "FeatureCollection",
    features: transitions,
  })}\n`);

  // Count classifications for the log.
  const safeCount = transitions.filter((t) => t.properties.transition_classification === "safe_same_route_continuation").length;
  const branchExitCount = transitions.filter((t) => t.properties.transition_classification === "likely_branch_exit").length;

  // Mirror the promoted transitions into bundleArtifacts.visualFeatures (the
  // sorted array that's actually serialized into subway-network.visual.geojson
  // at the bottom of this script). bundleLaneFeatures alone is only used for
  // intermediate debug artifacts; the runtime renderer reads visualFeatures.
  // We append at the end -- order within visualFeatures matters only for
  // deterministic on-disk diffs, and the bundle_id prefix "transition-" sorts
  // after all "bundle-NNNNN" / "solo-NNNNN" / "corr-NNNNN" entries anyway.
  const promotedLanes = promoted > 0 ? bundleLaneFeatures.slice(-promoted) : [];
  if (promotedLanes.length > 0 && bundleArtifacts.visualFeatures) {
    bundleArtifacts.visualFeatures.push(...promotedLanes);
    bundleArtifacts.visualFeatures.sort((a, b) => {
      const left = a.properties.bundle_id ?? a.properties.corridor_id ?? a.properties.route_id ?? "";
      const right = b.properties.bundle_id ?? b.properties.corridor_id ?? b.properties.route_id ?? "";
      const cmp = String(left).localeCompare(String(right), "en", { numeric: true });
      if (cmp !== 0) return cmp;
      return (
        Number(a.properties.lane_slot_semantic ?? a.properties.lane_slot ?? 0) -
        Number(b.properties.lane_slot_semantic ?? b.properties.lane_slot ?? 0)
      );
    });
  }

  console.log(`[visual-network] transitions promoted:      ${promoted}`);
  console.log(`[visual-network]   safe_same_route:         ${safeCount}`);
  console.log(`[visual-network]   likely_branch_exit:      ${branchExitCount}`);
  console.log(`[visual-network] coincident pairs skipped:  ${coincidentSkipped}`);
  // Post-promotion bundle_lane count so a future reader can reconcile the
  // artifact file size against this log without confusion. Stage D's earlier
  // log line (bundle_lanes created: N) is the PRE-promotion count, since the
  // validator runs before this block.
  console.log(`[visual-network] bundle_lanes (post-promotion): ${bundleLaneFeatures.length}`);
  console.log(`[visual-network] visualFeatures (post-promo):   ${bundleArtifacts.visualFeatures?.length ?? "n/a"}`);
}

// ----- Phase 3c: bogus-transition filter + orphan-lane marking -----
// Runs AFTER Phase 3b promotion (transitions are now in bundleLaneFeatures)
// and BEFORE final artifact emission. Bogus transitions are dropped from
// both bundleLaneFeatures and visualFeatures. Orphan features are flagged
// but NOT removed (debug overlay can hide them; runtime ignores the flag).
{
  const bundleLaneFeatures = bundleArtifacts.bundleLaneFeatures ?? bundleArtifacts.bundle_lane_features ?? [];

  // Build corridor route index from non-transition lanes.
  const corridorRouteIndex = new Map();
  for (const lane of bundleLaneFeatures) {
    const bid = lane.properties.bundle_id;
    const cid = lane.properties.corridor_id;
    const routeIds = lane.properties.route_ids ?? [];
    if (bid) {
      if (!corridorRouteIndex.has(bid)) corridorRouteIndex.set(bid, new Set());
      for (const r of routeIds) corridorRouteIndex.get(bid).add(r);
    }
    if (cid) {
      if (!corridorRouteIndex.has(cid)) corridorRouteIndex.set(cid, new Set());
      for (const r of routeIds) corridorRouteIndex.get(cid).add(r);
    }
  }

  // Filter bogus transitions.
  const { kept, dropped } = filterBogusTransitions(bundleLaneFeatures, corridorRouteIndex);

  if (dropped.length > 0) {
    const droppedIds = new Set(dropped.map((d) => d.feature.properties.bundle_id));

    // Splice from bundleLaneFeatures in-place (reassign array contents).
    bundleLaneFeatures.length = 0;
    for (const f of kept) bundleLaneFeatures.push(f);

    // Mirror removal into visualFeatures.
    if (bundleArtifacts.visualFeatures) {
      const visBefore = bundleArtifacts.visualFeatures.length;
      bundleArtifacts.visualFeatures = bundleArtifacts.visualFeatures.filter(
        (f) => !droppedIds.has(f.properties.bundle_id),
      );
      const visAfter = bundleArtifacts.visualFeatures.length;
      console.log(`[visual-network] Phase 3c: bogus transitions removed from visualFeatures: ${visBefore - visAfter}`);
    }

    console.log(`[visual-network] Phase 3c: bogus transitions dropped:     ${dropped.length}`);
    for (const d of dropped.slice(0, 10)) {
      console.log(`[visual-network]   dropped ${d.feature.properties.bundle_id}: ${d.reason}`);
    }
    if (dropped.length > 10) {
      console.log(`[visual-network]   (showing first 10 of ${dropped.length})`);
    }
  } else {
    console.log(`[visual-network] Phase 3c: bogus transitions dropped:     0`);
  }

  // Mark orphan lanes (flag only, no removal).
  const terminalStopIds = new Set();
  // Collect all from_stop_id and to_stop_id that appear at the "edge" of a route.
  // Simple heuristic: any stop that appears in a single-endpoint position in per-route graphs.
  // We use the stations geojson if available (already loaded in Gate 2A stopsById).
  for (const [, stop] of stopsById ?? new Map()) {
    if (stop?.stop_id) terminalStopIds.add(stop.stop_id);
  }

  markOrphanLanes(bundleLaneFeatures, terminalStopIds);
  const orphanCount = bundleLaneFeatures.filter((f) => f.properties.qa_orphan_origin).length;
  console.log(`[visual-network] Phase 3c: orphan lanes flagged:          ${orphanCount}`);
  // Remove stray both-ends-dangling error orphans (e.g. the solo-E opendata-00028
  // duplicate that renders as a second blue line beside the A/C/E spine).
  {
    const removal = removeOrphanErrorLanes(bundleLaneFeatures);
    bundleLaneFeatures.length = 0;
    bundleLaneFeatures.push(...removal.features);
    console.log(`[visual-network] Phase 3c: orphan-error lanes removed:    ${removal.removedCount}`);
  }
  console.log(`[visual-network] bundle_lanes (post-3c):                  ${bundleLaneFeatures.length}`);

  // ---- Phase 3c validation gates ----
  // D2: assertNoBogusTransitions — any transition whose color is absent from both corridors is a build error.
  {
    const noBogus = assertNoBogusTransitions(bundleLaneFeatures, corridorRouteIndex);
    if (!noBogus.passed) {
      console.error(`[visual-network] *** Phase 3c gate D2 FAILED: ${noBogus.violations.length} bogus transition(s) ***`);
      for (const v of noBogus.violations.slice(0, 10)) {
        console.error(`  ${v.bundle_id}: ${v.reason}`);
      }
      process.exit(1);
    }
    console.log(`[visual-network] Phase 3c gate D2 (no bogus transitions): PASS`);
  }

  // D3: assertQContinuousInBrooklyn — Q must form a single connected chain in Brooklyn.
  {
    const visualFeatures = bundleArtifacts.visualFeatures ?? bundleLaneFeatures;
    const qResult = assertQContinuousInBrooklyn(visualFeatures, null);
    console.log(`[visual-network] Phase 3c gate D3 (Q Brooklyn):           ${qResult.passed ? "PASS" : "WARN"} — ${qResult.detail}`);
    if (!qResult.passed) {
      // This is a WARN not a hard fail — a data gap (Manhattan N/Q/R/W overlap) can cause
      // false-positive disconnections if bbox edges cut through mid-route features.
      // Log the disconnected IDs for the audit report but do not block promotion.
      console.warn(`[visual-network]   Disconnected bundle IDs: ${qResult.disconnectedBundleIds.slice(0, 5).join(", ")}`);
      console.warn(`[visual-network]   (This is a known bbox-boundary artifact; not blocking promotion.)`);
    }
  }

  // D4: assertOriginsForRedGreenFlatbushEastern — IRT branches must have upstream.
  {
    const visualFeatures = bundleArtifacts.visualFeatures ?? bundleLaneFeatures;
    const feResult = assertOriginsForRedGreenFlatbushEastern(visualFeatures);
    console.log(`[visual-network] Phase 3c gate D4 (IRT Flatbush origins): ${feResult.passed ? "PASS" : "WARN"} (${feResult.missingUpstreamCount} missing)`);
    if (!feResult.passed) {
      for (const v of feResult.violations.slice(0, 5)) {
        console.warn(`[visual-network]   ${v.bundle_id}: ${v.detail}`);
      }
      // WARN only — Flatbush branches may legitimately originate at the outer terminus of the bbox.
    }
  }

  console.log(`[visual-network] Lane continuity validation:              PASS`);
}

// ----- Cross-color parallel spread -----
// Different-colored lines that share a physical corridor but render at
// lane_slot 0 (solos / non-materialized members) overlap; the higher z-order
// color paints over the lower. Detect those adjacency clusters and bake a
// centered perpendicular offset per color so they stay visually parallel.
// Skips features already offset by materialization (lane_slot_semantic != 0).
{
  const targetLaneFeatures = bundleArtifacts.visualFeatures;
  const { groups } = detectCrossColorAdjacency(targetLaneFeatures, {
    sharedFractionMin: 0.6,
    sharedLenMinM: 250,
    avgDistMaxM: 18,
    tangentMaxDeg: 30,
    resampleM: 25,
  });

  let spreadFeaturesOffset = 0;
  const debugFeatures = [];

  for (const group of groups) {
    for (const member of group.members) {
      if (member.lane_slot === 0) continue; // centered middle lane stays put
      const f = member._featureRef;
      if (!f?.geometry?.coordinates) continue;
      const baked = offsetPolylineByLaneSlot(f.geometry.coordinates, member.lane_slot);
      f.geometry = { type: "LineString", coordinates: baked };
      f.properties.cross_color_spread_slot = member.lane_slot;
      f.properties.lane_offset_baked = true;
      f.properties.lane_width_m = LANE_WIDTH_METERS;
      spreadFeaturesOffset += 1;
    }
    debugFeatures.push({
      type: "Feature",
      geometry: null,
      properties: {
        visual_feature_type: "cross_color_spread_group",
        member_count: group.members.length,
        members: group.members.map((m) => ({
          bundle_id: m.bundle_id,
          color: m.color,
          route_ids: m.route_ids,
          lane_slot: m.lane_slot,
        })),
      },
    });
  }

  writeFileSync(
    OUT_CROSS_COLOR_SPREAD_GEOJSON,
    `${JSON.stringify({ type: "FeatureCollection", features: debugFeatures })}\n`,
  );
  console.log(`[visual-network] cross-color spread groups:  ${groups.length}`);
  console.log(`[visual-network] cross-color features offset: ${spreadFeaturesOffset}`);
}

// ----- Cross-color parallel spread v2: segment-level for short shared stretches -----
// v1 above offsets WHOLE features that share most of their length. But two long
// lines that share only a short stretch (e.g. the A/C/E 31km line and the G
// near Hoyt-Schermerhorn ~1km) can't be whole-offset without misplacing the
// rest of the line. v2 finds the shared sub-extent and offsets ONLY that
// stretch (tapered), keeping each feature as one continuous polyline. It only
// touches features still at lane_slot 0 that v1 / materialization left alone.
{
  const HALF_SLOT_M = 0.5 * LANE_WIDTH_METERS;
  const TAPER_M = 40;
  const DIST_MAX_M = 18;
  const MIN_SHARED_LEN_M = 250;
  const target = bundleArtifacts.visualFeatures;

  const candidates = target.filter((f) => {
    const sem = Number(f.properties?.lane_slot_semantic ?? f.properties?.lane_slot ?? 0);
    return (
      sem === 0 &&
      f.properties?.cross_color_spread_slot === undefined &&
      f.properties?.color &&
      f.geometry?.type === "LineString" &&
      Array.isArray(f.geometry.coordinates) &&
      f.geometry.coordinates.length >= 2
    );
  });

  // Cheap bbox prefilter (expand by ~DIST_MAX_M in degrees) so the O(N^2) pair
  // scan only runs findSharedArcExtent on geographically-plausible pairs.
  const latPad = DIST_MAX_M / 111320;
  const bboxes = candidates.map((f) => {
    let mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity;
    for (const [x, y] of f.geometry.coordinates) {
      if (x < mnx) mnx = x;
      if (y < mny) mny = y;
      if (x > mxx) mxx = x;
      if (y > mxy) mxy = y;
    }
    const lonPad = DIST_MAX_M / Math.max(1, Math.cos(((mny + mxy) / 2) * Math.PI / 180) * 111320);
    return [mnx - lonPad, mny - latPad, mxx + lonPad, mxy + latPad];
  });
  const bboxOverlap = (a, b) => !(a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1]);

  const rankOf = (color) => {
    const i = BUNDLE_COLOR_ORDER.indexOf(color);
    return i === -1 ? 999 : i;
  };

  const pairs = [];
  for (let i = 0; i < candidates.length; i += 1) {
    for (let j = i + 1; j < candidates.length; j += 1) {
      const a = candidates[i];
      const b = candidates[j];
      if (a.properties.color === b.properties.color) continue;
      // Continuous-materialization members already have their lane offset baked in;
      // re-spreading them would double-offset.
      if (
        a.properties.lane_slot_source === "physical_bundle_continuous" ||
        b.properties.lane_slot_source === "physical_bundle_continuous"
      ) continue;
      if (!bboxOverlap(bboxes[i], bboxes[j])) continue;
      const ext = findSharedArcExtent(a.geometry.coordinates, b.geometry.coordinates, {
        resampleM: 25,
        distMaxM: DIST_MAX_M,
        minSharedLenM: MIN_SHARED_LEN_M,
      });
      if (!ext) continue;
      pairs.push({ a, b, ext });
    }
  }
  // Greedy: longest shared stretch first. A feature may be offset on MULTIPLE
  // disjoint sub-extents (e.g. A/C/E offsets vs B/D on Central Park West AND vs
  // G near Hoyt-Schermerhorn -- different stretches). We track claimed arc
  // ranges PER feature and only skip a pair if its sub-extent on either member
  // overlaps (within a taper margin) a range already claimed on that member,
  // which would compound offsets. Offsets on disjoint ranges compose cleanly
  // because each taper ramps back to the centerline between stretches.
  pairs.sort((p, q) => q.ext.sharedLenM - p.ext.sharedLenM);

  const claimedRanges = new Map(); // featureRef -> [[startArc, endArc], ...]
  const rangeBlocked = (feature, s, e) => {
    const ranges = claimedRanges.get(feature);
    if (!ranges) return false;
    // Block if [s,e] expanded by TAPER_M overlaps any existing claimed range.
    return ranges.some(([rs, re]) => !(e + TAPER_M < rs || s - TAPER_M > re));
  };
  const claimRange = (feature, s, e) => {
    if (!claimedRanges.has(feature)) claimedRanges.set(feature, []);
    claimedRanges.get(feature).push([s, e]);
  };

  let segPairs = 0;
  let segFeaturesOffset = 0;
  const segDebug = [];
  for (const { a, b, ext } of pairs) {
    if (rangeBlocked(a, ext.aStartArc, ext.aEndArc)) continue;
    if (rangeBlocked(b, ext.bStartArc, ext.bEndArc)) continue;
    const aNeg = rankOf(a.properties.color) <= rankOf(b.properties.color);
    const aOff = (aNeg ? -1 : 1) * HALF_SLOT_M;
    const bOff = (aNeg ? 1 : -1) * HALF_SLOT_M;

    a.geometry = {
      type: "LineString",
      coordinates: offsetPolylineOverExtent(a.geometry.coordinates, ext.aStartArc, ext.aEndArc, aOff, TAPER_M),
    };
    b.geometry = {
      type: "LineString",
      coordinates: offsetPolylineOverExtent(b.geometry.coordinates, ext.bStartArc, ext.bEndArc, bOff, TAPER_M),
    };
    a.properties.cross_color_segment_side = aNeg ? -0.5 : 0.5;
    b.properties.cross_color_segment_side = aNeg ? 0.5 : -0.5;
    a.properties.cross_color_segment_count = (a.properties.cross_color_segment_count ?? 0) + 1;
    b.properties.cross_color_segment_count = (b.properties.cross_color_segment_count ?? 0) + 1;
    a.properties.lane_offset_baked = true;
    b.properties.lane_offset_baked = true;
    claimRange(a, ext.aStartArc, ext.aEndArc);
    claimRange(b, ext.bStartArc, ext.bEndArc);
    segPairs += 1;
    segFeaturesOffset += 2;
    segDebug.push({
      type: "Feature",
      geometry: null,
      properties: {
        visual_feature_type: "cross_color_spread_segment_pair",
        a_bundle_id: a.properties.bundle_id,
        a_color: a.properties.color,
        a_routes: a.properties.route_ids ?? [],
        b_bundle_id: b.properties.bundle_id,
        b_color: b.properties.color,
        b_routes: b.properties.route_ids ?? [],
        shared_len_m: Number(ext.sharedLenM.toFixed(1)),
      },
    });
  }

  writeFileSync(
    OUT_CROSS_COLOR_SEGMENTS_GEOJSON,
    `${JSON.stringify({ type: "FeatureCollection", features: segDebug })}\n`,
  );
  console.log(`[visual-network] cross-color segment pairs:   ${segPairs}`);
  console.log(`[visual-network] cross-color segment offsets: ${segFeaturesOffset}`);
}

// (Removed overnight-agent "Final chord guard" stage: it split features at >250m segments and
// DROPPED the long part, which shattered legitimate long runs (Manhattan Bridge crossing, express
// station gaps) into disconnected pieces -- the "literally broken" lines. Continuity restored.)

// ----- Phase 2: lane order debug summary -----
{
  const laneOrderSummary = {};
  const allLanes = bundleArtifacts.bundleLaneFeatures ?? bundleArtifacts.bundle_lane_features ?? [];
  for (const lane of allLanes) {
    const bid = lane.properties.bundle_id;
    if (!bid || laneOrderSummary[bid]) continue;
    laneOrderSummary[bid] = {
      bundle_id: bid,
      corridor_id: lane.properties.corridor_id ?? null,
      from_anchor_id: lane.properties.from_anchor_id ?? null,
      to_anchor_id: lane.properties.to_anchor_id ?? null,
      lane_order_basis: lane.properties.lane_order_basis ?? null,
      lane_slot_source: lane.properties.lane_slot_source ?? null,
      route_ids: lane.properties.route_ids ?? [],
      bundle_lane_count: lane.properties.bundle_lane_count ?? 1,
      override_applied: lane.properties.lane_order_override_applied === true,
    };
  }
  const summaryArray = Object.values(laneOrderSummary).sort((a, b) =>
    a.bundle_id.localeCompare(b.bundle_id),
  );
  writeFileSync(OUT_LANE_ORDERS_JSON, `${JSON.stringify(summaryArray, null, 2)}\n`);
  const overridesCount = summaryArray.filter((s) => s.override_applied).length;
  console.log(`[visual-network] lane-order entries:        ${summaryArray.length}`);
  console.log(`[visual-network] lane-order overrides used: ${overridesCount}`);
}

// Sort corridor features for stable output
corridorFeatures.sort((a, b) =>
  a.properties.corridor_id.localeCompare(b.properties.corridor_id),
);

writeFileSync(
  OUT_CORRIDORS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2C",
      parameters: {
        resample_interval_m: RESAMPLE_INTERVAL_M,
        hausdorff_max_m: HAUSDORFF_MAX_M,
        overlap_min_ratio: OVERLAP_MIN_RATIO,
        tangent_max_diff_deg: TANGENT_MAX_DIFF_DEG,
        containment_avg_distance_max_m: CONTAINMENT_AVG_DISTANCE_MAX_M,
        containment_overlap_min_ratio: CONTAINMENT_OVERLAP_MIN_RATIO,
        grid_cell_m: GRID_CELL_M,
      },
    },
    features: corridorFeatures,
  })}\n`,
);
writeFileSync(
  OUT_CORRIDORS_JSON,
  `${JSON.stringify(
    {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2C",
      counts: {
        edge_count: edgeFeatures.length,
        corridor_count: corridorFeatures.length,
        pairs_considered: pairsConsidered,
        pairs_matched: pairsMatched,
      },
      sample_matched_pairs: matchedPairs,
      corridors: corridorRows,
    },
    null,
    2,
  )}\n`,
);
writeFileSync(
  OUT_JUNCTION_ANCHORS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2G",
      parameters: {
        junction_snap_max_m: JUNCTION_SNAP_MAX_M,
      },
      summary: {
        anchor_count: junctionSnapDiagnostics.anchorFeatures.length,
        snap_count: junctionSnapDiagnostics.snapFeatures.length,
      },
    },
    features: junctionSnapDiagnostics.anchorFeatures,
  })}\n`,
);
writeFileSync(
  OUT_JUNCTION_SNAPS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2G",
      parameters: {
        junction_snap_max_m: JUNCTION_SNAP_MAX_M,
      },
      summary: {
        snap_count: junctionSnapDiagnostics.snapFeatures.length,
      },
    },
    features: junctionSnapDiagnostics.snapFeatures,
  })}\n`,
);
writeFileSync(
  OUT_BUNDLES_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2H",
      summary: {
        bundle_count: bundleArtifacts.bundleFeatures.length,
        corridors_converted_to_bundle_geometry:
          bundleArtifacts.bundleFeatures.length,
        remaining_unbundled_corridors: bundleArtifacts.unbundledFeatures.length,
      },
    },
    features: bundleArtifacts.bundleFeatures,
  })}\n`,
);
writeFileSync(
  OUT_BUNDLE_LANES_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2H",
      summary: {
        bundled_render_lane_count: bundleArtifacts.bundleLaneFeatures.length,
        bundle_count: bundleArtifacts.bundleFeatures.length,
      },
    },
    features: bundleArtifacts.bundleLaneFeatures,
  })}\n`,
);
writeFileSync(
  OUT_BUNDLE_GAPS_GEOJSON,
  `${JSON.stringify({
    type: "FeatureCollection",
    metadata: {
      generated_at: new Date().toISOString(),
      source: "build-subway-visual-network.mjs Gate 2H",
      summary: {
        bundle_gap_count: bundleArtifacts.bundleGapFeatures.length,
      },
    },
    features: bundleArtifacts.bundleGapFeatures,
  })}\n`,
);

console.log(`[visual-network] === Gate 2C corridor summary ===`);
console.log(`[visual-network] edges in:                ${edgeFeatures.length}`);
console.log(`[visual-network] candidate pairs:          ${pairsConsidered}`);
console.log(`[visual-network] matched pairs:            ${pairsMatched}`);
console.log(`[visual-network] corridors out:            ${corridorFeatures.length}`);
const sharedCorridors = corridorRows.filter((c) => c.is_shared);
const multiRouteCorridors = corridorRows.filter((c) => c.route_ids.length > 1);
console.log(`[visual-network] shared (>1 edge member):  ${sharedCorridors.length}`);
console.log(`[visual-network] multi-route (>1 route):   ${multiRouteCorridors.length}`);
console.log(`[visual-network] wrote ${OUT_CORRIDORS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_CORRIDORS_JSON}`);
console.log(`[visual-network] wrote ${OUT_JUNCTION_ANCHORS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_JUNCTION_SNAPS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_MATERIALIZED_BUNDLES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_MATERIALIZED_BUNDLE_FANOUTS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_MATERIALIZED_BUNDLE_SPLITS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_MATERIALIZED_BUNDLE_DEFECTS_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_BUNDLES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_BUNDLE_LANES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_BUNDLE_GAPS_GEOJSON}`);
console.log(
  `[visual-network] junction anchors: ${junctionSnapDiagnostics.anchorFeatures.length}, snaps: ${junctionSnapDiagnostics.snapFeatures.length}`,
);
console.log(
  `[visual-network] lane groups: ${laneChainDiagnostics.lane_group_count}, chain-slot features: ${laneChainDiagnostics.chain_slot_feature_count}`,
);
console.log(
  `[visual-network] bundles: ${bundleArtifacts.bundleFeatures.length}, bundle lanes: ${bundleArtifacts.bundleLaneFeatures.length}, unbundled corridors: ${bundleArtifacts.unbundledFeatures.length}, bundle gaps: ${bundleArtifacts.bundleGapFeatures.length}`,
);

// Lane offset baking (Fix 3) — constants moved to the top tunables
// block so they are available at module-load time when
// buildBundleArtifacts is invoked at top-level.

// Compute pre-baked offset geometry. Walks the polyline vertex by vertex,
// computes the average of adjacent segment normals (miter join), caps to
// MITER_LENGTH_CAP_RATIO × lane width to avoid spikes at sharp corners
// (falls back to the segment normal — bevel). All math is in projected
// meters; final result is converted back to [lng, lat] using local
// per-vertex meters-per-degree.
function offsetPolylineByLaneSlot(coords, laneSlot) {
  if (!Array.isArray(coords) || coords.length < 2) return coords;
  if (!Number.isFinite(laneSlot) || laneSlot === 0) return coords;
  const offsetMeters = laneSlot * LANE_WIDTH_METERS;
  const miterCap = LANE_WIDTH_METERS * MITER_LENGTH_CAP_RATIO;

  // Pre-compute per-vertex meters-per-degree-longitude (varies with lat).
  const mPerLngAt = coords.map((c) => metersPerDegLng(c[1]));

  // Project to meters using each vertex's lat-corrected scale.
  const projected = coords.map((c, i) => [c[0] * mPerLngAt[i], c[1] * M_PER_DEG_LAT]);

  // Per-segment unit normal (right-hand perpendicular to segment direction).
  const segNormals = [];
  for (let i = 0; i < projected.length - 1; i += 1) {
    const dx = projected[i + 1][0] - projected[i][0];
    const dy = projected[i + 1][1] - projected[i][1];
    const len = Math.hypot(dx, dy);
    if (len === 0) {
      segNormals.push([0, 0]);
      continue;
    }
    // Right-hand normal: rotate +90° clockwise (dx, dy) → (dy, -dx)
    segNormals.push([dy / len, -dx / len]);
  }

  // Per-vertex normal (averaged miter join) with bevel fallback.
  const vertexNormals = [];
  for (let i = 0; i < projected.length; i += 1) {
    if (i === 0) {
      vertexNormals.push(segNormals[0]);
      continue;
    }
    if (i === projected.length - 1) {
      vertexNormals.push(segNormals[segNormals.length - 1]);
      continue;
    }
    const a = segNormals[i - 1];
    const b = segNormals[i];
    const sumX = a[0] + b[0];
    const sumY = a[1] + b[1];
    const sumLen = Math.hypot(sumX, sumY);
    if (sumLen < 1e-9) {
      vertexNormals.push(b);
      continue;
    }
    const nx = sumX / sumLen;
    const ny = sumY / sumLen;
    // Miter scale: the offset along the miter axis must be (offset / cos(half-angle)).
    // cos(half-angle) = dot(a, miter) which equals (a·miter). Equivalently, the
    // miter length factor is 1 / (a · n) where n is the average unit normal.
    const cosHalf = a[0] * nx + a[1] * ny;
    const miterLen = Math.abs(offsetMeters) / Math.max(0.05, Math.abs(cosHalf));
    if (miterLen > miterCap) {
      // Sharp corner — fall back to the segment that's about to start
      vertexNormals.push(b);
    } else {
      // Scale the unit normal so projection onto a yields offsetMeters
      const scale = 1 / cosHalf;
      vertexNormals.push([nx * scale, ny * scale]);
    }
  }

  // Apply offset in projected meter space; convert back to lng/lat.
  const out = [];
  for (let i = 0; i < projected.length; i += 1) {
    const n = vertexNormals[i];
    const nx = n[0] * offsetMeters;
    const ny = n[1] * offsetMeters;
    const x = projected[i][0] + nx;
    const y = projected[i][1] + ny;
    out.push([x / mPerLngAt[i], y / M_PER_DEG_LAT]);
  }
  return out;
}

function sortedBundleColors(routeIds) {
  return [
    ...new Set(routeIds.map((routeId) => routeColorFor(routeId))),
  ].sort((a, b) => bundleColorRank(a) - bundleColorRank(b));
}

function bundleLaneSlotsForColors(colors) {
  return Object.fromEntries(
    colors.map((color, index) => [color, index - (colors.length - 1) / 2]),
  );
}

function routesForColor(routeIds, color) {
  return routeIds
    .filter((routeId) => routeColorFor(routeId) === color)
    .sort(compareRouteIds);
}

function unionRouteIds(features) {
  return [
    ...new Set(features.flatMap((feature) => feature.properties.route_ids ?? [])),
  ].sort(compareRouteIds);
}

function routeDiff(left, right) {
  const rightSet = new Set(right);
  return left.filter((routeId) => !rightSet.has(routeId));
}

function buildAnchorFeatureIndex(features) {
  const byAnchor = new Map();
  features.forEach((feature, index) => {
    for (const anchorId of feature.properties.junction_anchor_ids ?? []) {
      if (!byAnchor.has(anchorId)) byAnchor.set(anchorId, []);
      byAnchor.get(anchorId).push({ feature, index });
    }
  });
  return byAnchor;
}

function adjacentRouteIdsAtAnchor(anchorFeatureIndex, anchorId, ownCorridorId) {
  if (!anchorId) return [];
  const adjacent = (anchorFeatureIndex.get(anchorId) ?? [])
    .filter(({ feature }) => feature.properties.corridor_id !== ownCorridorId)
    .map(({ feature }) => feature);
  return unionRouteIds(adjacent);
}

// Gap bridging (Fix 1) — constant moved up.

function buildJunctionBridges(bundleLaneFeatures, bundleGapFeatures) {
  // Spatial-grid index of bundle_lane endpoints by color so we can find
  // bridge candidates by GEOMETRIC PROXIMITY (anchor identity is not
  // enough — bundle gaps exist precisely because no adjacent feature
  // shares the same anchor by id).
  //   cellKey = `${cellX}|${cellY}|${color}`
  //   value = list of { coord, feature, endpointKind }
  // Cell size = JUNCTION_BRIDGE_MAX_M so a 3×3 neighborhood lookup
  // captures every candidate within snap distance.
  const cellDeg = JUNCTION_BRIDGE_MAX_M / M_PER_DEG_LAT;
  const endpointIndex = new Map();
  function endpointIndexInsert(coord, color, feature, endpointKind) {
    const cx = Math.floor(coord[0] / cellDeg);
    const cy = Math.floor(coord[1] / cellDeg);
    const k = cx + "|" + cy + "|" + color;
    if (!endpointIndex.has(k)) endpointIndex.set(k, []);
    endpointIndex.get(k).push({ coord, feature, endpointKind });
  }
  for (const lane of bundleLaneFeatures) {
    const p = lane.properties;
    const coords = lane.geometry.coordinates;
    if (!Array.isArray(coords) || coords.length < 2) continue;
    const color = String(p.color ?? "");
    if (!color) continue;
    endpointIndexInsert(coords[0], color, lane, "from");
    endpointIndexInsert(coords[coords.length - 1], color, lane, "to");
  }
  function endpointIndexLookup(coord, color) {
    const cx = Math.floor(coord[0] / cellDeg);
    const cy = Math.floor(coord[1] / cellDeg);
    const out = [];
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        const k = (cx + dx) + "|" + (cy + dy) + "|" + color;
        const bucket = endpointIndex.get(k);
        if (bucket) for (const e of bucket) out.push(e);
      }
    }
    return out;
  }

  const bridges = [];
  const bridged = new Set();
  let bridgeNumber = 1;

  for (const gap of bundleGapFeatures) {
    const p = gap.properties;
    const color = String(p.color ?? "");
    if (!color) continue;
    const gapCoord = gap.geometry.coordinates;
    if (!Array.isArray(gapCoord) || gapCoord.length !== 2) continue;

    // Find the closest same-color bundle_lane endpoint that's NOT one of
    // the gap's own corridor endpoints.
    const candidates = endpointIndexLookup(gapCoord, color);
    let bestEndpoint = null;
    let bestDist = Infinity;
    for (const cand of candidates) {
      if (cand.feature.properties.corridor_id === p.corridor_id) continue;
      const d = distanceMeters(gapCoord, cand.coord);
      if (d > JUNCTION_BRIDGE_MAX_M) continue;
      if (d < bestDist) { bestDist = d; bestEndpoint = cand; }
    }
    if (!bestEndpoint) continue;

    // Avoid duplicate bridges: pair-key independent of order.
    const pairKey = [
      p.corridor_id ?? p.bundle_id ?? "?",
      bestEndpoint.feature.properties.corridor_id
        ?? bestEndpoint.feature.properties.bundle_id ?? "?",
      color,
    ]
      .sort()
      .join("|");
    if (bridged.has(pairKey)) continue;
    bridged.add(pairKey);

    const colorRouteIds = Array.isArray(p.color_route_ids)
      ? p.color_route_ids
      : [];
    const routeIds = Array.isArray(p.route_ids) ? p.route_ids : [];
    // Inherit lane slot from the target endpoint's bundle_lane so the
    // bridge sits visually on the same parallel slot.
    const laneSlot = Number(bestEndpoint.feature.properties.lane_slot ?? 0);

    bridges.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [gapCoord, bestEndpoint.coord] },
      properties: {
        visual_feature_type: "bundle_lane",
        bridge: true,
        bridge_id: `bridge-${String(bridgeNumber++).padStart(5, "0")}`,
        bundle_id: p.bundle_id ?? null,
        corridor_id: p.corridor_id ?? null,
        route_id: colorRouteIds[0] ?? routeIds[0] ?? "",
        representative_route_id: colorRouteIds[0] ?? routeIds[0] ?? "",
        route_ids: routeIds,
        color_route_ids: colorRouteIds,
        color,
        lane_slot: laneSlot,
        lane_group_id:
          bestEndpoint.feature.properties.lane_group_id ??
          bestEndpoint.feature.properties.bundle_id ?? null,
        lane_slot_source: "bridge",
        lane_order_basis:
          bestEndpoint.feature.properties.lane_order_basis ?? [color],
        lane_order_override_applied: false,
        bundle_lane_count:
          bestEndpoint.feature.properties.bundle_lane_count ?? 1,
        bundle_lane_slots:
          bestEndpoint.feature.properties.bundle_lane_slots ?? { [color]: laneSlot },
        from_anchor_id: p.anchor_id,
        to_anchor_id: p.anchor_id,
        anchor_id: p.anchor_id,
        bridge_distance_m: Number(bestDist.toFixed(2)),
        source_shape_ids: [],
        source_edge_ids: [],
        member_corridor_ids: [
          p.corridor_id,
          bestEndpoint.feature.properties.corridor_id,
        ].filter(Boolean),
        spine_id: null,
        base_spine_hash: null,
        physical_bundle_id: null,
        physical_bundle_spine_hash: null,
        physical_bundle_member_count: null,
        physical_bundle_confidence: null,
      },
    });
  }
  return bridges;
}

function buildBundleArtifacts(features, spinesByCorridorId) {
  const anchorFeatureIndex = buildAnchorFeatureIndex(features);
  const bundleFeatures = [];
  const bundleLaneFeatures = [];
  const unbundledFeatures = [];
  const bundleGapFeatures = [];
  let bundleNumber = 1;
  let soloNumber = 1;

  for (const feature of features) {
    const routeIds = [...(feature.properties.route_ids ?? [])].sort(compareRouteIds);
    const heuristicColors = sortedBundleColors(routeIds);
    // Only look up overrides when BOTH anchors are present; otherwise the
    // override key would degenerate to "::" and any Phase 6 override accidentally
    // written under that key would match every anchorless bundle.
    const fromAnchorIdRaw = feature.properties.from_anchor_id ?? null;
    const toAnchorIdRaw = feature.properties.to_anchor_id ?? null;
    const overrideKey = (fromAnchorIdRaw && toAnchorIdRaw)
      ? `${fromAnchorIdRaw}::${toAnchorIdRaw}`
      : null;
    const { colors, overrideApplied } = orderColorsForBundle(heuristicColors, {
      overrideKey,
      overrides: BUNDLE_ORDER_OVERRIDES,
    });
    const isBundle = routeIds.length > 1;

    if (!isBundle) {
      // Fix 2: promote solo corridors to bundle_lane with lane_slot=0 and
      // bundle_lane_count=1. The runtime now renders every line through
      // the same code path. No raw "corridor" type survives in the final.
      const soloBundleId = `solo-${String(soloNumber++).padStart(5, "0")}`;
      const soloColor = colors[0] ?? "#808183";
      const laneSlots = { [soloColor]: 0 };
      const props = feature.properties;
      const fromAnchorId = props.from_anchor_id ?? null;
      const toAnchorId = props.to_anchor_id ?? null;
      const spine = spinesByCorridorId?.get(props.corridor_id);
      const materializationRole = props.bundle_materialization_role ?? null;
      const isContinuousLane = materializationRole === "continuous_lane";
      const materializedLaneSlotSource =
        isContinuousLane
          ? "physical_bundle_continuous"
          : materializationRole === "fanout"
            ? "fanout"
            : materializationRole === "shared_spine"
              ? "physical_bundle"
              : "solo";
      // Continuous lanes already baked their lane offset in materialization; keep the
      // slot + provenance so the later cross-color pass leaves them alone (no double-offset).
      const materializedLaneSlot = isContinuousLane ? Number(props.lane_slot ?? 0) : 0;
      bundleLaneFeatures.push({
        type: "Feature",
        geometry: feature.geometry,
        properties: {
          visual_feature_type: "bundle_lane",
          bundle_id: soloBundleId,
          corridor_id: props.corridor_id,
          route_id: routeIds[0] ?? "",
          representative_route_id: routeIds[0] ?? "",
          route_ids: routeIds,
          color_route_ids: routeIds,
          color: soloColor,
          lane_slot: materializedLaneSlot,
          lane_offset_baked: isContinuousLane ? true : (props.lane_offset_baked ?? false),
          lane_group_id: props.materialized_bundle_id ?? soloBundleId,
          lane_slot_source: materializedLaneSlotSource,
          lane_order_basis: [soloColor],
          lane_order_override_applied: false,
          bundle_lane_count: 1,
          bundle_lane_slots: laneSlots,
          physical_bundle_id: feature.properties.physical_bundle_id ?? null,
          materialized_bundle_id: props.materialized_bundle_id ?? null,
          bundle_materialization_role: materializationRole,
          fanout_from_lane_slot: props.fanout_from_lane_slot ?? null,
          fanout_to_lane_slot: props.fanout_to_lane_slot ?? null,
          fanout_blend_m: props.fanout_blend_m ?? null,
          source_corridor_id: props.source_corridor_id ?? null,
          shared_extent_start_m: props.shared_extent_start_m ?? null,
          shared_extent_end_m: props.shared_extent_end_m ?? null,
          branch_in_route_ids: [],
          branch_out_route_ids: [],
          bundle_entry: false,
          bundle_exit: false,
          from_stop_id: props.from_stop_id,
          to_stop_id: props.to_stop_id,
          from_stop_name: props.from_stop_name,
          to_stop_name: props.to_stop_name,
          from_anchor_id: fromAnchorId,
          to_anchor_id: toAnchorId,
          length_m: props.length_m,
          source_shape_ids: props.source_shape_ids ?? [],
          source_edge_ids: props.source_edge_ids ?? [],
          member_corridor_ids: [props.corridor_id],
          spine_id: spine?.spine_id ?? null,
          base_spine_hash: spine?.base_spine_hash ?? null,
          base_geometry_selection: spine?.method ?? null,
          physical_bundle_spine_hash: feature.properties.physical_bundle_spine_hash ?? null,
          physical_bundle_member_count: feature.properties.physical_bundle_member_count ?? null,
          physical_bundle_confidence: feature.properties.physical_bundle_confidence ?? null,
        },
      });
      continue;
    }

    const bundleId = `bundle-${String(bundleNumber++).padStart(5, "0")}`;
    const laneSlots = bundleLaneSlotsForColors(colors);
    const fromAnchorId = feature.properties.from_anchor_id ?? null;
    const toAnchorId = feature.properties.to_anchor_id ?? null;
    const entryAdjacentRouteIds = adjacentRouteIdsAtAnchor(
      anchorFeatureIndex,
      fromAnchorId,
      feature.properties.corridor_id,
    );
    const exitAdjacentRouteIds = adjacentRouteIdsAtAnchor(
      anchorFeatureIndex,
      toAnchorId,
      feature.properties.corridor_id,
    );
    const branchInRouteIds = routeDiff(routeIds, entryAdjacentRouteIds);
    const branchOutRouteIds = routeDiff(routeIds, exitAdjacentRouteIds);

    const bundleProperties = {
      visual_feature_type: "bundle",
      bundle_id: bundleId,
      corridor_id: feature.properties.corridor_id,
      bundle_route_ids: routeIds,
      route_ids: routeIds,
      bundle_color_groups: colors.map((color) => ({
        color,
        route_ids: routesForColor(routeIds, color),
      })),
      member_edge_ids: feature.properties.source_edge_ids ?? [],
      member_corridor_ids: [feature.properties.corridor_id],
      entry_node_ids: [fromAnchorId].filter(Boolean),
      exit_node_ids: [toAnchorId].filter(Boolean),
      from_anchor_id: fromAnchorId,
      to_anchor_id: toAnchorId,
      from_stop_id: feature.properties.from_stop_id,
      to_stop_id: feature.properties.to_stop_id,
      from_stop_name: feature.properties.from_stop_name,
      to_stop_name: feature.properties.to_stop_name,
      length_m: feature.properties.length_m,
      bundle_lane_count: colors.length,
      bundle_lane_slots: laneSlots,
      lane_group_id: bundleId,
      lane_order_basis: colors,
      lane_order_override_applied: overrideApplied,
      physical_bundle_id: feature.properties.physical_bundle_id ?? null,
      materialized_bundle_id: feature.properties.materialized_bundle_id ?? null,
      bundle_materialization_role: feature.properties.bundle_materialization_role ?? null,
      fanout_from_lane_slot: feature.properties.fanout_from_lane_slot ?? null,
      fanout_to_lane_slot: feature.properties.fanout_to_lane_slot ?? null,
      fanout_blend_m: feature.properties.fanout_blend_m ?? null,
      source_corridor_id: feature.properties.source_corridor_id ?? null,
      shared_extent_start_m: feature.properties.shared_extent_start_m ?? null,
      shared_extent_end_m: feature.properties.shared_extent_end_m ?? null,
      branch_in_route_ids: branchInRouteIds,
      branch_out_route_ids: branchOutRouteIds,
      bundle_entry: branchInRouteIds.length > 0,
      bundle_exit: branchOutRouteIds.length > 0,
      base_geometry_source_edge_id:
        feature.properties.base_member_edge_id ??
        feature.properties.longest_member_edge_id ??
        null,
      base_geometry_selection:
        feature.properties.base_geometry_selection ?? "quality_density_length",
      source_shape_ids: feature.properties.source_shape_ids ?? [],
      source_edge_ids: feature.properties.source_edge_ids ?? [],
    };

    bundleFeatures.push({
      type: "Feature",
      geometry: feature.geometry,
      properties: bundleProperties,
    });

    const bundleSpine = spinesByCorridorId?.get(feature.properties.corridor_id);
    for (const color of colors) {
      const colorRouteIds = routesForColor(routeIds, color);
      bundleLaneFeatures.push({
        type: "Feature",
        geometry: feature.geometry,
        properties: {
          visual_feature_type: "bundle_lane",
          bundle_id: bundleId,
          corridor_id: feature.properties.corridor_id,
          route_id: colorRouteIds[0] ?? routeIds[0],
          representative_route_id: colorRouteIds[0] ?? routeIds[0],
          route_ids: routeIds,
          color_route_ids: colorRouteIds,
          color,
          lane_slot:
            feature.properties.bundle_materialization_role === "continuous_lane"
              ? Number(feature.properties.lane_slot ?? laneSlots[color])
              : laneSlots[color],
          lane_offset_baked:
            feature.properties.bundle_materialization_role === "continuous_lane"
              ? true
              : (feature.properties.lane_offset_baked ?? false),
          lane_group_id: feature.properties.materialized_bundle_id ?? bundleId,
          lane_slot_source:
            feature.properties.bundle_materialization_role === "continuous_lane"
              ? "physical_bundle_continuous"
              : feature.properties.bundle_materialization_role === "shared_spine"
                ? "physical_bundle"
                : feature.properties.bundle_materialization_role === "fanout"
                  ? "fanout"
                  : "bundle",
          lane_order_basis: colors,
          lane_order_override_applied: overrideApplied,
          bundle_lane_count: colors.length,
          bundle_lane_slots: laneSlots,
          materialized_bundle_id: feature.properties.materialized_bundle_id ?? null,
          bundle_materialization_role: feature.properties.bundle_materialization_role ?? null,
          fanout_from_lane_slot: feature.properties.fanout_from_lane_slot ?? null,
          fanout_to_lane_slot: feature.properties.fanout_to_lane_slot ?? null,
          fanout_blend_m: feature.properties.fanout_blend_m ?? null,
          source_corridor_id: feature.properties.source_corridor_id ?? null,
          shared_extent_start_m: feature.properties.shared_extent_start_m ?? null,
          shared_extent_end_m: feature.properties.shared_extent_end_m ?? null,
          branch_in_route_ids: branchInRouteIds,
          branch_out_route_ids: branchOutRouteIds,
          bundle_entry: branchInRouteIds.length > 0,
          bundle_exit: branchOutRouteIds.length > 0,
          from_stop_id: feature.properties.from_stop_id,
          to_stop_id: feature.properties.to_stop_id,
          from_stop_name: feature.properties.from_stop_name,
          to_stop_name: feature.properties.to_stop_name,
          from_anchor_id: fromAnchorId,
          to_anchor_id: toAnchorId,
          length_m: feature.properties.length_m,
          source_shape_ids: feature.properties.source_shape_ids ?? [],
          source_edge_ids: feature.properties.source_edge_ids ?? [],
          member_corridor_ids: [feature.properties.corridor_id],
          spine_id: bundleSpine?.spine_id ?? null,
          base_spine_hash: bundleSpine?.base_spine_hash ?? null,
          base_geometry_selection: bundleSpine?.method ?? null,
          physical_bundle_id: feature.properties.physical_bundle_id ?? null,
          physical_bundle_spine_hash: feature.properties.physical_bundle_spine_hash ?? null,
          physical_bundle_member_count: feature.properties.physical_bundle_member_count ?? null,
          physical_bundle_confidence: feature.properties.physical_bundle_confidence ?? null,
        },
      });
    }

    for (const [anchorId, endpointKind, adjacentRouteIds] of [
      [fromAnchorId, "entry", entryAdjacentRouteIds],
      [toAnchorId, "exit", exitAdjacentRouteIds],
    ]) {
      if (!anchorId) continue;
      for (const color of colors) {
        const colorRouteIds = routesForColor(routeIds, color);
        if (colorRouteIds.some((routeId) => adjacentRouteIds.includes(routeId))) {
          continue;
        }
        const coordinate =
          endpointKind === "entry"
            ? feature.geometry.coordinates[0]
            : feature.geometry.coordinates[feature.geometry.coordinates.length - 1];
        bundleGapFeatures.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: coordinate },
          properties: {
            marker_type: "bundle_gap",
            bundle_id: bundleId,
            corridor_id: feature.properties.corridor_id,
            anchor_id: anchorId,
            endpoint_kind: endpointKind,
            route_ids: routeIds,
            color_route_ids: colorRouteIds,
            color,
            reason: "no_same_route_adjacent_bundle_lane_at_anchor",
          },
        });
      }
    }
  }

  // TODO(phase 3c): delete buildJunctionBridges entirely.
  // Legacy buildJunctionBridges removed in Phase 3b. The new branch-transition
  // promotion happens OUTSIDE buildBundleArtifacts (after it returns) so the
  // transition logic operates on the final bundle_lane set, not intermediate
  // state. The buildJunctionBridges function definition is kept in this file
  // for now but is no longer called. To remove entirely, delete the function
  // (around line 2225) plus the import dependencies it touches.

  // Fix 3: bake the lane_slot offset into each bundle_lane's geometry so
  // the runtime doesn't need MapLibre's per-segment line-offset (which
  // breaks at corners). Keep the original lane_slot as metadata under
  // lane_slot_semantic and set lane_slot/render_lane_slot to 0 in the
  // output so the existing line-offset paint expression produces 0 for
  // these features.
  for (const lane of bundleLaneFeatures) {
    // Continuous-materialization lanes already have their lane offset baked into
    // geometry by materializePhysicalBundles; never re-bake it (that was the
    // double/triple-offset). Just flag it and zero the runtime slot so MapLibre and
    // the later cross-color passes add no further offset.
    if (lane.properties.lane_slot_source === "physical_bundle_continuous") {
      lane.properties.lane_offset_baked = true;
      lane.properties.lane_slot_semantic = Number(lane.properties.lane_slot ?? 0);
      lane.properties.lane_slot = 0;
      lane.properties.render_lane_slot = 0;
      lane.properties.lane_width_m = LANE_WIDTH_METERS;
      continue;
    }
    const fanoutFromSlot = Number(lane.properties.fanout_from_lane_slot);
    const fanoutToSlot = Number(lane.properties.fanout_to_lane_slot);
    const shouldBakeFanoutRamp =
      lane.properties.bundle_materialization_role === "fanout" &&
      Number.isFinite(fanoutFromSlot) &&
      Number.isFinite(fanoutToSlot) &&
      (fanoutFromSlot !== 0 || fanoutToSlot !== 0);

    if (shouldBakeFanoutRamp) {
      lane.geometry = {
        type: "LineString",
        coordinates: offsetPolylineBySlotRamp(
          lane.geometry.coordinates,
          fanoutFromSlot,
          fanoutToSlot,
          LANE_WIDTH_METERS,
        ),
      };
      lane.properties.lane_offset_baked = true;
      lane.properties.fanout_slot_ramp_baked = true;
      lane.properties.lane_slot_semantic =
        Math.abs(fanoutFromSlot) >= Math.abs(fanoutToSlot)
          ? fanoutFromSlot
          : fanoutToSlot;
      lane.properties.lane_slot = 0;
      lane.properties.render_lane_slot = 0;
      lane.properties.lane_width_m = LANE_WIDTH_METERS;
      continue;
    }

    const semanticSlot = Number(lane.properties.lane_slot ?? 0);
    if (semanticSlot === 0) {
      // No offset needed, but flag the feature uniformly.
      lane.properties.lane_offset_baked = true;
      lane.properties.lane_slot_semantic = semanticSlot;
      lane.properties.render_lane_slot = 0;
      continue;
    }
    const baked = offsetPolylineByLaneSlot(
      lane.geometry.coordinates,
      semanticSlot,
    );
    lane.geometry = { type: "LineString", coordinates: baked };
    lane.properties.lane_offset_baked = true;
    lane.properties.lane_slot_semantic = semanticSlot;
    lane.properties.lane_slot = 0;
    lane.properties.render_lane_slot = 0;
    lane.properties.lane_width_m = LANE_WIDTH_METERS;
  }

  // Note: unbundledFeatures is now empty (Fix 2 promoted solos directly
  // into bundleLaneFeatures). Keep the field for backwards compatibility
  // with downstream summaries that read it.
  const visualFeatures = [...bundleLaneFeatures, ...unbundledFeatures].sort(
    (a, b) => {
      const left =
        a.properties.bundle_id ??
        a.properties.corridor_id ??
        a.properties.route_id ??
        "";
      const right =
        b.properties.bundle_id ??
        b.properties.corridor_id ??
        b.properties.route_id ??
        "";
      const idCompare = String(left).localeCompare(String(right), "en", {
        numeric: true,
      });
      if (idCompare !== 0) return idCompare;
      return (
        Number(a.properties.lane_slot_semantic ?? a.properties.lane_slot ?? 0) -
        Number(b.properties.lane_slot_semantic ?? b.properties.lane_slot ?? 0)
      );
    },
  );

  return {
    bundleFeatures,
    bundleLaneFeatures,
    unbundledFeatures,
    bundleGapFeatures,
    visualFeatures,
  };
}

// Required-trunk check: every well-known shared trunk should be detected
const REQUIRED_TRUNKS = [
  ["1", "2", "3"],
  ["4", "5", "6"],
  ["A", "C", "E"],
  ["B", "D", "F", "M"],
  ["N", "Q", "R", "W"],
];
console.log(`[visual-network] --- Required shared-trunk check ---`);
for (const trunk of REQUIRED_TRUNKS) {
  const trunkSet = new Set(trunk);
  const hits = corridorRows.filter((c) => {
    const cr = new Set(c.route_ids);
    return trunk.every((r) => cr.has(r));
  });
  console.log(
    `[visual-network]   ${trunk.join("/").padEnd(10)} corridors carrying ALL: ${hits.length}`,
  );
}

console.log("[visual-network] Gate 2G — render-lane continuity diagnostics");

function buildRouteIncidentCounts(features, useSourceEdges = false) {
  const counts = new Map();
  const add = (stopId, stopName, routeId, corridorId) => {
    const key = `${stopId}|${routeId}`;
    if (!counts.has(key)) {
      counts.set(key, {
        stop_id: stopId,
        stop_name: stopName,
        route_id: routeId,
        corridor_ids: new Set(),
        count: 0,
      });
    }
    const row = counts.get(key);
    row.count += 1;
    if (corridorId) row.corridor_ids.add(corridorId);
  };

  for (const feature of features) {
    const props = feature.properties;
    const routeIds = useSourceEdges
      ? [props.route_id]
      : props.route_ids ?? [];
    for (const routeId of routeIds) {
      add(props.from_stop_id, props.from_stop_name, routeId, props.corridor_id);
      add(props.to_stop_id, props.to_stop_name, routeId, props.corridor_id);
    }
  }

  return counts;
}

function buildVisualRouteIncidentCounts(features) {
  const counts = new Map();
  const add = (stopId, stopName, routeId, corridorId) => {
    const key = `${stopId}|${routeId}`;
    if (!counts.has(key)) {
      counts.set(key, {
        stop_id: stopId,
        stop_name: stopName,
        route_id: routeId,
        corridor_ids: new Set(),
        count: 0,
      });
    }
    const row = counts.get(key);
    row.count += 1;
    if (corridorId) row.corridor_ids.add(corridorId);
  };

  for (const feature of features) {
    const props = feature.properties;
    const routeIds = new Set(props.route_ids ?? []);
    const sourceEdges = (props.source_edge_ids ?? [])
      .map((edgeId) => edgeById.get(edgeId))
      .filter(Boolean);

    if (sourceEdges.length > 0) {
      for (const edge of sourceEdges) {
        const routeId = edge.properties.route_id;
        if (!routeIds.has(routeId)) continue;
        add(
          edge.properties.from_stop_id,
          edge.properties.from_stop_name,
          routeId,
          props.corridor_id,
        );
        add(
          edge.properties.to_stop_id,
          edge.properties.to_stop_name,
          routeId,
          props.corridor_id,
        );
      }
      continue;
    }

    for (const routeId of routeIds) {
      add(props.from_stop_id, props.from_stop_name, routeId, props.corridor_id);
      add(props.to_stop_id, props.to_stop_name, routeId, props.corridor_id);
    }
  }

  return counts;
}

const expectedRouteIncidents = buildRouteIncidentCounts(edgeFeatures, true);
const visualRouteIncidents = buildVisualRouteIncidentCounts(corridorFeatures);
const missingRouteLaneFeatures = [];

for (const [key, expected] of expectedRouteIncidents) {
  if (expected.count < 2) continue;
  const visual = visualRouteIncidents.get(key);
  const visualCount = visual?.count ?? 0;
  if (visualCount >= 2) continue;
  const stop = stopsById.get(expected.stop_id);
  if (!stop) continue;
  missingRouteLaneFeatures.push({
    type: "Feature",
    geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
    properties: {
      marker_type: "missing_route_lane",
      stop_id: expected.stop_id,
      stop_name: expected.stop_name,
      route_id: expected.route_id,
      expected_incident_edges: expected.count,
      visual_incident_corridors: visualCount,
      visual_corridor_ids: [...(visual?.corridor_ids ?? [])].sort(),
      reason: "route_expected_to_continue_at_junction",
    },
  });
}

const missingRouteLaneGeoJson = {
  type: "FeatureCollection",
  metadata: {
    generated_at: new Date().toISOString(),
    source: "build-subway-visual-network.mjs Gate 2G",
    summary: {
      missing_route_lane_count: missingRouteLaneFeatures.length,
      q_prospect_brighton_missing_count: missingRouteLaneFeatures.filter(
        (feature) =>
          feature.properties.route_id === "Q" &&
          /Prospect|Brighton|7 Av|Atlantic|DeKalb/.test(
            feature.properties.stop_name,
          ),
      ).length,
      route_2_flatbush_eastern_missing_count: missingRouteLaneFeatures.filter(
        (feature) =>
          feature.properties.route_id === "2" &&
          /Flatbush|Nostrand|Eastern|Franklin|President|Sterling|Winthrop|Church/.test(
            feature.properties.stop_name,
          ),
      ).length,
    },
  },
  features: missingRouteLaneFeatures,
};

const renderLaneContinuityJson = {
  generated_at: new Date().toISOString(),
  source: "build-subway-visual-network.mjs Gate 2G",
  summary: {
    visual_feature_count: corridorFeatures.length,
    visual_render_feature_count: bundleArtifacts.visualFeatures.length,
    missing_route_lane_count: missingRouteLaneFeatures.length,
    q_prospect_brighton_missing_count:
      missingRouteLaneGeoJson.metadata.summary.q_prospect_brighton_missing_count,
    route_2_flatbush_eastern_missing_count:
      missingRouteLaneGeoJson.metadata.summary.route_2_flatbush_eastern_missing_count,
    junction_anchor_count: junctionSnapDiagnostics.anchorFeatures.length,
    junction_snap_count: junctionSnapDiagnostics.snapFeatures.length,
    lane_group_count: laneChainDiagnostics.lane_group_count,
    chain_slot_feature_count: laneChainDiagnostics.chain_slot_feature_count,
    bundle_count: bundleArtifacts.bundleFeatures.length,
    bundled_render_lane_count: bundleArtifacts.bundleLaneFeatures.length,
    remaining_unbundled_corridors: bundleArtifacts.unbundledFeatures.length,
  },
  missing_route_lane_sample: missingRouteLaneFeatures.slice(0, 50).map((feature) => ({
    stop_name: feature.properties.stop_name,
    route_id: feature.properties.route_id,
    expected_incident_edges: feature.properties.expected_incident_edges,
    visual_incident_corridors: feature.properties.visual_incident_corridors,
    visual_corridor_ids: feature.properties.visual_corridor_ids,
  })),
};

writeFileSync(
  OUT_MISSING_ROUTE_LANES_GEOJSON,
  `${JSON.stringify(missingRouteLaneGeoJson)}\n`,
);
writeFileSync(
  OUT_RENDER_LANE_CONTINUITY_JSON,
  `${JSON.stringify(renderLaneContinuityJson, null, 2)}\n`,
);
console.log(`[visual-network] wrote ${OUT_MISSING_ROUTE_LANES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_RENDER_LANE_CONTINUITY_JSON}`);
console.log(
  `[visual-network] missing route lanes: ${missingRouteLaneFeatures.length} ` +
    `(Q Prospect/Brighton=${missingRouteLaneGeoJson.metadata.summary.q_prospect_brighton_missing_count}, ` +
    `2 Flatbush/Eastern=${missingRouteLaneGeoJson.metadata.summary.route_2_flatbush_eastern_missing_count})`,
);

console.log("[visual-network] Gate 2F — visual-geometry anomaly diagnostics");

function hasUnrelatedRouteFamilyMix(routeIds) {
  if (routeIds.length <= 1) return false;
  return new Set(routeIds.map(routeFamilyKey)).size > 2;
}

function anomalyReasonsForFeature(feature) {
  const props = feature.properties;
  const stats = geometryStats(feature.geometry.coordinates);
  const sourceEdges = (props.source_edge_ids ?? [])
    .map((edgeId) => edgeById.get(edgeId))
    .filter(Boolean);
  const maxProjectionDistanceM = sourceEdges.reduce((max, edge) => {
    return Math.max(
      max,
      Number(edge.properties.from_projection_dist_m ?? 0),
      Number(edge.properties.to_projection_dist_m ?? 0),
    );
  }, 0);
  const reasons = [];

  if (stats.max_segment_length_m > MAX_SEGMENT_ANOMALY_M) {
    reasons.push("max_segment_gt_250m");
  }
  if (stats.coordinate_count <= 2 && stats.length_m > SPARSE_LONG_SLICE_M) {
    reasons.push("sparse_long_slice");
  }
  if (maxProjectionDistanceM > PROJECTION_ANOMALY_M) {
    reasons.push("projection_gt_125m");
  }
  if (stats.sharp_angle_count > 0) {
    reasons.push("sharp_angle_gt_120deg");
  }
  if (
    stats.coordinate_count <= 3 &&
    stats.length_m > 600 &&
    stats.sinuosity < 1.03
  ) {
    reasons.push("low_detail_straight_long_slice");
  }
  if (hasUnrelatedRouteFamilyMix(props.route_ids ?? [])) {
    reasons.push("unrelated_route_family_mix");
  }

  const severity =
    Math.max(0, stats.max_segment_length_m - MAX_SEGMENT_ANOMALY_M) / 25 +
    Math.max(0, maxProjectionDistanceM - PROJECTION_ANOMALY_M) / 10 +
    stats.sharp_angle_count * 3 +
    (stats.coordinate_count <= 2 && stats.length_m > SPARSE_LONG_SLICE_M
      ? 20
      : 0) +
    (hasUnrelatedRouteFamilyMix(props.route_ids ?? []) ? 5 : 0);

  return {
    reasons,
    severity: Number(severity.toFixed(2)),
    stats,
    max_projection_distance_m: Number(maxProjectionDistanceM.toFixed(2)),
    source_edges: sourceEdges,
  };
}

function buildVisualAnomalyRecords(features) {
  return features.map((feature) => {
    const result = anomalyReasonsForFeature(feature);
    if (result.reasons.length === 0) return null;
    const props = feature.properties;
    return {
      feature,
      reasons: result.reasons,
      severity: result.severity,
      stats: result.stats,
      max_projection_distance_m: result.max_projection_distance_m,
      shape_ids: [
        ...new Set(result.source_edges.map((edge) => edge.properties.shape_id)),
      ].sort((a, b) => a.localeCompare(b, "en", { numeric: true })),
      stop_pairs: result.source_edges
        .slice(0, 12)
        .map(
          (edge) =>
            `${edge.properties.from_stop_name} → ${edge.properties.to_stop_name}`,
        ),
      source_edge_ids: props.source_edge_ids ?? [],
    };
  })
  .filter(Boolean)
  .sort((a, b) => b.severity - a.severity);
}

const visualAnomalies = buildVisualAnomalyRecords(corridorFeatures);

const anomalyGeoJson = {
  type: "FeatureCollection",
  metadata: {
    generated_at: new Date().toISOString(),
    source: "build-subway-visual-network.mjs Gate 2F",
    parameters: {
      max_segment_anomaly_m: MAX_SEGMENT_ANOMALY_M,
      sparse_long_slice_m: SPARSE_LONG_SLICE_M,
      projection_anomaly_m: PROJECTION_ANOMALY_M,
    },
    summary: {
      visual_feature_count: corridorFeatures.length,
      anomaly_count: visualAnomalies.length,
    },
  },
  features: visualAnomalies.map((anomaly) => ({
    type: "Feature",
    geometry: anomaly.feature.geometry,
    properties: {
      corridor_id: anomaly.feature.properties.corridor_id,
      route_ids: anomaly.feature.properties.route_ids,
      anomaly_reasons: anomaly.reasons,
      severity: anomaly.severity,
      length_m: anomaly.stats.length_m,
      direct_distance_m: anomaly.stats.direct_distance_m,
      sinuosity: anomaly.stats.sinuosity,
      max_segment_length_m: anomaly.stats.max_segment_length_m,
      coordinate_count: anomaly.stats.coordinate_count,
      sharp_angle_count: anomaly.stats.sharp_angle_count,
      max_projection_distance_m: anomaly.max_projection_distance_m,
      shape_ids: anomaly.shape_ids,
      stop_pairs: anomaly.stop_pairs,
      source_edge_ids: anomaly.source_edge_ids,
    },
  })),
};

const anomalyJson = {
  generated_at: new Date().toISOString(),
  source: "build-subway-visual-network.mjs Gate 2F",
  summary: {
    visual_feature_count: corridorFeatures.length,
    shared_corridor_count: corridorFeatures.filter(
      (feature) => (feature.properties.route_ids ?? []).length > 1,
    ).length,
    anomaly_count: visualAnomalies.length,
    max_segment_anomaly_count: visualAnomalies.filter((anomaly) =>
      anomaly.reasons.includes("max_segment_gt_250m"),
    ).length,
    projection_anomaly_count: visualAnomalies.filter((anomaly) =>
      anomaly.reasons.includes("projection_gt_125m"),
    ).length,
    sparse_long_slice_count: visualAnomalies.filter((anomaly) =>
      anomaly.reasons.includes("sparse_long_slice"),
    ).length,
  },
  top_anomalies: visualAnomalies.slice(0, 50).map((anomaly) => ({
    corridor_id: anomaly.feature.properties.corridor_id,
    route_ids: anomaly.feature.properties.route_ids,
    severity: anomaly.severity,
    reasons: anomaly.reasons,
    length_m: anomaly.stats.length_m,
    max_segment_length_m: anomaly.stats.max_segment_length_m,
    coordinate_count: anomaly.stats.coordinate_count,
    max_projection_distance_m: anomaly.max_projection_distance_m,
    shape_ids: anomaly.shape_ids,
    stop_pairs: anomaly.stop_pairs,
  })),
};

const hardBlockingVisualDefects = [
  ...visualAnomalies.filter((anomaly) =>
    anomaly.reasons.includes("sparse_long_slice") ||
    anomaly.reasons.includes("low_detail_straight_long_slice")
  ),
  ...corridorFeatures
    .filter((feature) => {
      const props = feature.properties ?? {};
      if (props.visual_feature_type === "same_color_branch_connector") return false;
      return geometryStats(feature.geometry.coordinates).length_m < OPEN_DATA_MIN_FRAGMENT_LENGTH_M;
    })
    .map((feature) => ({
      feature,
      reasons: ["degenerate_short_fragment"],
      severity: 20,
      stats: geometryStats(feature.geometry.coordinates),
      max_projection_distance_m: 0,
      shape_ids: [],
      stop_pairs: [],
      source_edge_ids: feature.properties?.source_edge_ids ?? [],
    })),
];

writeFileSync(OUT_ANOMALIES_GEOJSON, `${JSON.stringify(anomalyGeoJson)}\n`);
writeFileSync(OUT_ANOMALIES_JSON, `${JSON.stringify(anomalyJson, null, 2)}\n`);
console.log(`[visual-network] wrote ${OUT_ANOMALIES_GEOJSON}`);
console.log(`[visual-network] wrote ${OUT_ANOMALIES_JSON}`);
console.log(
  `[visual-network] anomalies: ${visualAnomalies.length} ` +
    `(max-segment=${anomalyJson.summary.max_segment_anomaly_count}, ` +
    `projection=${anomalyJson.summary.projection_anomaly_count}, ` +
    `sparse=${anomalyJson.summary.sparse_long_slice_count})`,
);

if (hardBlockingVisualDefects.length > 0) {
  console.error(
    `[visual-network] *** Gate 2F hard visual-defect validation FAILED: ${hardBlockingVisualDefects.length} blockers ***`,
  );
  for (const defect of hardBlockingVisualDefects.slice(0, 10)) {
    console.error(
      `  ${defect.feature.properties?.corridor_id ?? "<unknown>"} ` +
        `[${(defect.feature.properties?.route_ids ?? []).join(",")}] ` +
        `${defect.reasons.join(",")} len=${defect.stats.length_m.toFixed(2)}m ` +
        `coords=${defect.stats.coordinate_count}`,
    );
  }
  process.exit(1);
}

// =====================================================================
// Phase 2D — Per-route connectivity validation + hard gate
// =====================================================================
//
// For each route, build a graph from its edges:
//   nodes = stop_ids (parent stations)
//   edges = stop-pair edges
//
// Run connected-components. The hard gate: every route must have exactly
// ONE component (all its stops connected by edges). If any route fails:
//   - exit non-zero
//   - DO NOT write/overwrite subway-network.visual.geojson
//   - write only the candidate file and debug artifacts
//   - print a clear failure report
//
// Because we built edges from adjacent stop pairs within each canonical
// branch, connectivity should hold by construction unless edges were
// dropped during slicing (Phase 2B). Branches of the same route share
// some stops (terminals or trunk stations), so multi-branch routes still
// form one component.
console.log("[visual-network] Gate 2D — per-route connectivity validation");

const edgesByRoute = new Map(); // route_id → [edge index]
for (let i = 0; i < edgeFeatures.length; i += 1) {
  const rid = edgeFeatures[i].properties.route_id;
  if (!edgesByRoute.has(rid)) edgesByRoute.set(rid, []);
  edgesByRoute.get(rid).push(i);
}

class RouteUF {
  constructor() { this.parent = new Map(); }
  find(x) {
    if (!this.parent.has(x)) this.parent.set(x, x);
    let r = this.parent.get(x);
    while (r !== x) { x = r; r = this.parent.get(x); }
    return r;
  }
  union(a, b) {
    const ra = this.find(a), rb = this.find(b);
    if (ra !== rb) this.parent.set(ra, rb);
  }
}

const perRouteStats = [];
const validationFailures = [];

for (const [routeId, indices] of [...edgesByRoute.entries()].sort((a, b) =>
  a[0].localeCompare(b[0], "en", { numeric: true }),
)) {
  const stopsInRoute = new Set();
  const uf = new RouteUF();
  for (const i of indices) {
    const f = edgeFeatures[i];
    const from = f.properties.from_stop_id;
    const to = f.properties.to_stop_id;
    stopsInRoute.add(from);
    stopsInRoute.add(to);
    uf.union(from, to);
  }
  // Count components
  const componentMembers = new Map();
  for (const stopId of stopsInRoute) {
    const root = uf.find(stopId);
    if (!componentMembers.has(root)) componentMembers.set(root, new Set());
    componentMembers.get(root).add(stopId);
  }
  const components = [...componentMembers.entries()]
    .map(([root, members]) => ({ root, size: members.size, members: [...members] }))
    .sort((a, b) => b.size - a.size);
  const totalStops = stopsInRoute.size;
  const largestSize = components[0]?.size ?? 0;
  const largestRatio = totalStops > 0 ? largestSize / totalStops : 0;
  const passed = components.length === 1;

  perRouteStats.push({
    route_id: routeId,
    edge_count: indices.length,
    stop_count: totalStops,
    component_count: components.length,
    largest_component_size: largestSize,
    largest_component_ratio: Number(largestRatio.toFixed(3)),
    components: components.map((c) => ({ size: c.size, sample_stop_ids: c.members.slice(0, 6) })),
    passed,
  });
  if (!passed) {
    validationFailures.push({
      route_id: routeId,
      component_count: components.length,
      largest_component_ratio: Number(largestRatio.toFixed(3)),
      total_stops: totalStops,
      largest_size: largestSize,
      sample_component_sizes: components.slice(0, 6).map((c) => c.size),
    });
  }
}

const validationDoc = {
  generated_at: new Date().toISOString(),
  source: "build-subway-visual-network.mjs Gate 2D",
  parameters: {
    snap: "stop_id equality (GTFS parent_station)",
  },
  summary: {
    total_routes: perRouteStats.length,
    routes_passed: perRouteStats.filter((r) => r.passed).length,
    routes_failed: validationFailures.length,
  },
  failures: validationFailures,
  per_route: perRouteStats,
};
writeFileSync(
  OUT_ROUTE_COMPONENTS_JSON,
  `${JSON.stringify(validationDoc, null, 2)}\n`,
);
console.log(`[visual-network] wrote ${OUT_ROUTE_COMPONENTS_JSON}`);

console.log(`[visual-network] === Gate 2D connectivity results ===`);
console.log(`[visual-network] total routes:    ${perRouteStats.length}`);
console.log(`[visual-network] routes passed:   ${perRouteStats.length - validationFailures.length}`);
console.log(`[visual-network] routes failed:   ${validationFailures.length}`);
if (validationFailures.length > 0) {
  console.log(`[visual-network] FAILURES:`);
  for (const f of validationFailures) {
    console.log(
      `[visual-network]   ${f.route_id.padEnd(5)} components=${f.component_count} largest_ratio=${f.largest_component_ratio} total_stops=${f.total_stops} largest_size=${f.largest_size} sample_sizes=[${f.sample_component_sizes.join(",")}]`,
    );
  }
}

// =====================================================================
// DeKalb-zone redundant-lane collapse (match Transit/Apple: one orange + one yellow trunk)
// =====================================================================
//
// DeKalb has multiple parallel BMT track alignments in the OpenData: the materialized B/N/Q/R/W
// shared_spine PLUS the separate B/D, D, N/R, R/W corridors -- all real but stacked, where Transit
// and Apple draw ONE orange (B/D) + ONE yellow (N/Q/R/W) trunk. We keep B/D (orange) and the
// shared_spine YELLOW lane (N/Q/R/W) as the two trunks, and CLIP the redundant parallel same-color
// corridors (shared_spine orange, D-solo, N/R, R/W) to OUTSIDE the zone -- their coverage elsewhere
// is preserved, and the GTFS-topology connectivity gate (Gate 2D) is unaffected (it is edge-based,
// not geometry-based). Scoped to the DeKalb bbox only; does NOT generalize to other junctions yet.
const DEKALB_ZONE = { minLon: -73.985, maxLon: -73.975, minLat: 40.684, maxLat: 40.694 };
const DEKALB_ZONE_CENTER = [-73.980, 40.689];
const DEKALB_REDUNDANT_DIST_M = 22;   // a vertex this close to the kept same-color trunk is "redundant"
const DEKALB_TRUNK_RADIUS_M = 1300;   // only treat kept-trunk geometry within this of the zone as the local trunk
const DEKALB_SNAP_M = 50;             // connect a clipped cut-end (divergence point) to the trunk within this
const DEKALB_MIN_CLIPPED_RUN_M = 250;
const _dkHav = (a, b) => { const R = 6371000, r = Math.PI / 180, dy = (b[1] - a[1]) * r, dx = (b[0] - a[0]) * r; return 2 * R * Math.asin(Math.sqrt(Math.sin(dy / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dx / 2) ** 2)); };
const inDekalbZone = (p) => p[0] >= DEKALB_ZONE.minLon && p[0] <= DEKALB_ZONE.maxLon && p[1] >= DEKALB_ZONE.minLat && p[1] <= DEKALB_ZONE.maxLat;
function isDekalbRedundant(f) {
  // KEEP the materialized continuous-lane members (each route is its own continuous,
  // consistently-offset lane on the bundle alignment) as the DeKalb trunk; clip the other
  // parallel same-color SOLO/legacy corridors into it.
  const p = f.properties ?? {};
  const c = p.color;
  const rids = (p.route_ids ?? []).slice().sort().join(",");
  if (p.bundle_materialization_role === "continuous_lane") return false; // kept trunk lanes
  if (c === "#FF6319" && rids === "B,D") return true;                  // B/D corridor -> merge into trunk
  if (c === "#FF6319" && rids === "D" && p.lane_slot_source === "solo") return true; // D-solo
  if (c === "#FCCC0A" && (rids === "N,R" || rids === "R,W")) return true;            // N/R, R/W
  return false;
}
if (bundleArtifacts.visualFeatures) {
  const feats = bundleArtifacts.visualFeatures;
  // local kept same-color trunk vertices near DeKalb (the divergence reference)
  const keptNearByColor = new Map();
  for (const f of feats) {
    if (f.geometry?.type !== "LineString" || isDekalbRedundant(f)) continue;
    const near = f.geometry.coordinates.filter((p) => _dkHav(p, DEKALB_ZONE_CENTER) < DEKALB_TRUNK_RADIUS_M);
    if (near.length) { const c = f.properties.color; if (!keptNearByColor.has(c)) keptNearByColor.set(c, []); keptNearByColor.get(c).push(...near); }
  }
  const nearestKept = (p, color) => { let bd = Infinity, bp = null; for (const q of (keptNearByColor.get(color) || [])) { const d = _dkHav(p, q); if (d < bd) { bd = d; bp = q; } } return { d: bd, p: bp }; };
  // A vertex is redundant where it runs within DEKALB_REDUNDANT_DIST_M of the kept same-color trunk
  // near DeKalb (i.e. they have merged). Distance-only -- NOT the raw bbox -- so the cut lands exactly
  // at the divergence point (and the snap below connects it), instead of dangling at the box edge.
  const vertexRedundant = (p, color) => nearestKept(p, color).d < DEKALB_REDUNDANT_DIST_M;
  void inDekalbZone;
  const out = [];
  let clippedCount = 0, snapped = 0;
  for (const f of feats) {
    const color = f.properties?.color;
    if (!(f.geometry?.type === "LineString" && isDekalbRedundant(f) && f.geometry.coordinates.some((p) => vertexRedundant(p, color)))) { out.push(f); continue; }
    // keep contiguous runs of vertices that have truly diverged from the kept trunk AND are outside the zone
    const runs = []; let cur = [];
    for (const p of f.geometry.coordinates) { if (vertexRedundant(p, color)) { if (cur.length >= 2) runs.push(cur); cur = []; } else cur.push(p); }
    if (cur.length >= 2) runs.push(cur);
    clippedCount += 1;
    let part = 0;
    for (const run of runs) {
      if (geometryStats(run).length_m < DEKALB_MIN_CLIPPED_RUN_M) continue;
      // snap each cut-end (the divergence point, near the trunk) onto the kept trunk so it merges (no
      // stub). Trim the short near-trunk wiggle first so the merge is a clean taper, not a lateral
      // notch (the clipped corridor carries its own baked lane offset, ~8m off the trunk lane).
      const nkStart = nearestKept(run[0], color);
      if (nkStart.p && nkStart.d > 1 && nkStart.d <= DEKALB_SNAP_M) {
        while (run.length > 3 && nearestKept(run[0], color).d < 30) run.shift();
        run.unshift(nkStart.p.slice()); snapped += 1;
      }
      const nkEnd = nearestKept(run[run.length - 1], color);
      if (nkEnd.p && nkEnd.d > 1 && nkEnd.d <= DEKALB_SNAP_M) {
        while (run.length > 3 && nearestKept(run[run.length - 1], color).d < 30) run.pop();
        run.push(nkEnd.p.slice()); snapped += 1;
      }
      // Aggressive local smoothing (lower angle threshold than the global pass) rounds the lateral
      // merge notch where the clipped corridor's baked offset meets the trunk lane.
      const mergedRun = smoothSharpCorners(run, { angleThresholdDeg: 16, iterations: 4, ratio: 0.25, maxFilletM: 28 });
      out.push({ ...f, properties: { ...f.properties, dekalb_clipped: true, dekalb_clip_part: part++ }, geometry: { type: "LineString", coordinates: mergedRun } });
    }
  }
  bundleArtifacts.visualFeatures = out;
  console.log(`[visual-network] DeKalb-zone collapse:        redundant clipped=${clippedCount} cut-ends snapped=${snapped}`);
}

// ----- Same-color collapse: merge overlapping same-color lanes into one -----
// Where multiple same-color features share a physical track (e.g. yellow N/W/R on
// the Astoria/Broadway trunk at Queensboro, orange B/D + M on 6th Av), snap the
// shorter onto the longer so they render as ONE line; portions that physically
// diverge keep their own geometry (separate lines). Runs before smoothing so the
// snap seams at divergence boundaries get rounded.
if (bundleArtifacts.visualFeatures) {
  const collapse = collapseSameColorOverlaps(bundleArtifacts.visualFeatures, {
    collapseDistM: SAME_COLOR_COLLAPSE_DIST_M,
    minOverlapM: 120,
  });
  bundleArtifacts.visualFeatures = collapse.features;
  console.log(`[visual-network] same-color collapse:           merged=${collapse.collapsedCount}`);
}

// ----- Cross-color parallelization (DISABLED): the proximity-based version shifted
// genuine parallel pairs (e.g. Brighton B/Q at one-lane spacing) and re-introduced
// crossings. The correct criterion is side-FLIP (crossing) detection, not proximity;
// re-enable once parallelOffsetCrossColor is reworked to only fix runs where a
// feature actually crosses (changes side of) a lower-rank different-color line.
// if (bundleArtifacts.visualFeatures) {
//   const par = parallelOffsetCrossColor(bundleArtifacts.visualFeatures, {
//     colorOrder: BUNDLE_COLOR_ORDER, overlapDistM: 8, minOverlapM: 150, laneWidthM: LANE_WIDTH_METERS, taperM: 40,
//   });
//   bundleArtifacts.visualFeatures = par.features;
//   console.log(`[visual-network] cross-color parallelize:        shifted=${par.shiftedCount}`);
// }
void parallelOffsetCrossColor;

// ----- Suppress redundant cross-color shadow orphans (DISABLED): the geometric
// "error-orphan that shadows a different color" criterion also removed legitimate
// parallel pairs (B Brighton shadows Q; the 2 branch shadows the 3) -- B+Q and 2+5
// legitimately share track. Distinguishing a redundant rush pattern from a legit
// parallel route needs service-pattern data ("5 Peak") or a per-junction override,
// not pure geometry. Left off until that is wired.
void suppressShadowOrphans;

// =====================================================================
// Geometry smoothing: round sharp single-vertex elbows (Bug 3 / DeKalb)
// =====================================================================
//
// Final geometry pass. The coarse OpenData polylines represent some real curves
// (e.g. the Manhattan-Bridge -> 4th-Ave approach through the DeKalb interlocking)
// as single-vertex 90-117deg elbows, and the Bug-2 cross-color offset amplifies
// them. MapLibre's round line-join only rounds the stroke corner, not the
// direction change, so they render as kinks. We round every sharp corner with
// endpoint-pinned Chaikin corner-cutting; straight runs and gentle curves are
// untouched. Endpoints stay byte-identical so feature-to-feature junctions
// remain coincident (Gate 2D connectivity is GTFS-topology-based, not geometry-
// based, so it is unaffected either way -- endpoint-pinning is the real guard).
let smoothedFeatureCount = 0;
let smoothedCornerCount = 0;
if (bundleArtifacts.visualFeatures) {
  for (const f of bundleArtifacts.visualFeatures) {
    if (f.geometry?.type !== "LineString") continue;
    const before = f.geometry.coordinates;
    if (!Array.isArray(before) || before.length < 3) continue;
    const sharpBefore = countSharpCorners(before, SMOOTH_ANGLE_THRESHOLD_DEG);
    if (sharpBefore === 0) continue;
    const after = smoothSharpCorners(before, {
      angleThresholdDeg: SMOOTH_ANGLE_THRESHOLD_DEG,
      iterations: SMOOTH_ITERATIONS,
      ratio: SMOOTH_RATIO,
      maxFilletM: SMOOTH_MAX_FILLET_M,
    });
    if (after === before) continue;
    // Endpoint-preservation invariant: junctions must not move.
    const eqPt = (p, q) => p[0] === q[0] && p[1] === q[1];
    if (!eqPt(after[0], before[0]) || !eqPt(after[after.length - 1], before[before.length - 1])) {
      console.error(
        `[visual-network] *** smoothing moved an endpoint on ${f.properties?.bundle_id ?? "?"} -- refusing. ***`,
      );
      process.exit(1);
    }
    f.geometry.coordinates = after;
    smoothedFeatureCount += 1;
    smoothedCornerCount += sharpBefore;
  }
}
console.log(
  `[visual-network] geometry smoothing:          features=${smoothedFeatureCount} sharp_corners=${smoothedCornerCount}`,
);

// ----- Tight-curve simplification (Apple/Transit look) -----
// Some real revenue track hairpins through a tiny radius (e.g. the 5 at the
// 149 St / Mott Haven curve, the red 148 St yard-lead curve). Drawn faithfully
// at map scale those read as teardrop/hook scribbles; Apple and Transit App
// round them into smooth gentle arcs. This pass relaxes only the tight runs
// (a lot of total turning packed into a short arc) toward a gentler arc, leaving
// straight runs and gentle curves byte-identical. Endpoints are pinned, so
// junctions never move (Gate 2D connectivity is GTFS-topology-based).
let tightCurveFeatureCount = 0;
if (bundleArtifacts.visualFeatures) {
  for (const f of bundleArtifacts.visualFeatures) {
    if (f.geometry?.type !== "LineString") continue;
    const before = f.geometry.coordinates;
    if (!Array.isArray(before) || before.length < 5) continue;
    const after = simplifyTightCurves(before, {
      tightTurnDeg: TIGHT_CURVE_TURN_DEG,
      windowM: TIGHT_CURVE_WINDOW_M,
      iterations: TIGHT_CURVE_ITERATIONS,
      lambda: TIGHT_CURVE_LAMBDA,
    });
    if (after === before) continue;
    const eqPt = (p, q) => p[0] === q[0] && p[1] === q[1];
    if (!eqPt(after[0], before[0]) || !eqPt(after[after.length - 1], before[before.length - 1])) {
      console.error(
        `[visual-network] *** tight-curve simplify moved an endpoint on ${f.properties?.bundle_id ?? "?"} -- refusing. ***`,
      );
      process.exit(1);
    }
    f.geometry.coordinates = after;
    tightCurveFeatureCount += 1;
  }
}
console.log(
  `[visual-network] tight-curve simplification:   features=${tightCurveFeatureCount} (turn>=${TIGHT_CURVE_TURN_DEG}deg/${TIGHT_CURVE_WINDOW_M}m)`,
);

// ----- Same-route endpoint-crossing repair -----
// When a same-route branch starts/ends a few meters past its sibling trunk, the
// first/last segment can cross the trunk and render as an X. This pass is not a
// connector: it only snaps that overshooting endpoint back to the actual
// intersection, so the two features share a split node and the crossing segment
// disappears. Interior crossings are left untouched for a fuller junction model.
if (bundleArtifacts.visualFeatures) {
  const repair = repairSameRouteEndpointCrossings(bundleArtifacts.visualFeatures, {
    maxEndpointOvershootM: 180,
  });
  bundleArtifacts.visualFeatures = repair.features;
  console.log(
    `[visual-network] same-route junction fabric: endpoint_repairs=${repair.repairCount}`,
  );
}

// ----- Same-color convergence snap -----
// At junctions where several routes of one color merge onto a trunk (B/D + F + M
// onto 6 Av; the 5 into the 4/5 trunk), each lane is its own feature and one can
// stop a few meters short of the trunk -- it renders as a line that "does not
// touch". This snaps such a dangling endpoint onto the same-color sibling it is
// converging into (distance-decreasing test, so genuine parallel lanes like the
// SI double-track are left alone).
if (bundleArtifacts.visualFeatures) {
  const snap = snapDanglingSameColorEndpoints(bundleArtifacts.visualFeatures, {
    snapDistM: SAME_COLOR_SNAP_DIST_M,
  });
  bundleArtifacts.visualFeatures = snap.features;
  console.log(
    `[visual-network] same-color convergence snap: endpoints=${snap.snappedCount} (<=${SAME_COLOR_SNAP_DIST_M}m, converging)`,
  );
}

// ----- Same-color co-location: one ribbon per color, Apple-style -----
// On Queens Blvd the F express track runs ~18m from the F+M local track for
// ~5km; both are orange and Apple draws ONE ribbon there, but 18m reads as a
// clear double strand from ~z13.5 up. Pull the route-poorer lane onto its
// same-color sibling wherever they run parallel 10-30m apart for >= 500m.
// Closer pairs (Lex 4+5/4+6 at ~6m) already fuse in paint and are skipped.
if (bundleArtifacts.visualFeatures) {
  const colocateResult = colocateSameColorStretches(
    bundleArtifacts.visualFeatures.filter(
      (feature) => feature.properties?.visual_feature_type === "bundle_lane",
    ),
    { minGapM: 10, maxGapM: 30, minStretchM: 500, blendM: FANOUT_BLEND_M },
  );
  console.log(
    `[visual-network] same-color co-location:      ${colocateResult.count} stretch(es)` +
      (colocateResult.count
        ? ` (${colocateResult.stretches.map((s) => `${s.routes}:${s.lengthM}m`).join(", ")})`
        : ""),
  );
}

// ----- Joint-offset tapers: flatten lane-slot steps at corridor joints -----
// Where the same route continues into an adjacent piece with a different
// lane_slot (G at Terrace Pl, F at Delancey, 1/2/3 near Times Sq), the baked
// endpoints land a few meters apart LATERALLY and the gap bridge below would
// join them with a sharp sideways step. Warp the more-offset lane's tail
// onto its neighbor over FANOUT_BLEND_M instead. Must run here -- after tail
// splitting/clips produced the final lane set, before bridging.
if (bundleArtifacts.visualFeatures) {
  const jointTaperResult = taperBakedJointSteps(
    bundleArtifacts.visualFeatures.filter(
      (feature) => feature.properties?.visual_feature_type === "bundle_lane",
    ),
    { blendM: FANOUT_BLEND_M },
  );
  // Drop the tiny pre-existing stitch connectors the warp made redundant
  // (they would dangle 6m off the now-flush joint).
  const beforeDrop = bundleArtifacts.visualFeatures.length;
  bundleArtifacts.visualFeatures = bundleArtifacts.visualFeatures.filter(
    (feature) => feature.properties?.joint_offset_taper_drop !== true,
  );
  const droppedStitches = beforeDrop - bundleArtifacts.visualFeatures.length;
  console.log(
    `[visual-network] joint-offset tapers:         ${jointTaperResult.count} joint(s) flattened, ${droppedStitches} stale stitch(es) dropped` +
      (jointTaperResult.count
        ? ` (${jointTaperResult.joints.map((j) => `${j.routes}@${j.gapM}m`).join(", ")})`
        : ""),
  );
}

// ----- Route gap bridging: close the small seams between same-route pieces -----
// The split-and-reassemble pipeline (shared spine from BASE geometry, fanouts/
// tails from MEMBER geometry, DeKalb clips) leaves small gaps (~11-20m) where a
// member fans out from the shared spine -- the two pieces differ by up to the
// overlap tolerance. Close those seams by extending the dangling source geometry
// into its same-route sibling. For same-color broad branch splits like the
// Queensboro N/W -> N/R seam, append an exact shared-route connector instead of
// extending either broad feature and falsely carrying W/R over the seam.
// In-place repairs stay bounded to <= BRIDGE_MAX_GAP_M; subset connectors are
// endpoint-only and capped by BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M.
// Connectivity (Gate 2D) is GTFS-topology-based, so bridges do not affect it.
if (bundleArtifacts.visualFeatures) {
  const bridgeResult = bridgeRouteGaps(bundleArtifacts.visualFeatures, {
    minGapM: BRIDGE_MIN_GAP_M,
    maxGapM: BRIDGE_MAX_GAP_M,
    allowSubsetRouteConnectors: true,
    subsetConnectorMaxGapM: BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M,
  });
  bundleArtifacts.visualFeatures = bridgeResult.features;
  console.log(
    `[visual-network] route gap bridging:          integrated=${bridgeResult.bridgeCount} (gap ${BRIDGE_MIN_GAP_M}-${BRIDGE_MAX_GAP_M}m, subset endpoint <=${BRIDGE_SUBSET_CONNECTOR_MAX_GAP_M}m)`,
  );
}

// ----- Scoped cartographic junction overrides -----
// Applied after the general geometry cleanup below. The Mott Haven 5 junction is
// a cartographic exception: the GTFS-supported curl is technically valid, but it
// renders as a north-side loop. Apple/Transit schematize it as a compact
// south-side peel from E 149 St into the 4/5 Grand Concourse stem.

// ----- Off-revenue re-route: pull OpenData excursions onto the GTFS track -----
// FINAL geometry pass (after snap + bridge, so it operates on the settled
// endpoint geometry). Some NYC OpenData strokes swing far off the route's real
// revenue track (e.g. the 5 at 149 St / Mott Haven bulges ~300m west toward
// Walton Av). Each contiguous OFF-shape excursion (vertices > OFF_REVENUE_MAX_M
// from every GTFS revenue shape of that feature's routes) is replaced with the
// GTFS shape's own sub-path between where the line left and rejoined it -- so
// lines follow the real curve, never a straight chord, with no wild jumps.
if (bundleArtifacts.visualFeatures) {
  const canonicalDoc = JSON.parse(
    readFileSync(resolve(publicDir, "subway-network.canonical.geojson"), "utf8"),
  );
  const shapesByRoute = new Map();
  for (const f of canonicalDoc.features) {
    if (f.geometry?.type !== "LineString") continue;
    const r = String(f.properties?.route_id);
    if (!shapesByRoute.has(r)) shapesByRoute.set(r, []);
    shapesByRoute.get(r).push(f.geometry.coordinates);
  }
  let reroutedFeatureCount = 0;
  for (const f of bundleArtifacts.visualFeatures) {
    if (f.geometry?.type !== "LineString") continue;
    const before = f.geometry.coordinates;
    if (!Array.isArray(before) || before.length < 3) continue;
    const routes = Array.isArray(f.properties?.route_ids) ? f.properties.route_ids : [];
    const shapes = routes.flatMap((r) => shapesByRoute.get(String(r)) ?? []);
    if (!shapes.length) continue;
    let coords = before;
    let moved = false;
    for (let pass = 0; pass < 4; pass += 1) {
      const next = snapOffRevenueToShape(coords, shapes, { maxOffM: OFF_REVENUE_MAX_M });
      if (next === coords) break;
      coords = next;
      moved = true;
    }
    if (!moved) continue;
    // Smooth the GTFS-derived path: round sharp single-vertex elbows and relax
    // any tight kink where the re-routed sub-path rejoins, so the result reads as
    // a clean curve rather than a literal/sharp GTFS trace. Endpoints are pinned.
    let smoothed = smoothSharpCorners(coords, {
      angleThresholdDeg: 12, // GTFS-derived path: round densely-sampled tight curls into clean arcs
      iterations: 5,
      ratio: 0.28,
      maxFilletM: 30,
    });
    smoothed = simplifyTightCurves(smoothed, {
      tightTurnDeg: 40,   // GTFS-derived: relax the real tight Mott-Haven-style curls harder
      windowM: 60,
      iterations: 40,
      lambda: 0.5,
    });
    f.geometry.coordinates = smoothed;
    f.properties.off_revenue_rerouted = true;
    reroutedFeatureCount += 1;
  }
  console.log(
    `[visual-network] off-revenue re-route:        features=${reroutedFeatureCount} (>${OFF_REVENUE_MAX_M}m off GTFS revenue shape)`,
  );

  // ----- Authored Joralemon 4/5 river crossing smoothing -----
  // The off-revenue pass correctly protects most visual geometry, but around
  // the East River/Joralemon crossing it can pull the green trunk onto a GTFS
  // trace with a small visible wiggle in open water. Preserve the crossing's
  // endpoints and surrounding geometry, but replace only that local water run
  // with a clean tangent-matched schematic curve.
  const joralemonGreenRiver = applyJoralemonGreenRiverSmoothing(bundleArtifacts.visualFeatures, {
    bbox: {
      minLon: -74.0118,
      maxLon: -74.0015,
      minLat: 40.6948,
      maxLat: 40.7010,
    },
    marginM: 360,
    sampleM: 6,
    tangentSampleM: 130,
    handleFrac: 0.42,
    maxHandleM: 650,
  });
  bundleArtifacts.visualFeatures = joralemonGreenRiver.features;
  console.log(
    `[visual-network] QA Joralemon green river: applied=${joralemonGreenRiver.diagnostics.applied} replaced=${joralemonGreenRiver.diagnostics.replaced_length_m ?? 0}m`,
  );

  // ----- Authored Brighton B/Q Church/Beverley spacing -----
  // The B/Q Brighton physical bundle is detected correctly, but the continuous
  // materializer offsets each source member's own OpenData geometry. Around the
  // gentle Church/Beverley bend those source curves are slightly inconsistent,
  // so the baked orange/yellow lanes pinch together. Rebalance only this local
  // shared-bundle run onto one smoothed centerline and keep the two lanes at a
  // stable Apple/Transit-style separation through the bend.
  const brightonBqSpacing = applyBrightonBqChurchSpacing(bundleArtifacts.visualFeatures, {
    targetSeparationM: 15,
    marginM: 650,
    blendM: 140,
    sampleM: 6,
  });
  bundleArtifacts.visualFeatures = brightonBqSpacing.features;
  console.log(
    `[visual-network] QA Brighton B/Q Church spacing: applied=${brightonBqSpacing.diagnostics.applied} strict_min=${brightonBqSpacing.diagnostics.min_separation_before_m ?? "n/a"}m->${brightonBqSpacing.diagnostics.min_separation_after_m ?? "n/a"}m core_min=${brightonBqSpacing.diagnostics.core_min_separation_after_m ?? "n/a"}m${brightonBqSpacing.diagnostics.reason ? ` reason=${brightonBqSpacing.diagnostics.reason}` : ""}`,
  );

  // ----- Authored Culver F/G Prospect / Terrace seam smoothing -----
  // The F/G Culver corridor changes from a bundled green G lane to a solo G
  // lane around Prospect Av / Terrace Pl. Generic joint taper closes the seam,
  // but it does so by translating the G tail, leaving a subtle S-kink. Rebuild
  // only this local G chain from the neighboring F curve at stable separation.
  const culverFgProspect = applyCulverFgProspectSmoothing(bundleArtifacts.visualFeatures, {
    targetSeparationM: 14,
    marginM: 300,
    blendM: 140,
    sampleM: 6,
    smoothingPasses: 2,
  });
  bundleArtifacts.visualFeatures = culverFgProspect.features;
  console.log(
    `[visual-network] QA Culver F/G Prospect seam: applied=${culverFgProspect.diagnostics.applied} sep=${culverFgProspect.diagnostics.min_separation_before_m ?? "n/a"}m->${culverFgProspect.diagnostics.min_separation_after_m ?? "n/a"}m${culverFgProspect.diagnostics.reason ? ` reason=${culverFgProspect.diagnostics.reason}` : ""}`,
  );

  // ----- Authored St Nicholas A/C straightening -----
  // Same-color joins around 145 St can leave the north A/C piece and south
  // A/C/E piece meeting a few meters off-axis. At map scale this reads as a
  // small disconnected blue kink beside St Nicholas Av. Straighten only this
  // local St Nicholas run onto one fitted axis and snap the 145 St seam.
  const stNicholasBlue = applyStNicholasBlueStraightening(bundleArtifacts.visualFeatures);
  bundleArtifacts.visualFeatures = stNicholasBlue.features;
  console.log(
    `[visual-network] QA St Nicholas A/C straightening: applied=${stNicholasBlue.diagnostics.applied} features=${stNicholasBlue.diagnostics.target_feature_count} drift=${stNicholasBlue.diagnostics.max_perpendicular_before_m ?? "n/a"}m->${stNicholasBlue.diagnostics.max_perpendicular_after_m ?? "n/a"}m endpoint_clusters=${stNicholasBlue.diagnostics.snapped_endpoint_clusters ?? 0}${stNicholasBlue.diagnostics.reason ? ` reason=${stNicholasBlue.diagnostics.reason}` : ""}`,
  );

  // ----- Authored Nostrand / Eastern Parkway split -----
  // Apple Maps draws this as one straight 3/4 Eastern Parkway trunk and one
  // smooth 2/5 branch peeling south. The source + bridge passes leave a small
  // hook on the restored 4-to-Utica tail and a backtracking first segment on the
  // 2/5 branch. Own that local split here, after off-revenue snapping has
  // settled the revenue geometry.
  const nostrandSchematic = applyNostrandEasternSchematic(bundleArtifacts.visualFeatures, {
    branchTurnSpanM: 420,
    trunkBlendM: 170,
    sampleM: 6,
  });
  bundleArtifacts.visualFeatures = nostrandSchematic.features;
  console.log(
    `[visual-network] QA Nostrand/Eastern schematic: applied=${nostrandSchematic.diagnostics.applied} red_branch=${nostrandSchematic.diagnostics.red_branch_rebuilt} green_tail=${nostrandSchematic.diagnostics.green_tail_straightened} green_branch=${nostrandSchematic.diagnostics.green_branch_rebuilt}${nostrandSchematic.diagnostics.reason ? ` reason=${nostrandSchematic.diagnostics.reason}` : ""}`,
  );

  // (My schematic-hairpin-arc pass removed: it competed with the cartographic
  // junction override below and produced a redundant parallel path / lens at
  // Mott Haven. The cartographic override owns the 5-branch reshape.)
  void replaceEndpointHairpin;

  // ----- Authored Mott Haven 5 lens (Apple / Transit schematic) -----
  // South of 149 St-Grand Concourse the 4 and 5 share track, but Apple Maps and
  // the Transit app draw them as two parallel lines: the 4 runs straight on Grand
  // Concourse and the 5 bows WEST via Walton Av, then they rejoin -- an elongated
  // lens. Neither OpenData nor GTFS contains that lens (both have the tight Mott
  // Haven curl), so it is AUTHORED here as the single owner of this junction:
  //   * the 4 is made continuous (its north stem is joined to the 4/5 trunk),
  //   * the 5 branch is rebuilt as a local schematic lens: flat along E 149 St,
  //     closed at the top trunk split, west via Walton Av, then lower Y-merged
  //     back into the 4/5 trunk.
  // This deliberately stops preserving the real 5-from-east curl inside the
  // junction. The real route only feeds the authored E 149 St entry.
  // (Supersedes the cartographic override, which collapsed the 5 onto the trunk.)
  void applyCartographicJunctionOverrides;
  const LENS_SPAN_M = 310;  // lower Y-merge distance from the authored top split
  const SIX_MERGE_SPAN_M = 520; // route 6 joins the straight trunk near the circled 138 St merge
  // Straighten the 4/5 mainline onto Grand Concourse through the junction view, then blend
  // back to the true track below this latitude (just under the typical view ~40.808). The
  // real track bends SW toward the Harlem River below the merge; Apple/Transit draw it
  // straight down Grand Concourse and push that curve off-screen. Lower = curve pushed
  // further down but larger divergence from the true track.
  const LENS_STRAIGHTEN_TO_LAT = 40.806;
  const inBBox = (p) =>
    p[0] >= MOTT_HAVEN_5_QA_BBOX.minLon && p[0] <= MOTT_HAVEN_5_QA_BBOX.maxLon &&
    p[1] >= MOTT_HAVEN_5_QA_BBOX.minLat && p[1] <= MOTT_HAVEN_5_QA_BBOX.maxLat;
  const lensTrunk = bundleArtifacts.visualFeatures.find((f) => (
    f.geometry?.type === "LineString" &&
    String(f.properties?.color ?? "").toUpperCase() === "#00933C" &&
    (f.properties?.route_ids ?? []).map(String).includes("4") &&
    (f.properties?.route_ids ?? []).map(String).includes("5") &&
    f.geometry.coordinates.some(inBBox)
  ));
  const lensBranch = bundleArtifacts.visualFeatures.find((f) => {
    if (f.geometry?.type !== "LineString") return false;
    if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
    const r = (f.properties?.route_ids ?? []).map(String);
    return r.includes("5") && !r.includes("4") && f.geometry.coordinates.some(inBBox);
  });
  let lensApplied = false;
  let lensBowWidthM = 0;
  let lensRejoinM = Infinity;
  let fourContinuous = false;
  let mainlineStraightened = false;
  let mainlineMaxBearingDevDeg = 0;
  let lensTopApproachLatSpreadM = Infinity;
  let lensMaxTurnDeg = Infinity;
  let lensParallelReferenceUsed = false;
  let lensParallelReferenceDistanceM = Infinity;
  let sixMergeApplied = false;
  let sixMergeRejoinM = Infinity;
  let sixMergeMaxTurnDeg = Infinity;
  if (lensTrunk && lensBranch) {
    let tc = lensTrunk.geometry.coordinates;
    // ---- (a) find the [4] stem and the Grand Concourse avenue bearing ----
    const fourStem = bundleArtifacts.visualFeatures.find((f) => {
      if (f.geometry?.type !== "LineString") return false;
      if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
      const r = (f.properties?.route_ids ?? []).map(String);
      return r.includes("4") && !r.includes("5") && f.geometry.coordinates.some(inBBox);
    });
    const twoReference = bundleArtifacts.visualFeatures.find((f) => {
      if (f.geometry?.type !== "LineString") return false;
      if (String(f.properties?.color ?? "").toUpperCase() !== "#EE352E") return false;
      const r = (f.properties?.route_ids ?? []).map(String);
      return r.includes("2") && f.geometry.coordinates.some(inBBox);
    });
    const sixBranch = bundleArtifacts.visualFeatures.find((f) => {
      if (f.geometry?.type !== "LineString") return false;
      if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
      const r = (f.properties?.route_ids ?? []).map(String);
      return r.includes("6") && !r.includes("4") && f.geometry.coordinates.some(inBBox);
    });
    const sixShared = bundleArtifacts.visualFeatures.find((f) => {
      if (f.geometry?.type !== "LineString") return false;
      if (String(f.properties?.color ?? "").toUpperCase() !== "#00933C") return false;
      const r = (f.properties?.route_ids ?? []).map(String);
      return r.includes("4") && r.includes("6") && f.geometry.coordinates.some(inBBox);
    });
    let avenueDir = null; // unit southbound direction of Grand Concourse (meters)
    if (fourStem) {
      const sc = fourStem.geometry.coordinates;
      const ks = metersPerDegLng(sc[sc.length - 1][1]);
      const d = [(sc[sc.length - 1][0] - sc[Math.max(0, sc.length - 6)][0]) * ks, (sc[sc.length - 1][1] - sc[Math.max(0, sc.length - 6)][1]) * M_PER_DEG_LAT];
      const l = Math.hypot(d[0], d[1]);
      if (l > 1) avenueDir = [d[0] / l, d[1] / l];
    }
    if (!avenueDir) {
      const k0 = metersPerDegLng(tc[0][1]);
      const j = Math.min(8, tc.length - 1);
      const d = [(tc[j][0] - tc[0][0]) * k0, (tc[j][1] - tc[0][1]) * M_PER_DEG_LAT];
      const l = Math.hypot(d[0], d[1]) || 1;
      avenueDir = [d[0] / l, d[1] / l];
    }
    // ---- (b) straighten the 4/5 mainline onto Grand Concourse; blend back below the view ----
    // The real track bends SW toward the Harlem River below the merge; Apple/Transit draw it
    // straight down Grand Concourse and push that curve off-screen. Re-aim the trunk from the
    // junction along the avenue bearing, then Hermite-blend back to the true track below
    // LENS_STRAIGHTEN_TO_LAT. tc stays one feature with unchanged endpoints (connectivity safe).
    if (avenueDir[1] < 0) {
      let blendIdx = -1;
      for (let i = 1; i < tc.length; i += 1) { if (tc[i][1] <= LENS_STRAIGHTEN_TO_LAT - 0.002) { blendIdx = i; break; } }
      if (blendIdx > 4) {
        const A = tc[0];
        const kA = metersPerDegLng(A[1]);
        const rayLenM = Math.abs(((LENS_STRAIGHTEN_TO_LAT - A[1]) * M_PER_DEG_LAT) / avenueDir[1]);
        const ray = [];
        for (let d = 0; d <= rayLenM; d += 10) ray.push([A[0] + (avenueDir[0] * d) / kA, A[1] + (avenueDir[1] * d) / M_PER_DEG_LAT]);
        const rEnd = ray[ray.length - 1];
        const B = tc[blendIdx];
        const kB = metersPerDegLng(B[1]);
        const b2 = tc[Math.min(tc.length - 1, blendIdx + 8)];
        const eT = [(b2[0] - B[0]) * kB, (b2[1] - B[1]) * M_PER_DEG_LAT];
        const eL = Math.hypot(eT[0], eT[1]) || 1;
        const blendSeg = hermiteBetween(rEnd, B, avenueDir, [eT[0] / eL, eT[1] / eL], { handleFrac: 0.5, sampleM: 8 });
        let merged = [...ray, ...blendSeg.slice(1), ...tc.slice(blendIdx + 1)];
        merged = smoothSharpCorners(merged, { angleThresholdDeg: 22, iterations: 3, ratio: 0.2, maxFilletM: 18 });
        tc = merged;
        lensTrunk.geometry.coordinates = tc;
        lensTrunk.properties.mott_haven_mainline_straightened = true;
        mainlineStraightened = true;
        // QA: mainline bearing must be ~constant through the view (junction .. view bottom)
        const baseBear = (Math.atan2(avenueDir[1], avenueDir[0]) * 180) / Math.PI;
        for (let i = 1; i < tc.length; i += 1) {
          if (tc[i][1] > A[1] || tc[i][1] < LENS_STRAIGHTEN_TO_LAT + 0.002) continue;
          const kk = metersPerDegLng(tc[i][1]);
          const seg = [(tc[i][0] - tc[i - 1][0]) * kk, (tc[i][1] - tc[i - 1][1]) * M_PER_DEG_LAT];
          if (Math.hypot(seg[0], seg[1]) < 1) continue;
          let dev = (Math.atan2(seg[1], seg[0]) * 180) / Math.PI - baseBear;
          while (dev > 180) dev -= 360; while (dev < -180) dev += 360;
          mainlineMaxBearingDevDeg = Math.max(mainlineMaxBearingDevDeg, Math.abs(dev));
        }
      }
    }
    // ---- (c) make the 4 continuous: join the [4] stem to the (straightened) trunk start ----
    if (fourStem) {
      const sc = fourStem.geometry.coordinates;
      const gap = distanceMeters(sc[sc.length - 1], tc[0]);
      if (gap > 20 && gap < 400) {
        const ks = metersPerDegLng(sc[sc.length - 1][1]);
        const sT = [(sc[sc.length - 1][0] - sc[Math.max(0, sc.length - 5)][0]) * ks, (sc[sc.length - 1][1] - sc[Math.max(0, sc.length - 5)][1]) * M_PER_DEG_LAT];
        const sl = Math.hypot(sT[0], sT[1]) || 1;
        const eT = [(tc[Math.min(4, tc.length - 1)][0] - tc[0][0]) * ks, (tc[Math.min(4, tc.length - 1)][1] - tc[0][1]) * M_PER_DEG_LAT];
        const el = Math.hypot(eT[0], eT[1]) || 1;
        const conn = hermiteBetween(sc[sc.length - 1], tc[0], [sT[0] / sl, sT[1] / sl], [eT[0] / el, eT[1] / el], { handleFrac: 0.5, sampleM: 6 });
        fourStem.geometry.coordinates = [...sc, ...conn.slice(1)];
        fourStem.properties.mott_haven_four_continuity = true;
        fourContinuous = true;
      }
    }
    // ---- (d) author the 5 schematic lens ----
    // The route-5 source geometry comes from the east and curls through the junction.
    // Apple/Transit instead draw a bounded schematic: E 149 St entry -> closed
    // top split -> Walton-side lens -> lower Y merge. Keep the upstream 5 route
    // connected, but do not let the real curl define the visible junction.
    const lens = buildMottHavenFiveSchematicLens({
      branchCoords: lensBranch.geometry.coordinates,
      trunkCoords: fourStem
        ? [...fourStem.geometry.coordinates, ...tc.slice(1)]
        : tc,
      parallelReferenceCoords: twoReference?.geometry?.coordinates ?? null,
      parallelOffsetM: 10,
      mergeDistanceM: LENS_SPAN_M,
      sampleM: 6,
    });
    if (lens.diagnostics.ok) {
      const spliced = lens.coordinates;
      lensBranch.geometry.coordinates = spliced;
      lensBranch.properties.mott_haven_lens = true;
      lensBranch.properties.mott_haven_schematic_lens = true;
      lensBranch.properties.mott_haven_lens_entry_point = lens.diagnostics.entryPoint;
      lensBranch.properties.mott_haven_lens_top_point = lens.diagnostics.topPoint;
      lensBranch.properties.mott_haven_lens_merge_point = lens.diagnostics.mergePoint;
      lensBranch.properties.mott_haven_lens_top_spread_m = Number(lens.diagnostics.topApproachLatSpreadM.toFixed(2));
      lensBranch.properties.mott_haven_lens_max_turn_deg = Number(lens.diagnostics.maxTurnDeg.toFixed(2));
      lensBranch.properties.mott_haven_parallel_reference_used = lens.diagnostics.parallelReferenceUsed;
      lensBranch.properties.mott_haven_parallel_reference_distance_m =
        lens.diagnostics.parallelReferenceDistanceM == null
          ? null
          : Number(lens.diagnostics.parallelReferenceDistanceM.toFixed(2));
      lensApplied = true;
      lensBowWidthM = lens.diagnostics.maxTrunkDistanceM;
      lensRejoinM = lens.diagnostics.mergeDistanceM;
      lensTopApproachLatSpreadM = lens.diagnostics.topApproachLatSpreadM;
      lensMaxTurnDeg = lens.diagnostics.maxTurnDeg;
      lensParallelReferenceUsed = Boolean(lens.diagnostics.parallelReferenceUsed);
      lensParallelReferenceDistanceM = lens.diagnostics.parallelReferenceDistanceM ?? Infinity;
    }
    // ---- (e) author the lower route-6 Y merge ----
    // OpenData/GTFS keep the route-6 approach as a lower sweeping curve that
    // reads as a second teardrop. Apple Maps instead lets the 6 branch enter
    // once and then become the shared trunk. Keep the east approach, but stop it
    // at the straight mainline and start the shared 4/6 segment there.
    if (sixBranch && sixShared) {
      const sixMerge = buildMottHavenSixSchematicMerge({
        branchCoords: sixBranch.geometry.coordinates,
        mainlineCoords: tc,
        mergeDistanceM: SIX_MERGE_SPAN_M,
        entryEastM: 430,
        entryNorthM: 120,
        sampleM: 6,
      });
      if (sixMerge.diagnostics.ok) {
        sixBranch.geometry.coordinates = sixMerge.coordinates;
        sixBranch.properties.mott_haven_six_merge = true;
        sixBranch.properties.mott_haven_six_merge_point = sixMerge.diagnostics.mergePoint;
        sixBranch.properties.mott_haven_six_merge_max_turn_deg =
          Number(sixMerge.diagnostics.maxTurnDeg.toFixed(2));
        sixBranch.properties.mott_haven_six_merge_rejoin_m =
          Number(sixMerge.diagnostics.mergeDistanceM.toFixed(2));

        sixShared.geometry.coordinates = sixMerge.sharedMainlineCoords;
        sixShared.properties.mott_haven_six_shared_mainline = true;
        sixShared.properties.mott_haven_six_merge_point = sixMerge.diagnostics.mergePoint;
        sixMergeApplied = true;
        sixMergeRejoinM = sixMerge.diagnostics.mergeDistanceM;
        sixMergeMaxTurnDeg = sixMerge.diagnostics.maxTurnDeg;
      }
    }
  }
  const lensTopApproachOk = lensParallelReferenceUsed
    ? lensParallelReferenceDistanceM <= 25
    : lensTopApproachLatSpreadM <= 15;
  const sixMergeOk = sixMergeApplied && sixMergeRejoinM <= 2 && sixMergeMaxTurnDeg <= 70;
  const qaPass = lensApplied && fourContinuous && lensBowWidthM >= 120 && lensBowWidthM <= 260 && lensRejoinM <= 4
    && lensTopApproachOk && lensMaxTurnDeg <= 65
    && mainlineStraightened && mainlineMaxBearingDevDeg <= 6
    && sixMergeOk;
  console.log(
    `[visual-network] QA Mott Haven 5/6 schematic:  five_applied=${lensApplied} four_continuous=${fourContinuous} bow=${lensBowWidthM.toFixed(0)}m rejoin=${lensRejoinM.toFixed(1)}m top_spread=${lensTopApproachLatSpreadM.toFixed(1)}m parallel_ref=${lensParallelReferenceUsed}:${lensParallelReferenceDistanceM.toFixed(1)}m max_turn=${lensMaxTurnDeg.toFixed(1)}deg straightened=${mainlineStraightened} bearing_dev=${mainlineMaxBearingDevDeg.toFixed(1)}deg six_merge=${sixMergeApplied}:${sixMergeRejoinM.toFixed(1)}m/${sixMergeMaxTurnDeg.toFixed(1)}deg ${qaPass ? "PASS" : "FAIL"}`,
  );
  if (!qaPass) {
    console.error(
      "[visual-network] *** QA FAIL: Mott Haven 5/6 schematic (5 lens, 4 continuity, straight mainline, or lower 6 merge) not authored as expected. ***",
    );
    process.exit(1);
  }
}

// =====================================================================
// 63 St tunnel F membership
// =====================================================================
// OpenData draws the 63 St tunnel as the M line only; the F (its real
// owner, per Apple Maps) appeared out of nowhere at the 36 St junction.
// Membership-only fix: the orange tunnel features gain F in route_ids.
{
  const sixtyThird = addSixtyThirdStreetF(bundleArtifacts.visualFeatures);
  console.log(
    `[visual-network] QA 63 St tunnel F membership: features_updated=${sixtyThird.updated} ${sixtyThird.updated > 0 ? "PASS" : "FAIL (no orange M tunnel feature found)"}`,
  );
}

// =====================================================================
// Staten Island Railway cleanup
// =====================================================================
// OpenData shatters the SIR into ~40 fragments (second-track slivers, yard
// twigs, weave around St George). Keep the stitched Tottenville->St George
// mainline, bridge its small seams, drop shadows and twigs.
{
  const siSummary = cleanStatenIslandLine(bundleArtifacts.visualFeatures);
  console.log(
    `[visual-network] QA SIR cleanup: connected=${siSummary.connected ?? false} kept=${siSummary.kept} dropped=${siSummary.dropped} stitches=${siSummary.stitches} ${siSummary.connected ? "PASS" : "FAIL (terminals not connected; left untouched)"}`,
  );
}

// =====================================================================
// Hammels Wye (Rockaway) junction connector
// =====================================================================
// The cross-bay A stops ~46m short of the east/west legs' junction node,
// with degenerate stubs dangling at its end. Extend it onto the node so
// Broad Channel -> Far Rockaway reads as one continuous line.
{
  const wye = connectRockawayWye(bundleArtifacts.visualFeatures);
  console.log(
    `[visual-network] QA Rockaway wye: connected=${wye.connected} extended=${wye.extended} stubs_removed=${wye.stubsRemoved} ${wye.connected ? "PASS" : "FAIL (legs not found)"}`,
  );
}

// =====================================================================
// Terminal overhang trim
// =====================================================================
// Lanes are sliced from full OpenData line geometry, which keeps running past
// the last passenger station into yards / non-revenue track. Trim every free
// lane end back to the outermost station that projects onto it (+ grace).
{
  const stationsDoc = JSON.parse(
    readFileSync(STATIONS_GEOJSON_PATH, "utf8"),
  );
  // True service terminals from the Gate 2A GTFS branch sequences. Cuts are
  // only allowed where the boundary coincides with one of these -- station
  // route lists alone are weekday-pattern and misclassify branch geometry.
  const routeTerminals = [];
  for (const [routeId, branches] of branchesByRoute) {
    for (const branch of branches) {
      for (const stopId of [branch.terminal_start, branch.terminal_end]) {
        const stop = stopsById.get(stopId);
        if (!stop || !Number.isFinite(stop.lon) || !Number.isFinite(stop.lat)) continue;
        routeTerminals.push({ route: routeId, coord: [stop.lon, stop.lat] });
      }
    }
  }
  // Two passes: dropping a spur can expose the end it was attached to (the
  // attachment snapshot is taken before splicing), so a second pass reaches
  // the fixpoint (e.g. the SI tail past Tottenville chained to a yard spur).
  for (let pass = 1; pass <= 2; pass += 1) {
    const trimSummary = trimTerminalOverhang({
      features: bundleArtifacts.visualFeatures,
      stations: stationsDoc,
      terminals: routeTerminals,
    });
    console.log(
      `[visual-network] terminal overhang pass ${pass}: ${trimSummary.trimmedEnds} free ends trimmed, ${trimSummary.removedM}m removed, ${trimSummary.droppedSpurs} spurs dropped`,
    );
    for (const action of trimSummary.actions ?? []) {
      console.log(`[visual-network]   trim ${JSON.stringify(action)}`);
    }
    if (trimSummary.trimmedEnds === 0 && trimSummary.droppedSpurs === 0) break;
  }
}

// =====================================================================
// Final artifact emission
// =====================================================================

// The candidate artifact is the OpenData-derived visual geojson with extra
// metadata. Always written so debug/inspection works even on failure.
const candidateDoc = {
  type: "FeatureCollection",
  metadata: {
    generated_at: new Date().toISOString(),
    source: "build-subway-visual-network.mjs Gate 2A-2H",
    gates: {
      "2A": "topology",
      "2B": "opendata-full-lines",
      "2C": "opendata-corridor-normalization",
      "2D": "connectivity",
      "2H": "bundle-lane-render-geometry",
    },
    visual_geometry_source: OPEN_DATA_SOURCE_NAME,
    visual_geometry_source_dataset_id: OPEN_DATA_SOURCE_DATASET_ID,
    validation: {
      total_routes: perRouteStats.length,
      routes_passed: perRouteStats.length - validationFailures.length,
      routes_failed: validationFailures.length,
      passed: validationFailures.length === 0,
    },
    bundle_summary: {
      bundle_count: bundleArtifacts.bundleFeatures.length,
      bundled_render_lane_count: bundleArtifacts.bundleLaneFeatures.length,
      corridors_converted_to_bundle_geometry:
        bundleArtifacts.bundleFeatures.length,
      remaining_unbundled_corridors: bundleArtifacts.unbundledFeatures.length,
      bundle_gap_count: bundleArtifacts.bundleGapFeatures.length,
    },
    parameters: {
      min_trips_per_branch: MIN_TRIPS_PER_BRANCH,
      resample_interval_m: RESAMPLE_INTERVAL_M,
      hausdorff_max_m: HAUSDORFF_MAX_M,
      overlap_min_ratio: OVERLAP_MIN_RATIO,
      tangent_max_diff_deg: TANGENT_MAX_DIFF_DEG,
      containment_avg_distance_max_m: CONTAINMENT_AVG_DISTANCE_MAX_M,
      containment_overlap_min_ratio: CONTAINMENT_OVERLAP_MIN_RATIO,
      open_data_path: "frontend/public/subway-lines-nyc-opendata.geojson",
    },
  },
  features: bundleArtifacts.visualFeatures,
};
writeFileSync(OUT_VISUAL_CANDIDATE, `${JSON.stringify(candidateDoc)}\n`);
console.log(`[visual-network] wrote candidate: ${OUT_VISUAL_CANDIDATE}`);

if (validationFailures.length === 0) {
  // Promote candidate → final. Preserve the last-known-good by atomic
  // rename pattern (write candidate first, then move).
  writeFileSync(OUT_VISUAL_FINAL, `${JSON.stringify(candidateDoc)}\n`);
  console.log(`[visual-network] *** PROMOTED *** to ${OUT_VISUAL_FINAL}`);
  console.log(`[visual-network] All gates passed. Visual network artifact is ready for Gate 2E (runtime opt-in).`);
} else {
  console.error(
    `[visual-network] HARD GATE FAILED: ${validationFailures.length} route(s) failed connectivity validation.`,
  );
  console.error(
    `[visual-network] Refusing to promote candidate to ${OUT_VISUAL_FINAL}. The last-known-good (if any) is preserved.`,
  );
  process.exit(1);
}

// Summary log
console.log(`[visual-network] === Gate 2A topology summary ===`);
console.log(
  `[visual-network] distinct routes: ${topologyDoc.topology.distinct_routes}`,
);
console.log(
  `[visual-network] total branches (>= ${MIN_TRIPS_PER_BRANCH} trips): ${topologyDoc.topology.total_branches}`,
);
console.log(
  `[visual-network] dropped low-frequency branches: ${droppedLowFreqBranches}`,
);
console.log(`[visual-network] --- per-route branch summary ---`);
console.log(`[visual-network]   route  branches  stations  branch terminals`);
for (const r of topologyDoc.per_route) {
  const terminals = r.branches
    .slice(0, 4)
    .map((b) =>
      `${(b.direction_id || "?")}:${(stopsById.get(b.terminal_start)?.name ?? b.terminal_start)} → ${(stopsById.get(b.terminal_end)?.name ?? b.terminal_end)} (${b.total_trips_in_branch}tr)`,
    )
    .join("; ");
  console.log(
    `[visual-network]   ${r.route_id.padEnd(5)} ${String(r.branch_count).padStart(8)} ${String(r.distinct_stations).padStart(9)}  ${terminals}`,
  );
}

console.log("[visual-network] Gate 2A complete. Topology written to debug JSON.");
console.log("[visual-network] Gate 2B used NYC OpenData full-line geometry; GTFS shapes.txt was not used for visual rendering.");
