#!/usr/bin/env node

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const publicDir = resolve(frontendRoot, "public");
const groupsPath = resolve(publicDir, "subway-network.corridor-groups.json");
const visualPath = resolve(publicDir, "subway-network.group-visual.geojson");
const familyVisualPath = resolve(publicDir, "subway-network.family-visual.geojson");
const endpointsPath = resolve(publicDir, "debug", "group-endpoints.geojson");

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

const REQUIRED_GROUP_FAMILIES = ["N-Q-R-W", "B-D-F-M", "4-5-6", "1-2-3"];
const VISUAL_ROUTE_NORMALIZATION = new Map([
  ["FX", "F"],
  ["7X", "7"],
  ["6X", "6"],
]);
// `corridor_id` was a legacy 4B/4C runtime property and is intentionally NOT
// in this list anymore: build-corridor-groups.mjs now stamps it on features
// belonging to a manual cross-family override (DeKalb/Atlantic, 6 Av trunk,
// etc.). Solo/in-family-group features still carry corridor_id: null.
const FORBIDDEN_PROPERTIES = [
  "corridor_override",
  "transition_kind",
  "junction_transition",
  "transition_length_meters",
  "render_segment_index",
  "render_source_key",
];

function normalizeRouteId(value) {
  const routeId = String(value ?? "").trim().toUpperCase();
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
  const lat1 = from[1] * Math.PI / 180;
  const lat2 = to[1] * Math.PI / 180;
  const dLat = lat2 - lat1;
  const dLng = (to[0] - from[0]) * Math.PI / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * radius * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function lineLengthMeters(coordinates) {
  let total = 0;
  for (let index = 1; index < coordinates.length; index += 1) {
    total += distanceMeters(coordinates[index - 1], coordinates[index]);
  }
  return total;
}

assert.ok(existsSync(familyVisualPath), "family visual artifact must exist before grouping");
assert.ok(existsSync(groupsPath), "corridor groups artifact must exist");
assert.ok(existsSync(visualPath), "group visual artifact must exist");
assert.ok(existsSync(endpointsPath), "group endpoint diagnostics artifact must exist");

const groups = JSON.parse(readFileSync(groupsPath, "utf8"));
const visual = JSON.parse(readFileSync(visualPath, "utf8"));
const endpoints = JSON.parse(readFileSync(endpointsPath, "utf8"));

assert.equal(groups.source, "build-corridor-groups", "groups source must identify builder");
assert.ok(Array.isArray(groups.groups), "groups artifact must include groups array");
assert.ok(groups.groups.length > 0, "at least one corridor group must be derived");
assert.equal(visual.type, "FeatureCollection", "group visual must be a FeatureCollection");
assert.ok(Array.isArray(visual.features), "group visual features must be an array");
assert.ok(visual.features.length > 0, "group visual must contain render features");
assert.equal(endpoints.type, "FeatureCollection", "group endpoint diagnostics must be a FeatureCollection");
assert.equal(
  endpoints.features.length,
  visual.features.length * 2,
  "endpoint diagnostics must include one start and one end point per visual feature",
);

const groupById = new Map();
const groupFamilies = new Set();
for (const group of groups.groups) {
  assert.ok(group.group_id, "group must include group_id");
  assert.ok(group.visual_family, `${group.group_id} must include visual_family`);
  assert.ok(Array.isArray(group.member_routes), `${group.group_id} must include member_routes`);
  assert.ok(Array.isArray(group.lane_order), `${group.group_id} must include lane_order`);
  assert.ok(
    group.member_routes.length >= 2,
    `${group.group_id} must represent at least two shared services`,
  );
  assert.equal(
    new Set(group.member_routes.map(visualRouteIdFor)).size,
    group.member_routes.length,
    `${group.group_id} must count unique visual services only`,
  );
  for (const [variant, base] of VISUAL_ROUTE_NORMALIZATION) {
    assert.equal(
      group.member_routes.includes(variant) && group.member_routes.includes(base),
      false,
      `${group.group_id} must not count ${variant} separately from ${base}`,
    );
    assert.equal(
      group.lane_order.includes(variant) && group.lane_order.includes(base),
      false,
      `${group.group_id} lane order must not count ${variant} separately from ${base}`,
    );
  }
  assert.ok(
    group.lane_order.length <= group.member_routes.length,
    `${group.group_id} lane count must not exceed unique service count`,
  );
  assert.ok(
    ["high", "medium", "low"].includes(group.confidence),
    `${group.group_id} must include confidence`,
  );
  assert.ok(
    ["topology", "spatial", "manual"].includes(group.source),
    `${group.group_id} must include group source`,
  );
  assert.equal(groupById.has(group.group_id), false, `duplicate group_id ${group.group_id}`);
  groupById.set(group.group_id, group);
  groupFamilies.add(group.visual_family);
}

for (const visualFamily of REQUIRED_GROUP_FAMILIES) {
  assert.ok(groupFamilies.has(visualFamily), `${visualFamily} must have at least one group`);
}

const routes = new Set();
const rawRoutes = new Set();
const debugIds = new Set();
const laneSlotsByGroupRoute = new Map();
let nonzeroWithoutGroup = 0;
let tinyGroupSegments = 0;

for (const feature of visual.features) {
  const properties = feature.properties ?? {};
  const routeId = normalizeRouteId(properties.route_id ?? properties.display_route);
  routes.add(routeId);
  rawRoutes.add(routeId);
  for (const rawRouteId of properties.raw_route_ids ?? []) rawRoutes.add(normalizeRouteId(rawRouteId));

  assert.equal(feature.geometry?.type, "LineString", `${routeId} must render as LineString`);
  assert.ok(
    feature.geometry.coordinates.length >= 2,
    `${routeId} ${properties.debug_id ?? "(no debug id)"} must include at least two points`,
  );
  assert.ok(
    feature.geometry.coordinates.every(isValidCoordinate),
    `${routeId} ${properties.debug_id ?? "(no debug id)"} has invalid coordinates`,
  );

  assert.equal(properties.source, "group-corridors", `${routeId} source must be group-corridors`);
  assert.equal(
    properties.visual_route_id,
    visualRouteIdFor(routeId),
    `${routeId} must expose visual_route_id used by lane assignment`,
  );
  assert.ok(
    Array.isArray(properties.raw_route_ids) && properties.raw_route_ids.length >= 1,
    `${routeId} must preserve raw_route_ids lineage`,
  );
  assert.ok(
    Array.isArray(properties.raw_shape_ids) && properties.raw_shape_ids.length >= 1,
    `${routeId} must preserve raw_shape_ids lineage`,
  );
  assert.ok(properties.representative_shape_id, `${routeId} must include representative_shape_id`);
  assert.ok(
    Array.isArray(properties.visual_edge_ids) && properties.visual_edge_ids.length >= 1,
    `${routeId} must include local visual_edge_ids lineage`,
  );
  assert.equal(
    Number(properties.edge_count),
    properties.visual_edge_ids.length,
    `${routeId} edge_count must match visual_edge_ids length`,
  );
  assert.ok(
    Number.isInteger(Number(properties.edge_sequence)),
    `${routeId} must include a deterministic edge_sequence`,
  );
  assert.ok(
    ["high", "medium", "low"].includes(properties.edge_geometry_confidence),
    `${routeId} must include edge_geometry_confidence`,
  );
  assert.ok(
    Array.isArray(properties.representative_edge_geometry_sources) &&
      properties.representative_edge_geometry_sources.length >= 1,
    `${routeId} must include representative edge geometry source lineage`,
  );
  assert.ok(
    properties.representative_edge_geometry_source,
    `${routeId} must include representative_edge_geometry_source`,
  );
  assert.notEqual(
    properties.assignment_reason,
    undefined,
    `${routeId} must explain group/lane assignment reason`,
  );
  assert.ok(properties.debug_id, `${routeId} must include deterministic debug_id`);
  assert.equal(debugIds.has(properties.debug_id), false, `duplicate debug_id ${properties.debug_id}`);
  debugIds.add(properties.debug_id);
  assert.ok(properties.visual_family, `${routeId} must include visual_family`);
  assert.ok(properties.visual_branch_id, `${routeId} must include visual_branch_id`);
  assert.ok(Number.isInteger(Number(properties.group_sequence)), `${routeId} must include group_sequence`);
  assert.ok(properties.effective_render_key, `${routeId} must include effective_render_key`);
  assert.ok(Number.isFinite(Number(properties.feature_length_m)), `${routeId} must include feature_length_m`);
  assert.ok(
    ["solo", "group"].includes(properties.segment_kind),
    `${routeId} must use solo/group segment kind`,
  );

  for (const property of FORBIDDEN_PROPERTIES) {
    assert.equal(
      properties[property],
      undefined,
      `${routeId} must not carry old 4B/4C property ${property}`,
    );
  }

  const laneSlot = Number(properties.visual_lane_slot ?? 0);
  assert.ok(Number.isFinite(laneSlot), `${routeId} visual_lane_slot must be numeric`);
  if (Math.abs(laneSlot) > 1e-9 && !properties.group_id) nonzeroWithoutGroup += 1;

  if (properties.segment_kind === "solo") {
    assert.equal(laneSlot, 0, `${routeId} solo segment must recenter`);
    assert.equal(properties.group_id ?? null, null, `${routeId} solo segment must not have group_id`);
  } else {
    const group = groupById.get(properties.group_id);
    assert.ok(group, `${routeId} references unknown group ${properties.group_id}`);
    assert.ok(group.member_routes.includes(routeId), `${routeId} must be a member of ${properties.group_id}`);
    const routeLaneKey = `${properties.group_id}|${routeId}`;
    if (!laneSlotsByGroupRoute.has(routeLaneKey)) laneSlotsByGroupRoute.set(routeLaneKey, new Set());
    laneSlotsByGroupRoute.get(routeLaneKey).add(String(laneSlot));
    const lengthMeters = lineLengthMeters(feature.geometry.coordinates);
    if (lengthMeters < 50) tinyGroupSegments += 1;
  }
}

for (const routeId of EXPECTED_ROUTES) {
  assert.ok(rawRoutes.has(routeId), `expected route ${routeId} must remain present in raw lineage`);
}

assert.equal(nonzeroWithoutGroup, 0, "nonzero lane slots must only exist inside corridor groups");
assert.equal(tinyGroupSegments, 0, "group renderer must not generate tiny group fragments under 50m");

for (const [routeLaneKey, laneSlots] of laneSlotsByGroupRoute) {
  assert.equal(
    laneSlots.size,
    1,
    `${routeLaneKey} must use one lane for all shape/direction variants of the service`,
  );
}

// Perpendicular-shift helper assertions (Task 4 of the lane-baking plan).
const {
  bakeLaneOffsetIntoPolyline,
  LANE_WIDTH_METERS,
  TAPER_LENGTH_METERS,
} = await import("./build-corridor-groups.mjs");

assert.equal(LANE_WIDTH_METERS, 12, "LANE_WIDTH_METERS default must be 12");
assert.equal(TAPER_LENGTH_METERS, 30, "TAPER_LENGTH_METERS default must be 30");

const helperIdentityCoords = [
  [-74.0, 40.75],
  [-73.99, 40.75],
];
assert.deepEqual(
  bakeLaneOffsetIntoPolyline(helperIdentityCoords, 0, null),
  helperIdentityCoords,
  "bakeLaneOffsetIntoPolyline with offset=0 must return identity coords",
);

const helperEastGoingCoords = [
  [-74.0, 40.75],
  [-73.99, 40.75],
  [-73.98, 40.75],
];
const helperEastGoingShifted = bakeLaneOffsetIntoPolyline(
  helperEastGoingCoords,
  6,
  null,
);
for (const c of helperEastGoingShifted) {
  assert.ok(
    c[1] < 40.75,
    "east-travel right-of-travel offset must shift south (decreasing lat)",
  );
  const dyMeters = (40.75 - c[1]) * 111320;
  assert.ok(
    dyMeters > 4 && dyMeters < 8,
    `east-travel +6m offset should produce ~6m southward shift, got ${dyMeters}m`,
  );
}

const helperTaperCoords = [
  [-74.0, 40.75],
  [-73.999, 40.75],
  [-73.998, 40.75],
  [-73.997, 40.75],
  [-73.996, 40.75],
];
const helperTaperedShifted = bakeLaneOffsetIntoPolyline(
  helperTaperCoords,
  6,
  (fromStart, fromEnd, total) => fromEnd / total,
);
const helperDyAtStart = (40.75 - helperTaperedShifted[0][1]) * 111320;
const helperDyAtEnd =
  Math.abs(40.75 - helperTaperedShifted[helperTaperedShifted.length - 1][1]) *
  111320;
assert.ok(
  helperDyAtStart > 4,
  `taper start should have ~6m offset, got ${helperDyAtStart}m`,
);
assert.ok(
  helperDyAtEnd < 0.01,
  `taper end should have ~0m offset, got ${helperDyAtEnd}m`,
);

// Task 6 taper regression: grouped features (any corridor) have finite
// coordinates and ≥2 vertices after the taper-baked emit.
const taperVisualPath = resolve(publicDir, "subway-network.group-visual.geojson");
const taperVisual = JSON.parse(readFileSync(taperVisualPath, "utf8"));
const taperCorridorFeatures = taperVisual.features.filter(
  (f) => f.properties?.corridor_id != null,
);
assert.ok(
  taperCorridorFeatures.length > 0,
  "at least one feature must carry a manual corridor_id after the build",
);
for (const f of taperCorridorFeatures) {
  const coords = f.geometry.coordinates;
  assert.ok(
    coords.length >= 2,
    `${f.properties.corridor_id} ${f.properties.route_id} feature must have ≥2 coords`,
  );
  for (const [lng, lat] of coords) {
    assert.ok(
      Number.isFinite(lng) && Number.isFinite(lat),
      `${f.properties.corridor_id} ${f.properties.route_id} feature must have finite coords`,
    );
  }
}

// Regression: per-edge baking would produce "0m-distance" pinch points at
// every station along a shared-track stretch (4/5/6 on Lex Av is the
// canonical example). Post-merge baking should produce at most a handful
// of pinch points (one per merged-segment endpoint, not one per station).
const lexF4 = taperVisual.features.find(
  (f) =>
    f.properties.route_id === "4" &&
    f.geometry.coordinates.length > 200 &&
    f.properties.visual_lane_slot === -1,
);
const lexF6 = taperVisual.features.find(
  (f) =>
    f.properties.route_id === "6" &&
    f.geometry.coordinates.length > 200 &&
    f.properties.visual_lane_slot === 1,
);
if (lexF4 && lexF6) {
  const distM = (a, b) => {
    const dx = (a[0] - b[0]) * 84500;
    const dy = (a[1] - b[1]) * 111320;
    return Math.hypot(dx, dy);
  };
  const inOverlap = lexF4.geometry.coordinates.filter(
    (c) => c[1] > 40.71 && c[1] < 40.81,
  );
  let pinchCount = 0;
  for (const c4 of inOverlap) {
    let best = Infinity;
    for (const c6 of lexF6.geometry.coordinates) {
      const d = distM(c4, c6);
      if (d < best) best = d;
    }
    if (best < 1) pinchCount += 1;
  }
  // Allow up to 4 pinch points: at most one per merged-feature endpoint
  // touching the overlap region, with a small margin for floating-point
  // edge cases. The pre-fix value was 26.
  assert.ok(
    pinchCount <= 4,
    `route 4 vs route 6 in the Manhattan Lex Av overlap has ${pinchCount} 0m-distance vertex pairs; expected <= 4. This usually means bakeOffsetsOnMergedFeatures has regressed back into per-edge baking — see the design plan.`,
  );
}

// Canal St join regression: B and D each have two merged features that meet
// at [-73.993753, 40.718267] (the family-bundle ↔ 6av-orange-trunk
// boundary). Pre-fix, both features tapered to canonical at the join,
// producing a visible "kissing point". Post-fix, suppress-taper-at-same-
// slot-endpoint logic keeps the join at full perpendicular offset.
const canalJoin = [-73.993753, 40.718267];
const distFromCanal = (c) => {
  const dx = (c[0] - canalJoin[0]) * 84500;
  const dy = (c[1] - canalJoin[1]) * 111320;
  return Math.hypot(dx, dy);
};
for (const routeId of ["B", "D"]) {
  const expectedOffset =
    routeId === "B"
      ? 1.5 * LANE_WIDTH_METERS
      : 0.5 * LANE_WIDTH_METERS;
  const segments = taperVisual.features.filter(
    (f) =>
      f.properties.route_id === routeId &&
      f.properties.segment_kind === "group",
  );
  const touching = segments.filter((f) => {
    const first = f.geometry.coordinates[0];
    const last = f.geometry.coordinates.at(-1);
    return distFromCanal(first) < 12 || distFromCanal(last) < 12;
  });
  if (touching.length >= 2) {
    for (const f of touching) {
      const first = f.geometry.coordinates[0];
      const last = f.geometry.coordinates.at(-1);
      const startDist = distFromCanal(first);
      const endDist = distFromCanal(last);
      const checkEndpoint = (dist, label) => {
        if (dist < 12) {
          assert.ok(
            Math.abs(dist - expectedOffset) < 3,
            `route ${routeId} ${label} at Canal join is ${dist.toFixed(2)}m from canonical, expected ~${expectedOffset}m (the per-corridor-boundary kissing should be suppressed)`,
          );
        }
      };
      checkEndpoint(startDist, "start");
      checkEndpoint(endDist, "end");
    }
  }
}

console.log("corridor group checks passed", {
  groups: groups.groups.length,
  features: visual.features.length,
  routes: routes.size,
  groupFamilies: [...groupFamilies].sort(),
});
