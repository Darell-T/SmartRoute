import type maplibregl from "maplibre-gl";

// 3D buildings rendered as a NATIVE MapLibre fill-extrusion layer.
//
// Previously these were a deck.gl MVTLayer in the interleaved overlay, which
// (a) maintained a second tile pipeline fetched/tessellated independently of
// the basemap, and (b) re-rendered every frame in sync with every MapLibre
// repaint. Both showed up as pan jank (worst-frame ~34ms -> ~19ms with
// buildings off in profiling). MapLibre's fill-extrusion uploads tile
// geometry once and renders it in the basemap's own GL pass, so it is far
// cheaper during pan and over new tiles -- and it stays always-visible (no
// gesture flicker). Tiles are the same MapTiler "building" MVT source.

const BUILDINGS_SOURCE_ID = "sr-buildings-src";
export const BUILDINGS_LAYER_ID = "sr-buildings";

const HEIGHT_EXAGGERATION = 1.85;
const BUILDINGS_MIN_ZOOM = 12;

function mapTilerBuildingTiles() {
  const key = process.env.NEXT_PUBLIC_MAPTILER_API_KEY;
  if (!key) return null;
  return `https://api.maptiler.com/tiles/buildings/{z}/{x}/{y}.pbf?key=${encodeURIComponent(
    key,
  )}`;
}

// Top = exaggerated render_height (falls back to height, then a 24m default),
// floored at 8m so trivial footprints still read as blocks. Base = the
// building's min height (usually 0). Mirrors the old deck buildingHeight().
const HEIGHT_EXPR: maplibregl.ExpressionSpecification = [
  "max",
  8,
  [
    "*",
    HEIGHT_EXAGGERATION,
    ["coalesce", ["get", "render_height"], ["get", "height"], 24],
  ],
];

const BASE_EXPR: maplibregl.ExpressionSpecification = [
  "coalesce",
  ["get", "render_min_height"],
  ["get", "min_height"],
  0,
];

/**
 * Idempotently install the native buildings source + fill-extrusion layer.
 * Pass `beforeId` to slot the buildings below the subway lines (call again
 * with the subway glow layer id once it exists to re-order). No-op when the
 * MapTiler key is missing.
 */
export function ensureBuildingsLayer(map: maplibregl.Map, beforeId?: string) {
  const tileUrl = mapTilerBuildingTiles();
  if (!tileUrl) return;

  if (!map.getSource(BUILDINGS_SOURCE_ID)) {
    map.addSource(BUILDINGS_SOURCE_ID, {
      type: "vector",
      tiles: [tileUrl],
      minzoom: BUILDINGS_MIN_ZOOM,
      maxzoom: 15,
    });
  }

  if (!map.getLayer(BUILDINGS_LAYER_ID)) {
    map.addLayer(
      {
        id: BUILDINGS_LAYER_ID,
        type: "fill-extrusion",
        source: BUILDINGS_SOURCE_ID,
        "source-layer": "building",
        minzoom: BUILDINGS_MIN_ZOOM,
        paint: {
          // Gotham massing: dark slate-blue monoliths, clearly visible (the Dark
          // Knight skyline) with a subtle vertical gradient so towers read as 3D.
          // Still a notch under the transit lines, which draw above them.
          "fill-extrusion-color": "#1F2A3C",
          "fill-extrusion-height": HEIGHT_EXPR,
          "fill-extrusion-base": BASE_EXPR,
          "fill-extrusion-vertical-gradient": true,
          // Grow in across the appearance zoom so towers rise rather than pop.
          "fill-extrusion-opacity": [
            "interpolate",
            ["linear"],
            ["zoom"],
            12,
            0,
            13,
            0.55,
            15,
            0.78,
          ],
        },
      },
      beforeId,
    );
  } else if (beforeId && map.getLayer(beforeId)) {
    map.moveLayer(BUILDINGS_LAYER_ID, beforeId);
  }
}

function setBuildingsHidden(map: maplibregl.Map, hidden: boolean) {
  if (!map.getLayer(BUILDINGS_LAYER_ID)) return;
  const visibility = hidden ? "none" : "visible";
  if (map.getLayoutProperty(BUILDINGS_LAYER_ID, "visibility") !== visibility) {
    map.setLayoutProperty(BUILDINGS_LAYER_ID, "visibility", visibility);
  }
}
