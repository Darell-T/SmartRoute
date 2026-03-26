"use client";

import { useEffect, useRef } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";

import { createOrb, createOrbMarker, ORB_PULSE_KEYFRAME } from "./map/orbs";
import { applyHudMapStyle } from "./map/style";
import { calculateBearing, flyToRoute, startRotation, stopRotation, flyToOrigin } from "./map/camera";
import { addStationBadge, addIntermediateStopLabels, clearBadges } from "./map/station-badges";
import {
  addStepLayers, clearRouteLayers, startWirePulse, stopWirePulse,
  fireBeam, stopBeam, getStepDuration, easeOutCubic, subPath, getLineColor,
} from "./map/route-layers";

export interface RouteStep {
  type: "WALK" | "SUBWAY" | "BUS";
  start_point?: { latitude: number; longitude: number };
  end_point?: { latitude: number; longitude: number };
  polyline?: { encodedPolyline: string };
  train_line?: string;
  line_color?: string;
  direction?: string;
  departure_stop?: string;
  arrival_stop?: string;
  departure_coords?: { latitude: number; longitude: number };
  arrival_coords?: { latitude: number; longitude: number };
  minutes_until_train_arrives?: number;
  minutes_until_arrival?: number;
  stop_count?: number;
  route_id?: string;
  intermediate_stops?: string[];
}

/** Convert an API coordinate ({latitude, longitude}) to Mapbox [lng, lat] */
function toLngLat(c: { latitude: number; longitude: number }): [number, number] {
  return [c.longitude, c.latitude];
}

export interface TransitRouteData {
  steps: RouteStep[];
}

interface JarvisMapProps {
  onLocationUpdate?: (coords: { lng: number; lat: number }) => void;
  routeData?: TransitRouteData | null;
  isSpeaking?: boolean;
  destCoords?: { lat: number; lng: number } | null;
  /** Called with a recenter function once the map is ready */
  onMapReady?: (actions: { recenter: () => void }) => void;
}

export function JarvisMap({ onLocationUpdate, routeData, isSpeaking, destCoords, onMapReady }: JarvisMapProps) {
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
  const particleFrameRef = useRef<number | null>(null);
  const beamFrameRef = useRef<number | null>(null);
  const dynamicLayerIds = useRef<string[]>([]);
  const dynamicSourceIds = useRef<string[]>([]);

  const rotationRefs = {
    rotationTimeout: rotationTimeoutRef,
    rotationInterval: rotationIntervalRef,
  };

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
      pitch: 60,
      bearing: 15,
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
            "fill-extrusion-color": "#080810",
            "fill-extrusion-height": ["get", "height"],
            "fill-extrusion-base": ["get", "min_height"],
            "fill-extrusion-opacity": 0.35,
          },
        },
        labelLayerId,
      );

      applyHudMapStyle(map.current);
      mapReadyRef.current = true;

      onMapReady?.({
        recenter: () => {
          const origin = originRef.current;
          if (origin && map.current) {
            map.current.flyTo({ center: origin, zoom: 16, pitch: 60, duration: 1500 });
          }
        },
      });
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
              pitch: 60,
              duration: 2000,
            });
          }

          if (marker.current) {
            marker.current.setLngLat([coords.lng, coords.lat]);
          } else if (map.current) {
            initOrbMarker(coords);
          }
        },
        (error) => {
          console.error("Geolocation error:", error.message);
        },
        { enableHighAccuracy: true, maximumAge: 10000, timeout: 5000 },
      );
    }

    function initOrbMarker(coords: { lng: number; lat: number }) {
      if (!map.current) return;
      marker.current = createOrbMarker(map.current, coords, "#00D4FF", "rgba(0, 212, 255, 0.4)");
    }

    return () => {
      map.current?.remove();
      if (watchId) navigator.geolocation.clearWatch(watchId);
    };
  }, []);

  // Route animation + camera rotation
  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;

    const m = map.current;

    function stopAnimation() {
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
        animFrameRef.current = null;
      }
    }

    function stopAll() {
      stopRotation(rotationRefs);
      stopAnimation();
      stopWirePulse(m, particleFrameRef);
      stopBeam(m, beamFrameRef);
    }

    function clearRouteFromMap() {
      clearBadges(stationMarkersRef.current);
      clearRouteLayers(m, dynamicLayerIds.current, dynamicSourceIds.current);
    }

    // When routeData is cleared, clean up everything
    if (!routeData) {
      stopAll();
      clearRouteFromMap();
      return stopAll;
    }

    if (isSpeaking && routeData) {
      stopAll();
      clearRouteFromMap();

      const steps = routeData.steps;
      if (!steps || steps.length === 0) return stopAll;

      // Prepare all step layers and decode coords
      const stepData: { sourceId: string; coords: [number, number][]; step: RouteStep }[] = [];
      for (let i = 0; i < steps.length; i++) {
        const { sourceId, coords } = addStepLayers(
          m, steps[i], i, dynamicLayerIds.current, dynamicSourceIds.current,
        );
        stepData.push({ sourceId, coords, step: steps[i] });
      }

      // Collect all coordinates for fitBounds
      const allCoords = stepData.flatMap((s) => s.coords);
      if (allCoords.length > 0) {
        flyToRoute(m, allCoords);
      }

      // Fire beam at origin, then animate steps sequentially
      const userOrigin: [number, number] = originRef.current || allCoords[0] || [0, 0];

      fireBeam(m, userOrigin, "rgba(0, 212, 255, 0.7)", beamFrameRef, () => {
        const FIT_SETTLE = 1200;
        const PAUSE = 350;

        // Build timeline: each step starts after previous finishes + pause
        const timeline: { startMs: number; durMs: number; idx: number }[] = [];
        let cursor = FIT_SETTLE;
        for (let i = 0; i < stepData.length; i++) {
          const dur = getStepDuration(stepData[i].step.type);
          timeline.push({ startMs: cursor, durMs: dur, idx: i });
          cursor += dur + PAUSE;
        }
        const totalDuration = cursor;

        const startTime = performance.now();
        let done = false;

        function frame(now: number) {
          const e = now - startTime;

          for (const seg of timeline) {
            if (e >= seg.startMs) {
              const rawP = Math.min((e - seg.startMs) / seg.durMs, 1);
              const p = easeOutCubic(rawP);
              const coords = stepData[seg.idx].coords;
              const smoothCoords = subPath(coords, p);
              const source = m.getSource(stepData[seg.idx].sourceId) as mapboxgl.GeoJSONSource | undefined;
              if (source) {
                source.setData({
                  type: "Feature",
                  properties: {},
                  geometry: { type: "LineString", coordinates: smoothCoords },
                });
              }
            }
          }

          if (e < totalDuration) {
            animFrameRef.current = requestAnimationFrame(frame);
          } else if (!done) {
            done = true;

            // Ensure all segments fully drawn
            for (const seg of stepData) {
              const source = m.getSource(seg.sourceId) as mapboxgl.GeoJSONSource | undefined;
              if (source) {
                source.setData({
                  type: "Feature",
                  properties: {},
                  geometry: { type: "LineString", coordinates: seg.coords },
                });
              }
            }

            // Station badges at each transit step's departure and arrival (deduplicated)
            const badgeKeys = new Set<string>();
            let badgeCount = 0;
            function addBadgeIfNew(coords: [number, number], name: string, letter: string, color: string) {
              const key = `${coords[0].toFixed(4)},${coords[1].toFixed(4)}`;
              if (badgeKeys.has(key)) return;
              badgeKeys.add(key);
              const mk = addStationBadge(m, coords, name, letter, color, badgeCount++);
              stationMarkersRef.current.push(mk);
            }

            for (const seg of stepData) {
              if (seg.step.type === "SUBWAY" || seg.step.type === "BUS") {
                const color = seg.step.type === "SUBWAY"
                  ? (seg.step.line_color || getLineColor(seg.step.train_line || ""))
                  : "#0057B8";
                const letter = seg.step.train_line || (seg.step.type === "BUS" ? "BUS" : "?");

                if (seg.step.departure_coords && seg.step.departure_stop) {
                  addBadgeIfNew(
                    toLngLat(seg.step.departure_coords),
                    seg.step.departure_stop,
                    letter,
                    color,
                  );
                } else if (seg.coords.length > 0 && seg.step.departure_stop) {
                  addBadgeIfNew(seg.coords[0], seg.step.departure_stop, letter, color);
                }

                if (seg.step.arrival_coords && seg.step.arrival_stop) {
                  addBadgeIfNew(
                    toLngLat(seg.step.arrival_coords),
                    seg.step.arrival_stop,
                    letter,
                    color,
                  );
                } else if (seg.coords.length > 0 && seg.step.arrival_stop) {
                  addBadgeIfNew(seg.coords[seg.coords.length - 1], seg.step.arrival_stop, letter, color);
                }
              }
            }

            // Intermediate stop dot markers along transit polylines
            for (const seg of stepData) {
              if ((seg.step.type === "SUBWAY" || seg.step.type === "BUS") && seg.step.intermediate_stops) {
                const segColor = seg.step.type === "SUBWAY"
                  ? (seg.step.line_color || getLineColor(seg.step.train_line || ""))
                  : "#0057B8";
                const labels = addIntermediateStopLabels(m, seg.coords, seg.step.intermediate_stops, segColor);
                stationMarkersRef.current.push(...labels);
              }
            }

            // Start wire pulse along the entire route
            const fullRouteCoords = stepData.flatMap((s) => s.coords);
            startWirePulse(m, fullRouteCoords, particleFrameRef);

            // Destination beam then fly + rotate
            const lastCoords = stepData[stepData.length - 1].coords;
            const destEnd = lastCoords[lastCoords.length - 1] || userOrigin;
            fireBeam(m, destEnd, "rgba(255, 59, 48, 0.7)", beamFrameRef, () => {
              startRotation(m, destEnd, rotationRefs);
            });
          }
        }

        animFrameRef.current = requestAnimationFrame(frame);
      });
    } else if (!isSpeaking && routeData) {
      // Audio ended — stop rotation, fly back to origin facing toward route
      stopRotation(rotationRefs);
      const origin = originRef.current;
      if (origin) {
        const firstTransit = routeData.steps.find(
          (s) => (s.type === "SUBWAY" || s.type === "BUS") && s.departure_coords
        );
        const firstTransitCoords = firstTransit?.departure_coords
          ? toLngLat(firstTransit.departure_coords)
          : null;
        flyToOrigin(m, origin, firstTransitCoords);
      }
    }

    return stopAll;
  }, [isSpeaking, routeData]);

  // Destination marker — use primitive deps to avoid spurious re-runs
  const destLng = destCoords?.lng ?? null;
  const destLat = destCoords?.lat ?? null;

  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;

    if (destMarker.current) {
      destMarker.current.remove();
      destMarker.current = null;
    }

    if (destLng == null || destLat == null || !isFinite(destLng) || !isFinite(destLat)) {
      return;
    }

    const el = createOrb("#FF3B30", "rgba(255, 59, 48, 0.4)");

    destMarker.current = new mapboxgl.Marker({ element: el, anchor: "center" })
      .setLngLat([destLng, destLat])
      .addTo(map.current);

    return () => {
      if (destMarker.current) {
        destMarker.current.remove();
        destMarker.current = null;
      }
    };
  }, [destLng, destLat]);

  return (
    <>
      <style jsx global>{ORB_PULSE_KEYFRAME}</style>
      <div ref={mapContainer} className="absolute inset-0 w-full h-full" />
    </>
  );
}
