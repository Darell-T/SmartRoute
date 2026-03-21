"use client";

import { useEffect, useRef } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";

export interface TransitRouteData {
  walkIn: [number, number][];
  transit: [number, number][];
  walkOut: [number, number][];
  trainLine: string;
  originStationName: string;
  destStationName: string;
}

interface JarvisMapProps {
  onLocationUpdate?: (coords: { lng: number; lat: number }) => void;
  routeData?: TransitRouteData | null;
  isSpeaking?: boolean;
  destCoords?: { lat: number; lng: number } | null;
}

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

function getLineColor(line: string): string {
  return MTA_COLORS[line.toUpperCase()] ?? "#FFD700";
}

const WALK_IN_DUR = 1500;
const TRANSIT_DUR = 3000;
const WALK_OUT_DUR = 1000;
const PAUSE = 350;

const SRC_WALK_IN = "jr-walk-in-src";
const SRC_TRANSIT = "jr-transit-src";
const SRC_WALK_OUT = "jr-walk-out-src";
const LYR_WALK_IN = "jr-walk-in-lyr";
const LYR_TRANSIT_GLOW = "jr-transit-glow-lyr";
const LYR_TRANSIT = "jr-transit-lyr";
const LYR_WALK_OUT = "jr-walk-out-lyr";

export function JarvisMap({ onLocationUpdate, routeData, isSpeaking, destCoords }: JarvisMapProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<mapboxgl.Map | null>(null);
  const marker = useRef<mapboxgl.Marker | null>(null);
  const destMarker = useRef<mapboxgl.Marker | null>(null);
  const onLocationUpdateRef = useRef(onLocationUpdate);
  const mapReadyRef = useRef(false);
  const rotationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const rotationTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const originRef = useRef<[number, number] | null>(null);
  const stationMarkersRef = useRef<mapboxgl.Marker[]>([]);
  const initialFlyDoneRef = useRef(false);

  useEffect(() => {
    onLocationUpdateRef.current = onLocationUpdate;
  }, [onLocationUpdate]);

  // Map initialization
  useEffect(() => {
    if (!mapContainer.current) return;

    mapboxgl.accessToken = process.env.NEXT_PUBLIC_MAPBOX_TOKEN || "";

    const defaultLocation = { lng: -73.9857, lat: 40.7484 };

    map.current = new mapboxgl.Map({
      container: mapContainer.current,
      style: "mapbox://styles/mapbox/dark-v11",
      center: [defaultLocation.lng, defaultLocation.lat],
      zoom: 16,
      pitch: 55,
      bearing: -17.6,
      antialias: true,
    });

    map.current.on("style.load", () => {
      if (!map.current) return;

      const layers = map.current.getStyle().layers;
      const labelLayerId = layers?.find(
        (layer) => layer.type === "symbol" && layer.layout?.["text-field"],
      )?.id;

      map.current.addLayer(
        {
          id: "3d-buildings",
          source: "composite",
          "source-layer": "building",
          filter: ["==", "extrude", "true"],
          type: "fill-extrusion",
          minzoom: 15,
          paint: {
            "fill-extrusion-color": "#1a1a2e",
            "fill-extrusion-height": ["get", "height"],
            "fill-extrusion-base": ["get", "min_height"],
            "fill-extrusion-opacity": 0.8,
          },
        },
        labelLayerId,
      );

      createOrbMarker(defaultLocation);
      mapReadyRef.current = true;
    });

    let watchId: number;

    if (navigator.geolocation) {
      watchId = navigator.geolocation.watchPosition(
        (position) => {
          const coords = {
            lng: position.coords.longitude,
            lat: position.coords.latitude,
          };

          onLocationUpdateRef.current?.(coords);
          originRef.current = [coords.lng, coords.lat];

          if (map.current && !initialFlyDoneRef.current) {
            initialFlyDoneRef.current = true;
            map.current.flyTo({
              center: [coords.lng, coords.lat],
              zoom: 16,
              pitch: 55,
              duration: 2000,
            });
          }

          if (marker.current) {
            marker.current.setLngLat([coords.lng, coords.lat]);
          } else if (map.current) {
            createOrbMarker(coords);
          }
        },
        (error) => {
          console.log("Geolocation error:", error.message);
        },
        { enableHighAccuracy: true, maximumAge: 10000, timeout: 5000 },
      );
    }

    function createOrbMarker(coords: { lng: number; lat: number }) {
      if (!map.current) return;

      const el = document.createElement("div");
      el.className = "jarvis-orb";
      el.innerHTML = `
        <div class="orb-core"></div>
        <div class="orb-glow"></div>
        <div class="orb-pulse"></div>
      `;

      marker.current = new mapboxgl.Marker({ element: el, anchor: "center" })
        .setLngLat([coords.lng, coords.lat])
        .addTo(map.current);
    }

    // Task 5: Map interactions are ENABLED — user can pan/zoom/rotate freely
    // map.current.scrollZoom, dragPan, etc. are enabled by default

    return () => {
      map.current?.remove();
      if (watchId) navigator.geolocation.clearWatch(watchId);
    };
  }, []);

  // Destination orb (Task 2e)
  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;

    // Remove existing dest marker
    if (destMarker.current) {
      destMarker.current.remove();
      destMarker.current = null;
    }

    if (destCoords) {
      const el = document.createElement("div");
      el.className = "jarvis-orb dest-orb";
      el.innerHTML = `
        <div class="orb-core dest-core"></div>
        <div class="orb-glow dest-glow"></div>
        <div class="orb-pulse dest-pulse"></div>
      `;

      destMarker.current = new mapboxgl.Marker({ element: el, anchor: "center" })
        .setLngLat([destCoords.lng, destCoords.lat])
        .addTo(map.current!);
    }
  }, [destCoords]);

  // Route animation + camera rotation
  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;

    const m = map.current;

    function stopRotation() {
      if (rotationIntervalRef.current) {
        clearInterval(rotationIntervalRef.current);
        rotationIntervalRef.current = null;
      }
      if (rotationTimeoutRef.current) {
        clearTimeout(rotationTimeoutRef.current);
        rotationTimeoutRef.current = null;
      }
    }

    function stopAnimation() {
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
        animFrameRef.current = null;
      }
    }

    function stopAll() {
      stopRotation();
      stopAnimation();
    }

    function clearStationMarkers() {
      stationMarkersRef.current.forEach((mk) => mk.remove());
      stationMarkersRef.current = [];
    }

    function setSourceData(sourceId: string, coords: [number, number][]) {
      const source = m.getSource(sourceId) as mapboxgl.GeoJSONSource | undefined;
      if (source) {
        source.setData({
          type: "Feature",
          properties: {},
          geometry: { type: "LineString", coordinates: coords },
        });
      }
    }

    function clearRouteFromMap() {
      clearStationMarkers();
      for (const id of [LYR_WALK_IN, LYR_TRANSIT_GLOW, LYR_TRANSIT, LYR_WALK_OUT]) {
        if (m.getLayer(id)) m.removeLayer(id);
      }
      for (const id of [SRC_WALK_IN, SRC_TRANSIT, SRC_WALK_OUT]) {
        if (m.getSource(id)) m.removeSource(id);
      }
    }

    function ensureRouteLayers(lineColor: string) {
      const emptyGeom = {
        type: "Feature" as const,
        properties: {},
        geometry: { type: "LineString" as const, coordinates: [] },
      };

      if (!m.getSource(SRC_WALK_IN)) {
        m.addSource(SRC_WALK_IN, { type: "geojson", data: emptyGeom });
      }
      if (!m.getLayer(LYR_WALK_IN)) {
        m.addLayer({
          id: LYR_WALK_IN,
          type: "line",
          source: SRC_WALK_IN,
          layout: { "line-join": "round", "line-cap": "round" },
          paint: {
            "line-color": "#FFFFFF",
            "line-width": 2.5,
            "line-opacity": 0.7,
            "line-dasharray": [2, 4],
          },
        });
      }

      if (!m.getSource(SRC_TRANSIT)) {
        m.addSource(SRC_TRANSIT, { type: "geojson", data: emptyGeom });
      }
      if (!m.getLayer(LYR_TRANSIT_GLOW)) {
        m.addLayer({
          id: LYR_TRANSIT_GLOW,
          type: "line",
          source: SRC_TRANSIT,
          layout: { "line-join": "round", "line-cap": "round" },
          paint: {
            "line-color": lineColor,
            "line-width": 14,
            "line-opacity": 0.12,
            "line-blur": 6,
          },
        });
      }
      if (!m.getLayer(LYR_TRANSIT)) {
        m.addLayer({
          id: LYR_TRANSIT,
          type: "line",
          source: SRC_TRANSIT,
          layout: { "line-join": "round", "line-cap": "round" },
          paint: {
            "line-color": lineColor,
            "line-width": 5,
            "line-opacity": 0.95,
          },
        });
      }

      if (!m.getSource(SRC_WALK_OUT)) {
        m.addSource(SRC_WALK_OUT, { type: "geojson", data: emptyGeom });
      }
      if (!m.getLayer(LYR_WALK_OUT)) {
        m.addLayer({
          id: LYR_WALK_OUT,
          type: "line",
          source: SRC_WALK_OUT,
          layout: { "line-join": "round", "line-cap": "round" },
          paint: {
            "line-color": "#FFFFFF",
            "line-width": 2.5,
            "line-opacity": 0.7,
            "line-dasharray": [2, 4],
          },
        });
      }
    }

    function addStationBadge(
      coords: [number, number],
      name: string,
      lineLetter: string,
      lineColor: string,
    ) {
      const el = document.createElement("div");
      el.style.cssText = `
        display: flex;
        align-items: center;
        gap: 6px;
        background: ${lineColor};
        border-radius: 12px;
        padding: 4px 10px;
        font-size: 12px;
        font-weight: 600;
        color: ${lineColor === "#FCCC0A" || lineColor === "#6CBE45" ? "#000" : "#fff"};
        letter-spacing: 0.01em;
        white-space: nowrap;
        pointer-events: none;
        font-family: 'Space Grotesk', ui-sans-serif, system-ui, sans-serif;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
      `;
      el.innerHTML = `<span style="font-weight:700">${lineLetter}</span><span>${name}</span>`;

      const mk = new mapboxgl.Marker({ element: el, anchor: "bottom", offset: [0, -6] })
        .setLngLat(coords)
        .addTo(m);
      stationMarkersRef.current.push(mk);
    }

    // When routeData is cleared (new submission), clean up everything
    if (!routeData) {
      stopAll();
      clearRouteFromMap();
      return stopAll;
    }

    if (isSpeaking && routeData) {
      stopAll();
      clearRouteFromMap();

      const lineColor = getLineColor(routeData.trainLine);
      ensureRouteLayers(lineColor);

      // Fit bounds to encompass entire route
      const allCoords = [...routeData.walkIn, ...routeData.transit, ...routeData.walkOut];
      if (allCoords.length > 0) {
        const bounds = new mapboxgl.LngLatBounds();
        allCoords.forEach((c) => bounds.extend(c as mapboxgl.LngLatLike));
        m.fitBounds(bounds, { padding: 80, duration: 1500, pitch: 55 });
      }

      // Phase boundaries (ms) — delay animation start to let fitBounds settle
      const ANIM_DELAY = 1600;
      const T1 = ANIM_DELAY + WALK_IN_DUR;
      const T2 = T1 + PAUSE;
      const T3 = T2 + TRANSIT_DUR;
      const T4 = T3 + PAUSE;
      const T5 = T4 + WALK_OUT_DUR;

      const startTime = performance.now();
      let done = false;

      function frame(now: number) {
        const e = now - startTime;

        // Walk-in (starts after fitBounds delay)
        if (e >= ANIM_DELAY) {
          const p = Math.min((e - ANIM_DELAY) / WALK_IN_DUR, 1);
          const n = Math.max(2, Math.ceil(p * routeData!.walkIn.length));
          setSourceData(SRC_WALK_IN, routeData!.walkIn.slice(0, n));
        }

        // Transit (starts after walk-in + pause)
        if (e >= T2) {
          const p = Math.min((e - T2) / TRANSIT_DUR, 1);
          const n = Math.max(2, Math.ceil(p * routeData!.transit.length));
          setSourceData(SRC_TRANSIT, routeData!.transit.slice(0, n));
        }

        // Walk-out (starts after transit + pause)
        if (e >= T4) {
          const p = Math.min((e - T4) / WALK_OUT_DUR, 1);
          const n = Math.max(2, Math.ceil(p * routeData!.walkOut.length));
          setSourceData(SRC_WALK_OUT, routeData!.walkOut.slice(0, n));
        }

        if (e < T5) {
          animFrameRef.current = requestAnimationFrame(frame);
        } else if (!done) {
          done = true;

          // Ensure all segments fully drawn
          setSourceData(SRC_WALK_IN, routeData!.walkIn);
          setSourceData(SRC_TRANSIT, routeData!.transit);
          setSourceData(SRC_WALK_OUT, routeData!.walkOut);

          // Station badges at transit endpoints
          const originCoord = routeData!.transit[0];
          const destCoord = routeData!.transit[routeData!.transit.length - 1];
          addStationBadge(originCoord, routeData!.originStationName, routeData!.trainLine, lineColor);
          addStationBadge(destCoord, routeData!.destStationName, routeData!.trainLine, lineColor);

          // Fly to destination then begin slow rotation
          m.flyTo({ center: destCoord, zoom: 15, pitch: 60, duration: 2000 });
          rotationTimeoutRef.current = setTimeout(() => {
            rotationIntervalRef.current = setInterval(() => {
              m.setBearing((m.getBearing() + 0.3) % 360);
            }, 50);
          }, 2100);
        }
      }

      animFrameRef.current = requestAnimationFrame(frame);
    } else if (!isSpeaking && routeData) {
      // Audio ended — stop rotation, fly back to origin, keep route visible
      stopRotation();
      const origin = originRef.current;
      if (origin) {
        m.flyTo({ center: origin, zoom: 16, pitch: 55, speed: 0.5, duration: 3000 });
      }
    }

    return stopAll;
  }, [isSpeaking, routeData]);

  return (
    <>
      <style jsx global>{`
        .jarvis-orb {
          width: 80px;
          height: 80px;
          position: relative;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .orb-core {
          width: 20px;
          height: 20px;
          background: radial-gradient(
            circle,
            #4da6ff 0%,
            #2d7dd2 50%,
            #1a5fa8 100%
          );
          border-radius: 50%;
          position: absolute;
          z-index: 3;
          box-shadow:
            0 0 10px #4da6ff,
            0 0 20px #4da6ff,
            0 0 30px #2d7dd2;
        }

        .orb-glow {
          width: 60px;
          height: 60px;
          background: radial-gradient(
            circle,
            rgba(77, 166, 255, 0.3) 0%,
            rgba(45, 125, 210, 0.1) 50%,
            transparent 70%
          );
          border-radius: 50%;
          position: absolute;
          z-index: 2;
          animation: orbGlow 2s ease-in-out infinite;
        }

        .orb-pulse {
          width: 80px;
          height: 80px;
          background: radial-gradient(
            circle,
            rgba(77, 166, 255, 0.15) 0%,
            transparent 60%
          );
          border-radius: 50%;
          position: absolute;
          z-index: 1;
          animation: orbPulse 3s ease-in-out infinite;
        }

        /* Destination orb — warm amber/gold */
        .dest-core {
          background: radial-gradient(
            circle,
            #F5A623 0%,
            #D4891A 50%,
            #B06E12 100%
          ) !important;
          box-shadow:
            0 0 10px #F5A623,
            0 0 20px #F5A623,
            0 0 30px #D4891A !important;
        }

        .dest-glow {
          background: radial-gradient(
            circle,
            rgba(245, 166, 35, 0.3) 0%,
            rgba(212, 137, 26, 0.1) 50%,
            transparent 70%
          ) !important;
        }

        .dest-pulse {
          background: radial-gradient(
            circle,
            rgba(245, 166, 35, 0.15) 0%,
            transparent 60%
          ) !important;
        }

        @keyframes orbGlow {
          0%,
          100% {
            transform: scale(1);
            opacity: 1;
          }
          50% {
            transform: scale(1.1);
            opacity: 0.8;
          }
        }

        @keyframes orbPulse {
          0%,
          100% {
            transform: scale(1);
            opacity: 0.6;
          }
          50% {
            transform: scale(1.3);
            opacity: 0.3;
          }
        }
      `}</style>
      <div ref={mapContainer} className="absolute inset-0 w-full h-full" />
    </>
  );
}
