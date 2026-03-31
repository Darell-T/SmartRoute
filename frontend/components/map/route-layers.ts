import mapboxgl from "mapbox-gl";
import polyline from "@mapbox/polyline";
import type { RouteStep } from "@/types";

const SRC_PARTICLES = "jr-pulse-src";
const LYR_PARTICLES = "jr-pulse-lyr";

const MTA_COLORS: Record<string, string> = {
  A: "#0039A6", C: "#0039A6", E: "#0039A6",
  B: "#FF6319", D: "#FF6319", F: "#FF6319", M: "#FF6319",
  G: "#6CBE45",
  J: "#996633", Z: "#996633",
  L: "#A7A9AC",
  N: "#FCCC0A", Q: "#FCCC0A", R: "#FCCC0A", W: "#FCCC0A",
  "1": "#EE352E", "2": "#EE352E", "3": "#EE352E",
  "4": "#00933C", "5": "#00933C", "6": "#00933C",
  "7": "#B933AD",
  S: "#808183",
  SI: "#00A9CE",
};

export function getLineColor(line: string): string {
  return MTA_COLORS[line.toUpperCase()] ?? "#FFD700";
}

/** Decode a Google-encoded polyline into [lng, lat] pairs for Mapbox */
export function decodePolyline(encoded: string): [number, number][] {
  const decoded = polyline.decode(encoded);
  return decoded.map(([lat, lng]: [number, number]) => [lng, lat]);
}

/** Get animation duration for a step type */
export function getStepDuration(type: string): number {
  switch (type) {
    case "WALK": return 1000;
    case "SUBWAY": return 2000;
    case "BUS": return 1500;
    default: return 1000;
  }
}

/** Ease-out cubic for smooth deceleration */
export function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

/**
 * Build a smooth sub-path of coordinates from index 0 up to a fractional
 * position along the line.
 */
export function subPath(coords: [number, number][], fraction: number): [number, number][] {
  if (coords.length < 2 || fraction <= 0) return [coords[0], coords[0]];
  if (fraction >= 1) return coords;

  const segLens: number[] = [];
  let total = 0;
  for (let i = 1; i < coords.length; i++) {
    const dx = coords[i][0] - coords[i - 1][0];
    const dy = coords[i][1] - coords[i - 1][1];
    const d = Math.sqrt(dx * dx + dy * dy);
    segLens.push(d);
    total += d;
  }
  if (total === 0) return [coords[0]];

  const targetDist = fraction * total;
  let traveled = 0;

  for (let i = 0; i < segLens.length; i++) {
    if (traveled + segLens[i] >= targetDist) {
      const t = (targetDist - traveled) / segLens[i];
      const interpPoint: [number, number] = [
        coords[i][0] + t * (coords[i + 1][0] - coords[i][0]),
        coords[i][1] + t * (coords[i + 1][1] - coords[i][1]),
      ];
      return [...coords.slice(0, i + 1), interpPoint];
    }
    traveled += segLens[i];
  }
  return coords;
}

/** Add a source + layer(s) for a single route step and return the source ID */
export function addStepLayers(
  m: mapboxgl.Map,
  step: RouteStep,
  index: number,
  dynamicLayerIds: string[],
  dynamicSourceIds: string[],
): { sourceId: string; coords: [number, number][] } {
  const sourceId = `jr-step-${index}-src`;
  const emptyGeom = {
    type: "Feature" as const,
    properties: {},
    geometry: { type: "LineString" as const, coordinates: [] as [number, number][] },
  };

  let coords: [number, number][] = [];
  if (step.polyline?.encodedPolyline) {
    coords = decodePolyline(step.polyline.encodedPolyline);
  }

  m.addSource(sourceId, { type: "geojson", data: emptyGeom });
  dynamicSourceIds.push(sourceId);

  if (step.type === "WALK") {
    const layerId = `jr-step-${index}-walk`;
    m.addLayer({
      id: layerId,
      type: "line",
      source: sourceId,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#FFFFFF",
        "line-width": 3,
        "line-opacity": 0.85,
        "line-dasharray": [2, 4],
      },
    });
    dynamicLayerIds.push(layerId);
  } else if (step.type === "SUBWAY") {
    const color = step.line_color || getLineColor(step.train_line || "");
    const glowId = `jr-step-${index}-glow`;
    const lineId = `jr-step-${index}-line`;
    m.addLayer({
      id: glowId,
      type: "line",
      source: sourceId,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": color,
        "line-width": 16,
        "line-opacity": 0.18,
        "line-blur": 6,
      },
    });
    m.addLayer({
      id: lineId,
      type: "line",
      source: sourceId,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": color,
        "line-width": 5,
        "line-opacity": 0.95,
      },
    });
    dynamicLayerIds.push(glowId, lineId);
  } else if (step.type === "BUS") {
    const glowId = `jr-step-${index}-bus-glow`;
    const lineId = `jr-step-${index}-bus-line`;
    m.addLayer({
      id: glowId,
      type: "line",
      source: sourceId,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#0057B8",
        "line-width": 12,
        "line-opacity": 0.1,
        "line-blur": 6,
      },
    });
    m.addLayer({
      id: lineId,
      type: "line",
      source: sourceId,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#0057B8",
        "line-width": 4,
        "line-opacity": 0.9,
      },
    });
    dynamicLayerIds.push(glowId, lineId);
  }

  return { sourceId, coords };
}

/** Remove all dynamically created route layers and sources */
export function clearRouteLayers(
  m: mapboxgl.Map,
  dynamicLayerIds: string[],
  dynamicSourceIds: string[],
) {
  for (const id of dynamicLayerIds) {
    if (m.getLayer(id)) m.removeLayer(id);
  }
  for (const id of dynamicSourceIds) {
    if (m.getSource(id)) m.removeSource(id);
  }
  dynamicLayerIds.length = 0;
  dynamicSourceIds.length = 0;
}

/** Start wire pulse animation along the full route */
export function startWirePulse(
  m: mapboxgl.Map,
  allCoords: [number, number][],
  particleFrameRef: { current: number | null },
) {
  if (allCoords.length < 2) return;

  if (m.getLayer(LYR_PARTICLES)) m.removeLayer(LYR_PARTICLES);
  if (m.getSource(SRC_PARTICLES)) m.removeSource(SRC_PARTICLES);

  m.addSource(SRC_PARTICLES, {
    type: "geojson",
    data: {
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: allCoords },
    },
  });

  const dashSeg = 2;
  const gapLen = 60;
  const totalCycle = dashSeg + gapLen;

  m.addLayer({
    id: LYR_PARTICLES,
    type: "line",
    source: SRC_PARTICLES,
    layout: { "line-join": "round", "line-cap": "round" },
    paint: {
      "line-color": "#FFFFFF",
      "line-width": 3,
      "line-opacity": 0.85,
      "line-dasharray": [0, totalCycle],
    },
  });

  const speed = 0.04;

  function tick(now: number) {
    const step = (now * speed) % totalCycle;

    if (step <= gapLen) {
      m.setPaintProperty(LYR_PARTICLES, "line-dasharray", [
        0, step, dashSeg, gapLen - step,
      ]);
    } else {
      const d = step - gapLen;
      m.setPaintProperty(LYR_PARTICLES, "line-dasharray", [
        d, gapLen, dashSeg - d, 0,
      ]);
    }

    particleFrameRef.current = requestAnimationFrame(tick);
  }
  particleFrameRef.current = requestAnimationFrame(tick);
}

/** Stop wire pulse animation and remove its layers */
export function stopWirePulse(
  m: mapboxgl.Map,
  particleFrameRef: { current: number | null },
) {
  if (particleFrameRef.current) {
    cancelAnimationFrame(particleFrameRef.current);
    particleFrameRef.current = null;
  }
  if (m.getLayer(LYR_PARTICLES)) m.removeLayer(LYR_PARTICLES);
  if (m.getSource(SRC_PARTICLES)) m.removeSource(SRC_PARTICLES);
}

