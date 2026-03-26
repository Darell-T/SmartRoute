import mapboxgl from "mapbox-gl";
import * as THREE from "three";
import polyline from "@mapbox/polyline";
import type { RouteStep } from "../jarvis-map";

const LYR_BEAM = "jr-beam-3d";
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
        "line-width": 2.5,
        "line-opacity": 0.7,
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
        "line-width": 14,
        "line-opacity": 0.12,
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

/** Fire a 3D beam effect at a coordinate, calls onComplete when done */
export function fireBeam(
  m: mapboxgl.Map,
  origin: [number, number],
  color: string,
  beamFrameRef: { current: number | null },
  onComplete: () => void,
) {
  stopBeam(m, beamFrameRef);

  let threeColor: THREE.Color;
  const rgbaMatch = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (rgbaMatch) {
    threeColor = new THREE.Color(
      parseInt(rgbaMatch[1]) / 255,
      parseInt(rgbaMatch[2]) / 255,
      parseInt(rgbaMatch[3]) / 255,
    );
  } else {
    threeColor = new THREE.Color(color);
  }

  const merc = mapboxgl.MercatorCoordinate.fromLngLat(
    { lng: origin[0], lat: origin[1] },
    0,
  );
  const scale = merc.meterInMercatorCoordinateUnits();
  const beamHeightMeters = 200;

  const scene = new THREE.Scene();
  const camera = new THREE.Camera();
  let renderer: THREE.WebGLRenderer;

  const beamGeom = new THREE.PlaneGeometry(2 * scale, beamHeightMeters * scale);
  const beamMat = new THREE.MeshBasicMaterial({
    color: threeColor,
    transparent: true,
    opacity: 0.7,
    side: THREE.DoubleSide,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const beamMesh = new THREE.Mesh(beamGeom, beamMat);

  const glowGeom = new THREE.PlaneGeometry(8 * scale, beamHeightMeters * scale);
  const glowMat = new THREE.MeshBasicMaterial({
    color: threeColor,
    transparent: true,
    opacity: 0.15,
    side: THREE.DoubleSide,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const glowMesh = new THREE.Mesh(glowGeom, glowMat);

  const halfHeight = (beamHeightMeters * scale) / 2;
  beamMesh.position.set(merc.x, merc.y, halfHeight);
  glowMesh.position.set(merc.x, merc.y, halfHeight);
  beamMesh.scale.set(1, 0, 1);
  glowMesh.scale.set(1, 0, 1);

  scene.add(beamMesh);
  scene.add(glowMesh);

  const EXTEND_DUR = 600;
  const FADE_DUR = 400;
  const beamStart = performance.now();
  let beamDone = false;

  const customLayer: mapboxgl.CustomLayerInterface = {
    id: LYR_BEAM,
    type: "custom",
    renderingMode: "3d",
    onAdd(_map: mapboxgl.Map, gl: WebGLRenderingContext) {
      renderer = new THREE.WebGLRenderer({
        canvas: _map.getCanvas(),
        context: gl,
        antialias: true,
      });
      renderer.autoClear = false;
    },
    render(_gl: WebGLRenderingContext, matrix: number[]) {
      if (beamDone) return;

      camera.projectionMatrix = new THREE.Matrix4().fromArray(matrix);
      camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();

      const elapsed = performance.now() - beamStart;

      if (elapsed < EXTEND_DUR) {
        const p = elapsed / EXTEND_DUR;
        const eased = 1 - Math.pow(1 - p, 3);
        beamMesh.scale.set(1, eased, 1);
        glowMesh.scale.set(1, eased, 1);
      } else if (elapsed < EXTEND_DUR + FADE_DUR) {
        beamMesh.scale.set(1, 1, 1);
        glowMesh.scale.set(1, 1, 1);
        const fadeP = (elapsed - EXTEND_DUR) / FADE_DUR;
        beamMat.opacity = 0.7 * (1 - fadeP);
        glowMat.opacity = 0.15 * (1 - fadeP);
      } else {
        beamDone = true;
        beamMat.opacity = 0;
        glowMat.opacity = 0;
        requestAnimationFrame(() => {
          stopBeam(m, beamFrameRef);
          onComplete();
        });
        return;
      }

      renderer.resetState();
      renderer.render(scene, camera);
      m.triggerRepaint();
    },
  };

  m.addLayer(customLayer);
  m.triggerRepaint();
}

/** Stop 3D beam animation */
export function stopBeam(
  m: mapboxgl.Map,
  beamFrameRef: { current: number | null },
) {
  if (beamFrameRef.current) {
    cancelAnimationFrame(beamFrameRef.current);
    beamFrameRef.current = null;
  }
  if (m.getLayer(LYR_BEAM)) m.removeLayer(LYR_BEAM);
}
