"use client";

import { useEffect, useRef } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { TripsLayer } from "@deck.gl/geo-layers";
import { PathLayer } from "@deck.gl/layers";

import type { TransitRouteData, Coordinates } from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import { createOrb, createOrbMarker } from "./map/orbs";
import { applyHudMapStyle } from "./map/style";
import { flyToRoute, startRotation, stopRotation, flyToOrigin } from "./map/camera";
import { addStationBadge, addIntermediateStopLabels, clearBadges } from "./map/station-badges";
import { buildTrips, getLineColor, type Trip } from "./map/route-layers";

function toLngLat(c: Coordinates): [number, number] {
  return [c.longitude, c.latitude];
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
  const overlayRef = useRef<MapboxOverlay | null>(null);

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

    map.current = new mapboxgl.Map({
      container: mapContainer.current,
      style: "mapbox://styles/mapbox/dark-v11",
      center: [DEFAULT_LOCATION.lng, DEFAULT_LOCATION.lat],
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

      const overlay = new MapboxOverlay({ interleaved: true, layers: [] });
      map.current.addControl(overlay as unknown as mapboxgl.IControl);
      overlayRef.current = overlay;

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

    function handlePosition(coords: { lng: number; lat: number }) {
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
        marker.current = createOrbMarker(map.current, coords, "#00D4FF", "rgba(0, 212, 255, 0.4)");
      }
    }

    let watchId: number;

    if (navigator.geolocation) {
      watchId = navigator.geolocation.watchPosition(
        (position) => {
          handlePosition({
            lng: position.coords.longitude,
            lat: position.coords.latitude,
          });
        },
        (error) => {
          console.error("Geolocation error:", error.message);
          handlePosition(DEFAULT_LOCATION);
        },
        { enableHighAccuracy: true, maximumAge: 10000, timeout: 5000 },
      );
    } else {
      handlePosition(DEFAULT_LOCATION);
    }

    return () => {
      map.current?.remove();
      if (watchId) navigator.geolocation.clearWatch(watchId);
    };
  }, []);

  // Route animation + camera rotation
  useEffect(() => {
    if (!map.current || !mapReadyRef.current || !overlayRef.current) return;

    const m = map.current;
    const overlay = overlayRef.current;

    const TRAIL_LENGTH = 850;
    const FADE_IN = 700;

    type CompletedStep = { trip: Trip; revealedAt: number };

    function renderTrips(
      trips: Trip[],
      currentTime: number,
      completedSteps: CompletedStep[],
    ) {
      const layers: import("@deck.gl/core").Layer[] = [];

      for (let i = 0; i < completedSteps.length; i++) {
        const cs = completedSteps[i];
        const fade = Math.min(Math.max((currentTime - cs.revealedAt) / FADE_IN, 0), 1);
        const eased = fade * fade * (3 - 2 * fade);
        layers.push(
          new PathLayer<Trip>({
            id: `jr-path-${i}`,
            data: [cs.trip],
            getPath: (t) => t.path,
            getColor: (t) => [t.color[0], t.color[1], t.color[2], 255],
            getWidth: (t) => t.width,
            widthUnits: "pixels",
            widthMinPixels: 3,
            opacity: 0.95 * eased,
            capRounded: true,
            jointRounded: true,
          }),
        );
      }

      layers.push(
        new TripsLayer<Trip>({
          id: "jr-trips",
          data: trips,
          getPath: (t) => t.path,
          getTimestamps: (t) => t.timestamps,
          getColor: (t) => t.color,
          getWidth: (t) => t.width,
          widthUnits: "pixels",
          opacity: 0.95,
          capRounded: true,
          jointRounded: true,
          trailLength: TRAIL_LENGTH,
          currentTime,
          fadeTrail: true,
        }),
      );

      overlay.setProps({ layers });
    }

    function stopAnimation() {
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
        animFrameRef.current = null;
      }
    }

    function stopAll() {
      stopRotation(rotationRefs);
      stopAnimation();
    }

    function clearRouteFromMap() {
      clearBadges(stationMarkersRef.current);
      overlay.setProps({ layers: [] });
    }

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

      const { trips, stepCoords, stepEndTimes, totalDuration } = buildTrips(steps);

      const allCoords = stepCoords.flat();
      if (allCoords.length > 0) {
        flyToRoute(m, allCoords);
      }

      const userOrigin: [number, number] = originRef.current || allCoords[0] || [0, 0];
      const endTime = totalDuration + TRAIL_LENGTH;
      const startTime = performance.now();
      let done = false;

      const stepTripIndex = new Map<number, number>();
      {
        let ti = 0;
        for (let i = 0; i < steps.length; i++) {
          if (stepCoords[i].length >= 2) {
            stepTripIndex.set(i, ti);
            ti++;
          }
        }
      }

      const completedSteps: CompletedStep[] = [];
      const badgeKeys = new Set<string>();
      let badgeCount = 0;
      let nextStepToReveal = 0;

      function addBadgeIfNew(coords: [number, number], name: string, letter: string, color: string) {
        const key = `${coords[0].toFixed(4)},${coords[1].toFixed(4)}`;
        if (badgeKeys.has(key)) return;
        badgeKeys.add(key);
        const mk = addStationBadge(m, coords, name, letter, color, badgeCount++);
        stationMarkersRef.current.push(mk);
      }

      function revealStep(i: number) {
        const step = steps[i];
        const coords = stepCoords[i];

        const ti = stepTripIndex.get(i);
        if (ti !== undefined) {
          completedSteps.push({ trip: trips[ti], revealedAt: stepEndTimes[i] });
        }

        if (step.type === "SUBWAY" || step.type === "BUS") {
          const color = step.type === "SUBWAY"
            ? (step.line_color || getLineColor(step.train_line || ""))
            : "#0057B8";
          const letter = step.train_line || (step.type === "BUS" ? "BUS" : "?");

          if (step.departure_coords && step.departure_stop) {
            addBadgeIfNew(toLngLat(step.departure_coords), step.departure_stop, letter, color);
          } else if (coords.length > 0 && step.departure_stop) {
            addBadgeIfNew(coords[0], step.departure_stop, letter, color);
          }

          if (step.arrival_coords && step.arrival_stop) {
            addBadgeIfNew(toLngLat(step.arrival_coords), step.arrival_stop, letter, color);
          } else if (coords.length > 0 && step.arrival_stop) {
            addBadgeIfNew(coords[coords.length - 1], step.arrival_stop, letter, color);
          }

          if (step.intermediate_stops) {
            const labels = addIntermediateStopLabels(m, coords, step.intermediate_stops, color);
            stationMarkersRef.current.push(...labels);
          }
        }
      }

      function frame(now: number) {
        const e = now - startTime;
        const currentTime = Math.min(e, endTime);

        while (nextStepToReveal < steps.length && e >= stepEndTimes[nextStepToReveal]) {
          revealStep(nextStepToReveal);
          nextStepToReveal++;
        }

        renderTrips(trips, currentTime, completedSteps);

        if (e < endTime) {
          animFrameRef.current = requestAnimationFrame(frame);
          return;
        }

        if (done) return;
        done = true;

        const lastCoords = stepCoords[stepCoords.length - 1] || [];
        const destEnd = lastCoords[lastCoords.length - 1] || userOrigin;
        startRotation(m, destEnd, rotationRefs);
      }

      animFrameRef.current = requestAnimationFrame(frame);
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
    <div ref={mapContainer} className="absolute inset-0 w-full h-full" />
  );
}
