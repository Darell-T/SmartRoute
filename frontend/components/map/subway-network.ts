import type maplibregl from "maplibre-gl";

import {
  ENABLE_SUBWAY_LANE_SEPARATION,
  normalizeSubwayRouteId,
  prepareSubwayNetworkForLaneSeparation,
  SUBWAY_ROUTE_FAMILY,
  type SubwayLaneRenderMode,
} from "./subway-lane-separation";

const SOURCE_ID = "sr-subway-network";
const SHADOW_LAYER_ID = "sr-subway-network-shadow";
const CASING_LAYER_ID = "sr-subway-network-casing";
const GLOW_LAYER_ID = "sr-subway-network-glow";
const LINE_LAYER_ID = "sr-subway-network-lines";
export const SUBWAY_NETWORK_LINE_LAYER_ID = LINE_LAYER_ID;
const IDENTITY_ANCHOR_SOURCE_ID = "sr-subway-route-identity-anchors";
const IDENTITY_ANCHOR_LAYER_ID = "sr-subway-route-identity-anchors-core";
const IDENTITY_ANCHOR_INTERVAL_LAYER_ID = "sr-subway-route-identity-anchors-interval";
const FOCUS_IDENTITY_ANCHOR_LAYER_ID = "sr-subway-route-identity-anchors-focus";
const GROUP_ENDPOINT_SOURCE_ID = "sr-subway-group-endpoints";
const GROUP_ENDPOINT_START_LAYER_ID = "sr-subway-group-endpoints-start";
const GROUP_ENDPOINT_END_LAYER_ID = "sr-subway-group-endpoints-end";

const STOPS_SOURCE_ID = "sr-subway-stops";
const STOPS_DOT_LAYER_ID = "sr-subway-stops-dots";
const STOPS_HUB_LAYER_ID = "sr-subway-stops-dots-hubs";
const STOPS_LABEL_LAYER_ID = "sr-subway-stops-labels";

export const FIRST_SUBWAY_NETWORK_LAYER_ID = SHADOW_LAYER_ID;

type SubwayVisualLayerRole = "shadow" | "casing" | "glow" | "line";

const NO_FOCUSED_ROUTE_ID = "__sr-no-focused-route__";
export type SubwayNetworkFocusInput =
  | Iterable<unknown>
  | boolean
  | {
      selectedRouteIds?: Iterable<unknown> | unknown | null;
      incidentRouteIds?: Iterable<unknown> | unknown | null;
      nearbyRouteIds?: Iterable<unknown> | unknown | null;
    };

export type SubwayNetworkFocusState = {
  selectedRouteIds: string[];
  incidentRouteIds: string[];
  nearbyRouteIds: string[];
  sameFamilySiblingRouteIds: string[];
  allEmphasisRouteIds: string[];
};

const EMPTY_SUBWAY_FOCUS_STATE: SubwayNetworkFocusState = {
  selectedRouteIds: [],
  incidentRouteIds: [],
  nearbyRouteIds: [],
  sameFamilySiblingRouteIds: [],
  allEmphasisRouteIds: [],
};

const subwayFocusState = new WeakMap<maplibregl.Map, SubwayNetworkFocusState>();

const LINE_OPACITY: Record<
  SubwayVisualLayerRole,
  {
    idle: number;
    selected: number;
    incident: number;
    nearby: number;
    sibling: number;
    background: number;
  }
> = {
  shadow: {
    idle: 0.18,
    selected: 0.42,
    incident: 0.34,
    nearby: 0.26,
    sibling: 0.14,
    background: 0.06,
  },
  casing: {
    idle: 0.46,
    selected: 0.86,
    incident: 0.76,
    nearby: 0.64,
    sibling: 0.44,
    background: 0.2,
  },
  glow: {
    idle: 0.032,
    selected: 0.18,
    incident: 0.12,
    nearby: 0.075,
    sibling: 0.035,
    background: 0.006,
  },
  line: {
    idle: 0.52,
    selected: 0.96,
    incident: 0.9,
    nearby: 0.78,
    sibling: 0.52,
    background: 0.24,
  },
};

const LINE_WIDTH: Record<
  SubwayVisualLayerRole,
  {
    idle: [number, number, number];
    focused: [number, number, number];
    muted: [number, number, number];
  }
> = {
  shadow: {
    idle: [3.8, 6.8, 10.8],
    focused: [5.1, 8.8, 13],
    muted: [2.8, 5.3, 8.2],
  },
  casing: {
    idle: [2.3, 4.2, 6.6],
    focused: [3.2, 5.5, 8.2],
    muted: [1.7, 3.1, 4.9],
  },
  glow: {
    idle: [2.8, 4.8, 7.2],
    focused: [4.8, 7.8, 10.5],
    muted: [1.6, 2.8, 4.4],
  },
  line: {
    idle: [1.62, 2.68, 4.4],
    focused: [2.45, 3.85, 6.15],
    muted: [1.1, 1.9, 3.2],
  },
};

// Routes whose bullet SVGs differ from the lowercase route_id.
const BULLET_OVERRIDES: Record<string, string> = {
  "6X": "6d",
  "7X": "7d",
  FX: "fd",
  FS: "sf",
  SI: "sir",
  SIR: "sir",
  GS: "s",
  H: "s",
};

const ALL_ROUTES = [
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "6d",
  "7",
  "7d",
  "a",
  "b",
  "c",
  "d",
  "e",
  "f",
  "fd",
  "g",
  "h",
  "j",
  "l",
  "m",
  "n",
  "q",
  "r",
  "s",
  "sf",
  "sir",
  "sr",
  "t",
  "w",
  "z",
];

function addUniqueRouteId(routeIds: string[], seen: Set<string>, routeId: string) {
  if (!routeId || seen.has(routeId)) return;
  seen.add(routeId);
  routeIds.push(routeId);
}

function focusRouteAliases(routeId: string) {
  switch (routeId) {
    case "6X":
      return ["6X", "6D"];
    case "7X":
      return ["7X", "7D"];
    case "F":
      return ["F", "FX", "FD"];
    case "S":
      return ["S", "FS", "GS", "H"];
    case "SI":
      return ["SI", "SIR"];
    default:
      return [routeId];
  }
}

function iterableValues(value: Iterable<unknown> | unknown | null | undefined) {
  if (value == null || typeof value === "boolean") return [];
  if (typeof value === "string") return [value];
  if (typeof value === "object" && Symbol.iterator in value) {
    return Array.from(value as Iterable<unknown>);
  }
  return [value];
}

export function normalizeSubwayFocusRouteIds(
  routeIds?: Iterable<unknown> | unknown | null,
) {
  const normalized: string[] = [];
  const seen = new Set<string>();

  for (const routeId of iterableValues(routeIds)) {
    const canonicalRouteId = normalizeSubwayRouteId(routeId);
    if (!canonicalRouteId) continue;
    for (const alias of focusRouteAliases(canonicalRouteId)) {
      addUniqueRouteId(normalized, seen, alias);
    }
  }

  return normalized;
}

function isStructuredFocusInput(
  input: SubwayNetworkFocusInput | null | undefined,
): input is Exclude<SubwayNetworkFocusInput, Iterable<unknown> | boolean> {
  if (!input || typeof input !== "object") return false;
  return (
    !Array.isArray(input) &&
    !(Symbol.iterator in input) &&
    (
      "selectedRouteIds" in input ||
      "incidentRouteIds" in input ||
      "nearbyRouteIds" in input
    )
  );
}

function normalizedRouteSet(routeIds: readonly string[]) {
  const set = new Set<string>();
  for (const routeId of routeIds) {
    const canonicalRouteId = normalizeSubwayRouteId(routeId);
    if (canonicalRouteId) set.add(canonicalRouteId);
  }
  return set;
}

function selectedFamilySiblings(selectedRouteIds: readonly string[]) {
  const selected = normalizedRouteSet(selectedRouteIds);
  const families = new Set<string>();
  for (const routeId of selected) {
    const family = SUBWAY_ROUTE_FAMILY[routeId];
    if (family) families.add(family);
  }

  const siblings: string[] = [];
  const seen = new Set<string>();

  for (const [routeId, family] of Object.entries(SUBWAY_ROUTE_FAMILY)) {
    if (!families.has(family) || selected.has(routeId)) continue;
    addUniqueRouteId(siblings, seen, routeId);
  }

  return siblings;
}

function addRouteBucket(
  target: string[],
  seen: Set<string>,
  routeIds: readonly string[],
) {
  for (const routeId of routeIds) {
    const canonicalRouteId = normalizeSubwayRouteId(routeId);
    if (!canonicalRouteId || seen.has(canonicalRouteId)) continue;
    seen.add(canonicalRouteId);
    target.push(canonicalRouteId);
  }
}

export function normalizeSubwayNetworkFocusState(
  input: SubwayNetworkFocusInput | null | undefined = [],
): SubwayNetworkFocusState {
  if (typeof input === "boolean") return EMPTY_SUBWAY_FOCUS_STATE;

  const selectedRouteIds = isStructuredFocusInput(input)
    ? normalizeSubwayFocusRouteIds(input.selectedRouteIds)
    : normalizeSubwayFocusRouteIds(input);
  const incidentRouteIds = isStructuredFocusInput(input)
    ? normalizeSubwayFocusRouteIds(input.incidentRouteIds)
    : [];
  const nearbyRouteIds = isStructuredFocusInput(input)
    ? normalizeSubwayFocusRouteIds(input.nearbyRouteIds)
    : [];
  const sameFamilySiblingRouteIds = selectedFamilySiblings(selectedRouteIds);
  const allEmphasisRouteIds: string[] = [];
  const seen = new Set<string>();

  addRouteBucket(allEmphasisRouteIds, seen, selectedRouteIds);
  addRouteBucket(allEmphasisRouteIds, seen, incidentRouteIds);
  addRouteBucket(allEmphasisRouteIds, seen, nearbyRouteIds);

  return {
    selectedRouteIds,
    incidentRouteIds,
    nearbyRouteIds,
    sameFamilySiblingRouteIds,
    allEmphasisRouteIds,
  };
}

function subwayRouteFocusCondition(routeIds: readonly string[]) {
  const normalized = normalizeSubwayFocusRouteIds(routeIds);
  if (normalized.length === 0) return false;
  return ["match", ["get", "route_id"], normalized, true, false];
}

export function subwayRouteFocusFilter(
  routeIds: readonly string[],
): maplibregl.FilterSpecification {
  const normalized = normalizeSubwayFocusRouteIds(routeIds);
  if (normalized.length === 0) {
    return [
      "==",
      ["get", "route_id"],
      NO_FOCUSED_ROUTE_ID,
    ] as maplibregl.FilterSpecification;
  }
  return [
    "match",
    ["get", "route_id"],
    normalized,
    true,
    false,
  ] as maplibregl.FilterSpecification;
}

function hasRouteFocus(focusState: SubwayNetworkFocusState) {
  return (
    focusState.selectedRouteIds.length > 0 ||
    focusState.incidentRouteIds.length > 0 ||
    focusState.nearbyRouteIds.length > 0
  );
}

function relevancePaintValue(
  focusInput: SubwayNetworkFocusInput,
  values: {
    idle: number;
    selected: number;
    incident: number;
    nearby: number;
    sibling: number;
    background: number;
  },
) {
  const focusState = normalizeSubwayNetworkFocusState(focusInput);
  if (!hasRouteFocus(focusState)) return values.idle;

  return [
    "case",
    subwayRouteFocusCondition(focusState.selectedRouteIds),
    values.selected,
    subwayRouteFocusCondition(focusState.incidentRouteIds),
    values.incident,
    subwayRouteFocusCondition(focusState.nearbyRouteIds),
    values.nearby,
    subwayRouteFocusCondition(focusState.sameFamilySiblingRouteIds),
    values.sibling,
    values.background,
  ] as unknown as number;
}

export function subwayFocusedLineOpacityExpression(
  routeIds: SubwayNetworkFocusInput,
  role: SubwayVisualLayerRole,
) {
  const opacity = LINE_OPACITY[role];
  return relevancePaintValue(routeIds, opacity);
}

export function subwayFocusedLineWidthExpression(
  routeIds: SubwayNetworkFocusInput,
  role: SubwayVisualLayerRole,
) {
  const widths = LINE_WIDTH[role];
  const focusState = normalizeSubwayNetworkFocusState(routeIds);

  if (!hasRouteFocus(focusState)) {
    return [
      "interpolate",
      ["linear"],
      ["zoom"],
      9,
      widths.idle[0],
      12,
      widths.idle[1],
      16,
      widths.idle[2],
    ] as unknown as number;
  }

  const selectedCondition = subwayRouteFocusCondition(focusState.selectedRouteIds);
  const incidentCondition = subwayRouteFocusCondition(focusState.incidentRouteIds);
  const nearbyCondition = subwayRouteFocusCondition(focusState.nearbyRouteIds);
  const siblingCondition = subwayRouteFocusCondition(
    focusState.sameFamilySiblingRouteIds,
  );

  return [
    "interpolate",
    ["linear"],
    ["zoom"],
    9,
    [
      "case",
      selectedCondition,
      widths.focused[0],
      incidentCondition,
      widths.focused[0],
      nearbyCondition,
      widths.idle[0],
      siblingCondition,
      widths.idle[0],
      widths.muted[0],
    ],
    12,
    [
      "case",
      selectedCondition,
      widths.focused[1],
      incidentCondition,
      widths.focused[1],
      nearbyCondition,
      widths.idle[1],
      siblingCondition,
      widths.idle[1],
      widths.muted[1],
    ],
    16,
    [
      "case",
      selectedCondition,
      widths.focused[2],
      incidentCondition,
      widths.focused[2],
      nearbyCondition,
      widths.idle[2],
      siblingCondition,
      widths.idle[2],
      widths.muted[2],
    ],
  ] as unknown as number;
}

export function subwayBulletOpacityExpression(routeIds: SubwayNetworkFocusInput) {
  const focusState = normalizeSubwayNetworkFocusState(routeIds);
  if (!hasRouteFocus(focusState)) {
    return [
      "interpolate",
      ["linear"],
      ["zoom"],
      12.5,
      0.18,
      14,
      0.38,
      16,
      0.54,
    ] as unknown as number;
  }

  return [
    "case",
    subwayRouteFocusCondition(focusState.selectedRouteIds),
    0.24,
    subwayRouteFocusCondition(focusState.incidentRouteIds),
    0.42,
    subwayRouteFocusCondition(focusState.nearbyRouteIds),
    0.32,
    subwayRouteFocusCondition(focusState.sameFamilySiblingRouteIds),
    0.22,
    0.08,
  ] as unknown as number;
}

function focusedBulletLayerOpacityExpression() {
  return [
    "interpolate",
    ["linear"],
    ["zoom"],
    12.25,
    0.82,
    14,
    0.95,
    17,
    1,
  ] as unknown as number;
}

function firstSymbolLayerId(m: maplibregl.Map) {
  return m.getStyle().layers?.find((layer) => layer.type === "symbol")?.id;
}

function networkDebugEnabled() {
  if (process.env.NEXT_PUBLIC_NETWORK_DEBUG === "on") return true;
  if (typeof window !== "undefined") {
    return new URLSearchParams(window.location.search).get("network-debug") === "1";
  }
  return false;
}

/**
 * The build script bakes lane offsets into geometry coordinates as
 * perpendicular shifts in lat/lng (see frontend/scripts/build-corridor-groups.mjs
 * `bakeLaneOffsetIntoPolyline`). Runtime line-offset must therefore be 0 —
 * any non-zero value would double-shift the line, producing 2x the intended
 * spacing.
 *
 * The legacy zoom-interpolated expression remains in git history if a
 * fallback to runtime offsets is ever needed.
 *
 * `subwayLineSortKeyExpression()` below still uses `visual_z_order` so
 * cross-route stacking inside a bundle behaves correctly.
 */
export function subwayLaneOffsetExpression(): number {
  return 0;
}

function subwayLineSortKeyExpression(): number {
  if (!ENABLE_SUBWAY_LANE_SEPARATION) return 0;
  return ["coalesce", ["get", "visual_z_order"], 0] as unknown as number;
}

function subwayLineLayout() {
  return {
    "line-cap": "round" as const,
    "line-join": "round" as const,
    "line-sort-key": subwayLineSortKeyExpression(),
  };
}

// Render an SVG into a raster the Mapbox icon atlas can consume.
// Mapbox addImage cannot ingest SVG <img> directly in a portable way,
// so we draw to canvas at 2× and register at pixelRatio 2.
function loadBulletImage(
  m: maplibregl.Map,
  name: string,
  src: string,
  size = 64,
) {
  return new Promise<void>((resolve) => {
    if (m.hasImage(name)) return resolve();
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.drawImage(img, 0, 0, size, size);
          const data = ctx.getImageData(0, 0, size, size);
          if (!m.hasImage(name)) m.addImage(name, data, { pixelRatio: 2 });
        }
      } catch {
        /* swallow — bullet just won't render for this route */
      }
      resolve();
    };
    img.onerror = () => resolve();
    img.src = src;
  });
}

async function ensureBulletImages(m: maplibregl.Map) {
  await Promise.all(
    ALL_ROUTES.map((slug) =>
      loadBulletImage(m, `mta-${slug}`, `/mta-bullets/${slug}.svg`),
    ),
  );
}

function routeIconImageExpression() {
  return [
    "match",
    ["get", "route_id"],
    ...Object.entries(BULLET_OVERRIDES).flatMap(([k, v]) => [
      k,
      `mta-${v}`,
    ]),
    ["concat", "mta-", ["downcase", ["get", "route_id"]]],
  ];
}

function addRouteIdentityLayer(
  m: maplibregl.Map,
  layerId: string,
  anchorKindFilter: maplibregl.FilterSpecification,
  minzoom: number,
  beforeId?: string,
) {
  if (!m.getSource(IDENTITY_ANCHOR_SOURCE_ID) || m.getLayer(layerId)) return;

  m.addLayer(
    {
      id: layerId,
      type: "symbol",
      source: IDENTITY_ANCHOR_SOURCE_ID,
      // Bullets join the map only when the rider zooms in to borough-inspection
      // range. Wider than this (city-wide) the colored polylines carry line
      // identity on their own, and the live train markers already wear the
      // route bullet — doubling up reads as clutter.
      minzoom,
      filter: anchorKindFilter,
      layout: {
        "symbol-placement": "point",
        // Wide spacing so each bullet is a landmark along a route rather than a
        // beaded chain. Mapbox's collision engine still thins overlapping
        // placements on top of this baseline.
        "icon-image": routeIconImageExpression(),
        "icon-size": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.75,
          0.28,
          15,
          0.39,
          17,
          0.48,
        ],
        "icon-allow-overlap": false,
        "icon-ignore-placement": false,
        "icon-padding": 10,
        "icon-rotation-alignment": "viewport",
        "icon-pitch-alignment": "viewport",
        "symbol-sort-key": ["coalesce", ["get", "priority"], 3],
      },
      paint: {
        "icon-opacity": subwayBulletOpacityExpression([]),
        "icon-halo-color": "#0a0d13",
        "icon-halo-width": 1.2,
      },
    } as unknown as maplibregl.AddLayerObject,
    beforeId,
  );
}

function addFocusRouteIdentityLayer(m: maplibregl.Map, beforeId?: string) {
  if (
    !m.getSource(IDENTITY_ANCHOR_SOURCE_ID) ||
    m.getLayer(FOCUS_IDENTITY_ANCHOR_LAYER_ID)
  ) {
    return;
  }

  m.addLayer(
    {
      id: FOCUS_IDENTITY_ANCHOR_LAYER_ID,
      type: "symbol",
      source: IDENTITY_ANCHOR_SOURCE_ID,
      minzoom: 12,
      filter: subwayRouteFocusFilter(
        (subwayFocusState.get(m) ?? EMPTY_SUBWAY_FOCUS_STATE).selectedRouteIds,
      ),
      layout: {
        "symbol-placement": "point",
        "icon-image": routeIconImageExpression(),
        "icon-size": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12,
          0.4,
          15,
          0.6,
          17,
          0.72,
        ],
        "icon-allow-overlap": false,
        "icon-ignore-placement": false,
        "icon-padding": 8,
        "icon-rotation-alignment": "viewport",
        "icon-pitch-alignment": "viewport",
        "symbol-sort-key": ["coalesce", ["get", "priority"], 3],
      },
      paint: {
        "icon-opacity": focusedBulletLayerOpacityExpression(),
        "icon-halo-color": "#07090d",
        "icon-halo-width": 1.6,
      },
    } as unknown as maplibregl.AddLayerObject,
    beforeId,
  );
}

// Apple Maps transit-mode-inspired stack tuned for the dusk basemap:
//
//   shadow   — wide, soft black puck beneath the line for lift/edge
//              separation. Prevents the line from blending into roads.
//   casing   — tight near-black outline that reads as a crisp boundary
//              without the "doubled color" muddiness of a same-hue casing.
//   line     — full MTA color at the original thickness, with a small
//              line-emissive-strength so it stays legible without blowing
//              out on dusk (full emissive looked neon on the lighter map).
//   bullets  — precomputed point anchors, collision-pruned by Mapbox so
//              labels stay attached to useful positions without beading every
//              line segment.
export function addSubwayNetwork(m: maplibregl.Map) {
  if (m.getSource(SOURCE_ID)) return;

  m.addSource(SOURCE_ID, {
    type: "geojson",
    data: "/subway-network.canonical.geojson",
  });
  m.addSource(IDENTITY_ANCHOR_SOURCE_ID, {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
  });
  if (networkDebugEnabled()) {
    m.addSource(GROUP_ENDPOINT_SOURCE_ID, {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
  }

  const beforeId = firstSymbolLayerId(m);

  m.addLayer(
    {
      id: SHADOW_LAYER_ID,
      type: "line",
      source: SOURCE_ID,
      paint: {
        "line-color": "#000000",
        "line-opacity": subwayFocusedLineOpacityExpression([], "shadow"),
        "line-offset": subwayLaneOffsetExpression(),
        "line-width": subwayFocusedLineWidthExpression([], "shadow"),
        "line-blur": 3,
      },
      layout: subwayLineLayout(),
    },
    beforeId,
  );

  m.addLayer(
    {
      id: CASING_LAYER_ID,
      type: "line",
      source: SOURCE_ID,
      paint: {
        "line-color": "#0a0d13",
        "line-opacity": subwayFocusedLineOpacityExpression([], "casing"),
        "line-offset": subwayLaneOffsetExpression(),
        "line-width": subwayFocusedLineWidthExpression([], "casing"),
      },
      layout: subwayLineLayout(),
    },
    beforeId,
  );

  m.addLayer(
    {
      id: GLOW_LAYER_ID,
      type: "line",
      source: SOURCE_ID,
      paint: {
        "line-color": ["get", "color"],
        "line-opacity": subwayFocusedLineOpacityExpression([], "glow"),
        "line-offset": subwayLaneOffsetExpression(),
        "line-width": subwayFocusedLineWidthExpression([], "glow"),
        "line-blur": 3,
      },
      layout: subwayLineLayout(),
    },
    beforeId,
  );

  m.addLayer(
    {
      id: LINE_LAYER_ID,
      type: "line",
      source: SOURCE_ID,
      paint: {
        "line-color": ["get", "color"],
        "line-offset": subwayLaneOffsetExpression(),
        "line-opacity": subwayFocusedLineOpacityExpression([], "line"),
        "line-width": subwayFocusedLineWidthExpression([], "line"),
      },
      layout: subwayLineLayout(),
    },
    beforeId,
  );

  if (networkDebugEnabled() && m.getSource(GROUP_ENDPOINT_SOURCE_ID)) {
    m.addLayer(
      {
        id: GROUP_ENDPOINT_START_LAYER_ID,
        type: "circle",
        source: GROUP_ENDPOINT_SOURCE_ID,
        minzoom: 14,
        filter: ["==", ["get", "endpoint_kind"], "start"],
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            14,
            2.2,
            17,
            4,
          ],
          "circle-color": "#37d67a",
          "circle-stroke-color": "#07100b",
          "circle-stroke-width": 1.2,
          "circle-opacity": 0.86,
        },
      } as unknown as maplibregl.AddLayerObject,
      beforeId,
    );
    m.addLayer(
      {
        id: GROUP_ENDPOINT_END_LAYER_ID,
        type: "circle",
        source: GROUP_ENDPOINT_SOURCE_ID,
        minzoom: 14,
        filter: ["==", ["get", "endpoint_kind"], "end"],
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            14,
            2.2,
            17,
            4,
          ],
          "circle-color": "#ffbc42",
          "circle-stroke-color": "#140d04",
          "circle-stroke-width": 1.2,
          "circle-opacity": 0.86,
        },
      } as unknown as maplibregl.AddLayerObject,
      beforeId,
    );
  }

  void ensureBulletImages(m).then(() => {
    try {
      addRouteIdentityLayer(
        m,
        IDENTITY_ANCHOR_LAYER_ID,
        ["!=", ["get", "anchor_kind"], "interval"] as maplibregl.FilterSpecification,
        12.75,
        beforeId,
      );
      addRouteIdentityLayer(
        m,
        IDENTITY_ANCHOR_INTERVAL_LAYER_ID,
        ["==", ["get", "anchor_kind"], "interval"] as maplibregl.FilterSpecification,
        14.75,
        beforeId,
      );
      addFocusRouteIdentityLayer(m, beforeId);
      setSubwayNetworkFocus(
        m,
        subwayFocusState.get(m) ?? EMPTY_SUBWAY_FOCUS_STATE,
      );
    } catch (error) {
      console.warn("[subway-network] failed to add route identity layer", error);
    }
  });
}

// ---------------------------------------------------------------------------
// Stops layer — Apple-Maps-style station dots fed from the backend GTFS data.
// Stops are added as a dedicated GeoJSON source so we can refresh independently
// of the full network. Two layers: a circle for the dot and a symbol for the
// station label that only kicks in at very close zoom.
// ---------------------------------------------------------------------------

export function addSubwayStops(m: maplibregl.Map) {
  if (m.getSource(STOPS_SOURCE_ID)) return;

  m.addSource(STOPS_SOURCE_ID, {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
  });

  const beforeId = firstSymbolLayerId(m);

  m.addLayer(
    {
      id: STOPS_DOT_LAYER_ID,
      type: "circle",
      source: STOPS_SOURCE_ID,
      minzoom: 12.5,
      paint: {
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.5,
          2.4,
          14,
          3.4,
          16,
          4.6,
          18,
          6.2,
        ],
        "circle-color": "#ffffff",
        "circle-stroke-color": "#0a0d13",
        "circle-stroke-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.5,
          1.5,
          16,
          2.2,
        ],      },
    },
    beforeId,
  );

  // Transfer hubs (3+ routes) get a second, larger puck drawn above the base
  // dots. Stations like Fulton, Atlantic-Barclays, and Times Sq visibly
  // outrank single-line stops at a glance without a separate data payload.
  m.addLayer(
    {
      id: STOPS_HUB_LAYER_ID,
      type: "circle",
      source: STOPS_SOURCE_ID,
      minzoom: 12.5,
      filter: [">=", ["length", ["get", "route_ids"]], 3],
      paint: {
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.5,
          3,
          14,
          4.1,
          16,
          5.6,
          18,
          7.4,
        ],
        "circle-color": "#ffffff",
        "circle-stroke-color": "#0a0d13",
        "circle-stroke-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          12.5,
          2,
          16,
          2.8,
        ],      },
    },
    beforeId,
  );

  // Two label layers, mirroring Apple Maps' progressive disclosure:
  //   priority labels — major transfer hubs (3+ routes) start emerging at
  //                     zoom 12.5 so the city view always names the big nodes
  //                     riders use to orient.
  //   all labels      — every remaining station name kicks in at zoom 13.5,
  //                     letting Mapbox's collision detection thin the dense
  //                     midtown cluster while keeping outer-borough names.
  //
  // text-variable-anchor lets each label flip to whichever side of its dot
  // has space, which roughly doubles how many names survive collisions vs
  // a fixed top anchor. symbol-sort-key (negated route count) is a hint to
  // Mapbox to keep the busier station when two collide.

  const sharedLabelLayout = {
    "text-field": ["get", "name"],
    "text-font": ["Open Sans Semibold", "Arial Unicode MS Bold"],
    "text-variable-anchor": ["top", "bottom", "left", "right"],
    "text-radial-offset": 0.85,
    "text-justify": "auto",
    "text-allow-overlap": false,
    "text-optional": true,
    "text-padding": 2,
    "text-max-width": 9,
    "symbol-sort-key": ["*", -1, ["length", ["get", "route_ids"]]],
  };

  const sharedLabelPaint = {
    "text-color": "rgba(244,245,247,0.94)",
    "text-halo-color": "rgba(7,9,13,0.95)",
    "text-halo-width": 1.4,
  };

  m.addLayer({
    id: `${STOPS_LABEL_LAYER_ID}-priority`,
    type: "symbol",
    source: STOPS_SOURCE_ID,
    minzoom: 12.5,
    filter: [">=", ["length", ["get", "route_ids"]], 3],
    layout: {
      ...sharedLabelLayout,
      "text-size": [
        "interpolate",
        ["linear"],
        ["zoom"],
        12.5,
        9.5,
        14,
        10.5,
        16,
        11.5,
        18,
        13,
      ],
    },
    paint: sharedLabelPaint,
  } as unknown as maplibregl.AddLayerObject);

  m.addLayer({
    id: STOPS_LABEL_LAYER_ID,
    type: "symbol",
    source: STOPS_SOURCE_ID,
    minzoom: 13.5,
    layout: {
      ...sharedLabelLayout,
      "text-size": [
        "interpolate",
        ["linear"],
        ["zoom"],
        13.5,
        9,
        15,
        10.5,
        17,
        12,
        19,
        13.5,
      ],
    },
    paint: sharedLabelPaint,
  } as unknown as maplibregl.AddLayerObject);
}

export function setSubwayStopsData(
  m: maplibregl.Map,
  data: GeoJSON.FeatureCollection<
    GeoJSON.Point,
    { name: string; route_ids: string[] }
  >,
) {
  const src = m.getSource(STOPS_SOURCE_ID) as
    | maplibregl.GeoJSONSource
    | undefined;
  if (src) src.setData(data);
}

export function setSubwayNetworkData(
  m: maplibregl.Map,
  data: GeoJSON.FeatureCollection,
  mode?: SubwayLaneRenderMode,
) {
  const src = m.getSource(SOURCE_ID) as
    | maplibregl.GeoJSONSource
    | undefined;
  if (src) src.setData(prepareSubwayNetworkForLaneSeparation(data, { mode }));
}

export function setSubwayRouteIdentityData(
  m: maplibregl.Map,
  data: GeoJSON.FeatureCollection,
) {
  const src = m.getSource(IDENTITY_ANCHOR_SOURCE_ID) as
    | maplibregl.GeoJSONSource
    | undefined;
  if (src) src.setData(data);
}

export function setSubwayGroupEndpointData(
  m: maplibregl.Map,
  data: GeoJSON.FeatureCollection,
) {
  const src = m.getSource(GROUP_ENDPOINT_SOURCE_ID) as
    | maplibregl.GeoJSONSource
    | undefined;
  if (src) src.setData(data);
}

function setLinePaint(
  m: maplibregl.Map,
  layerId: string,
  role: SubwayVisualLayerRole,
  focusState: SubwayNetworkFocusState,
) {
  if (!m.getLayer(layerId)) return;
  m.setPaintProperty(
    layerId,
    "line-opacity",
    subwayFocusedLineOpacityExpression(focusState, role),
  );
  m.setPaintProperty(
    layerId,
    "line-width",
    subwayFocusedLineWidthExpression(focusState, role),
  );
}

export function setSubwayNetworkFocus(
  m: maplibregl.Map,
  routeIdsOrHasRoute: SubwayNetworkFocusInput = [],
) {
  const focusState = normalizeSubwayNetworkFocusState(routeIdsOrHasRoute);

  subwayFocusState.set(m, focusState);

  setLinePaint(m, SHADOW_LAYER_ID, "shadow", focusState);
  setLinePaint(m, CASING_LAYER_ID, "casing", focusState);
  setLinePaint(m, GLOW_LAYER_ID, "glow", focusState);
  setLinePaint(m, LINE_LAYER_ID, "line", focusState);

  for (const layerId of [IDENTITY_ANCHOR_LAYER_ID, IDENTITY_ANCHOR_INTERVAL_LAYER_ID]) {
    if (!m.getLayer(layerId)) continue;
    m.setPaintProperty(
      layerId,
      "icon-opacity",
      subwayBulletOpacityExpression(focusState),
    );
  }

  if (m.getLayer(FOCUS_IDENTITY_ANCHOR_LAYER_ID)) {
    m.setFilter(
      FOCUS_IDENTITY_ANCHOR_LAYER_ID,
      subwayRouteFocusFilter(focusState.selectedRouteIds),
    );
  }
}
