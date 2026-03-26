import mapboxgl from "mapbox-gl";

// Layer IDs added by our app — must be excluded from HUD restyling
const APP_LAYER_IDS = new Set(["jr-pulse-lyr", "jr-beam-3d", "3d-buildings"]);

/**
 * Restyle the Mapbox base map to a dark tactical HUD aesthetic.
 * Hides fills, restyles roads/water/labels to faint cyan on pure black.
 * Skips any layers added by the app (route lines, buildings, particles, etc.).
 */
export function applyHudMapStyle(m: mapboxgl.Map) {
  const layers = m.getStyle().layers;
  if (!layers) return;

  for (const layer of layers) {
    if (APP_LAYER_IDS.has(layer.id)) continue;
    if (layer.id.startsWith("jr-")) continue;

    const id = layer.id;
    const type = layer.type;

    if (type === "background") {
      m.setPaintProperty(id, "background-color", "#050508");
      continue;
    }

    if (type === "fill") {
      m.setPaintProperty(id, "fill-opacity", 0);
      continue;
    }

    if (type === "fill-extrusion") {
      m.setPaintProperty(id, "fill-extrusion-opacity", 0);
      continue;
    }

    if (type === "raster") {
      m.setPaintProperty(id, "raster-opacity", 0);
      continue;
    }

    if (type === "line") {
      const idLower = id.toLowerCase();

      if (idLower.includes("water")) {
        m.setPaintProperty(id, "line-color", "rgba(0, 212, 255, 0.2)");
        m.setPaintProperty(id, "line-width", 1);
        m.setPaintProperty(id, "line-opacity", 1);
        continue;
      }

      if (
        idLower.includes("admin") ||
        idLower.includes("boundary") ||
        idLower.includes("borough") ||
        idLower.includes("neighborhood")
      ) {
        m.setPaintProperty(id, "line-color", "rgba(0, 212, 255, 0.08)");
        m.setPaintProperty(id, "line-width", 0.8);
        m.setPaintProperty(id, "line-dasharray", [4, 4]);
        m.setPaintProperty(id, "line-opacity", 1);
        continue;
      }

      if (
        idLower.includes("contour") ||
        idLower.includes("hillshade") ||
        idLower.includes("land") ||
        idLower.includes("terrain")
      ) {
        m.setPaintProperty(id, "line-color", "rgba(0, 212, 255, 0.06)");
        m.setPaintProperty(id, "line-width", 0.6);
        m.setPaintProperty(id, "line-opacity", 1);
        continue;
      }

      const isMajor =
        idLower.includes("motorway") ||
        idLower.includes("trunk") ||
        idLower.includes("primary") ||
        idLower.includes("secondary") ||
        idLower.includes("major") ||
        idLower.includes("highway");

      if (idLower.includes("case") || idLower.includes("casing")) {
        m.setPaintProperty(id, "line-opacity", 0);
        continue;
      }

      if (isMajor) {
        m.setPaintProperty(id, "line-color", "rgba(0, 212, 255, 0.35)");
        m.setPaintProperty(id, "line-width", 1);
        m.setPaintProperty(id, "line-opacity", 1);
      } else {
        m.setPaintProperty(id, "line-color", "rgba(0, 212, 255, 0.2)");
        m.setPaintProperty(id, "line-width", 0.5);
        m.setPaintProperty(id, "line-opacity", 1);
      }
      continue;
    }

    if (type === "symbol") {
      try {
        m.setPaintProperty(id, "text-color", "rgba(0, 212, 255, 0.2)");
        m.setPaintProperty(id, "text-halo-width", 0);
        m.setPaintProperty(id, "text-halo-color", "rgba(0, 0, 0, 0)");
        m.setLayoutProperty(id, "text-size", 10);
        m.setPaintProperty(id, "icon-opacity", 0);
      } catch {
        m.setLayoutProperty(id, "visibility", "none");
      }
      continue;
    }

    if (type === "hillshade") {
      m.setPaintProperty(id, "hillshade-exaggeration", 0.3);
      m.setPaintProperty(id, "hillshade-shadow-color", "#050508");
      m.setPaintProperty(id, "hillshade-highlight-color", "rgba(0, 212, 255, 0.04)");
      m.setPaintProperty(id, "hillshade-accent-color", "rgba(0, 212, 255, 0.03)");
      continue;
    }
  }
}
