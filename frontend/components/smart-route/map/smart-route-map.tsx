"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { TransitRouteData } from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import {
  createCurrentLocationDot,
  createDestinationPin,
  createWaypointMarker,
  updateCurrentLocationDot,
} from "./route-preview-markers";
import { flyToRoute, stopRotation } from "@/components/map/camera";
import { addStationBadge, clearBadges } from "@/components/map/station-badges";
import { buildTrips, getLineColor } from "@/components/map/route-layers";
import { isTransitStep } from "@/lib/route-planning";
import { ensureBuildingsLayer } from "@/components/map/buildings-layer";
import {
  buildSubwayLaneFeaturesFromVisual,
  buildSubwayStopFeatures,
  ensureMtaBulletImages,
  ensureSubwayNetworkLayers,
  splitStationAnchorFeatureCollections,
  stationMarkerRouteIds,
  setSubwayNetworkHidden,
  SUBWAY_CASING_LAYER_ID,
  SUBWAY_FILL_LAYER_ID,
  SUBWAY_GLOW_LAYER_ID,
  type SubwayStationMarkerCollections,
} from "@/components/map/subway-network";
import {
  bringRouteStopsToTop,
  clearRouteStopData,
  ensureRouteStopLayers,
  setRouteStopData,
} from "@/components/map/route-stops";

import {
  toLngLat,
  artifactUrl,
  mapFeatureArrayProperty,
  firstSymbolLayerId,
  DEBUG_LIVE_MAP,
  loadVisualSubwayNetworkOrNull,
  loadSubwayStationAnchorsOrNull,
} from "./smart-route-map-helpers";

declare global {
  interface Window {
    __smartRouteMap?: maplibregl.Map;
  }
}

const DARK_MAP_STYLE_URL =
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

function applyDarkMapTheme(mapInstance: maplibregl.Map): void {
  // The map remains a stable dark cartographic canvas in both interface
  // themes. Only the navigation and Route/Alerts rail switch appearance.
  for (const layer of mapInstance.getStyle().layers ?? []) {
    const id = layer.id;
    const type = layer.type;
    try {
      if (
        type === "line" &&
        /road|street|tunnel|bridge|motorway|trunk|primary|secondary|tertiary/i.test(id)
      ) {
        mapInstance.setPaintProperty(id, "line-color", "#2B3A4D");
        mapInstance.setPaintProperty(id, "line-opacity", 0.55);
      } else if (type === "fill" && /water|ocean|river|bay/i.test(id)) {
        mapInstance.setPaintProperty(id, "fill-color", "#1B3A52");
      } else if (type === "fill" && /park|wood|grass|forest|cemetery/i.test(id)) {
        mapInstance.setPaintProperty(id, "fill-color", "#1C4327");
        mapInstance.setPaintProperty(id, "fill-opacity", 0.72);
      } else if (type === "fill" && /land|landuse|sand/i.test(id)) {
        mapInstance.setPaintProperty(id, "fill-color", "#161E2E");
        mapInstance.setPaintProperty(id, "fill-opacity", 0.66);
      } else if (type === "background") {
        mapInstance.setPaintProperty(id, "background-color", "#0D1220");
      } else if (type === "symbol") {
        if (/poi/i.test(id)) {
          mapInstance.setLayoutProperty(id, "visibility", "none");
        } else {
          mapInstance.setPaintProperty(id, "text-opacity", 0.55);
          mapInstance.setPaintProperty(id, "text-halo-color", "#05070A");
        }
      }
    } catch {
      // This CARTO style revision lacks the targeted property; skip it.
    }
  }
}

interface SmartRouteMapProps {
  onLocationUpdate?: (coords: { lng: number; lat: number; fallback?: true }) => void;
  routeData?: TransitRouteData | null;
  destCoords?: { lat: number; lng: number } | null;
  mobileSheetState?: string;
  onMapReady?: (actions: {
    recenter: () => void;
    zoomIn: () => void;
    zoomOut: () => void;
    resetNorth: () => void;
  }) => void;
}

export function SmartRouteMap({
  onLocationUpdate,
  routeData,
  destCoords,
  mobileSheetState,
  onMapReady,
}: SmartRouteMapProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const marker = useRef<maplibregl.Marker | null>(null);
  const markerElement = useRef<HTMLDivElement | null>(null);
  const destMarker = useRef<maplibregl.Marker | null>(null);
  const waypointMarkersRef = useRef<maplibregl.Marker[]>([]);
  const onLocationUpdateRef = useRef(onLocationUpdate);
  const mapReadyRef = useRef(false);
  const rotationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const rotationTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const originRef = useRef<[number, number] | null>(null);
  const originAccuracyRef = useRef<number | null>(null);
  const stationMarkersRef = useRef<maplibregl.Marker[]>([]);
  const initialFlyDoneRef = useRef(false);
  const routePreviewFitKeyRef = useRef<string | null>(null);
  const subwayLanesRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const subwayStopsRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const subwayStationMarkersRef =
    useRef<SubwayStationMarkerCollections | null>(null);
  const subwayVisualModeActiveRef = useRef(false);
  const subwayVisualFallbackUsedRef = useRef(false);
  const [subwayLayerDataVersion, setSubwayLayerDataVersion] = useState(0);
  const [mapStyleVersion, setMapStyleVersion] = useState(0);

  // Slot the native buildings below the subway lines once those layers exist.
  const syncBuildingsOrder = () => {
    if (!map.current) return;
    const beforeId = map.current.getLayer(SUBWAY_GLOW_LAYER_ID)
      ? SUBWAY_GLOW_LAYER_ID
      : map.current.getLayer(SUBWAY_CASING_LAYER_ID)
        ? SUBWAY_CASING_LAYER_ID
        : undefined;
    ensureBuildingsLayer(map.current, beforeId);
  };

  useEffect(() => {
    onLocationUpdateRef.current = onLocationUpdate;
  }, [onLocationUpdate]);

  useEffect(() => {
    if (!mapContainer.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: DARK_MAP_STYLE_URL,
      center: [DEFAULT_LOCATION.lng, DEFAULT_LOCATION.lat],
      zoom: 14.5,
      pitch: 45,
      bearing: 0,
      // antialias OFF: MSAA on the whole WebGL canvas is a heavy fragment
      // cost (multiplied by devicePixelRatio) for negligible gain on vector
      // content, which already has its own edge AA. Pan-smoothness win.
      canvasContextAttributes: { antialias: false },
    });

    function handleMissingBaseStyleImage(event: { id: string }) {
      const activeMap = map.current;
      if (
        !activeMap ||
        event.id.startsWith("subway-bullet-") ||
        activeMap.hasImage(event.id)
      ) {
        return;
      }
      // CARTO occasionally references an optional shield sprite that is absent
      // from its published sprite sheet. Registering a transparent fallback
      // keeps MapLibre quiet without masking SmartRoute's own transit bullets.
      activeMap.addImage(event.id, {
        width: 1,
        height: 1,
        data: new Uint8Array(4),
      });
    }

    map.current.on("styleimagemissing", handleMissingBaseStyleImage);

    // QA debug handle: dev-only exposure for Playwright-driven route QA. Gated by
    // process.env.NODE_ENV !== "production" AND URL param `qa-map=1`. Never set
    // in production; ignored by normal app behavior; reads only the URL once at
    // map construction time, so subsequent navigation cannot toggle it on.
    if (typeof window !== "undefined" && process.env.NODE_ENV !== "production") {
      try {
        const qaParams = new URLSearchParams(window.location.search);
        if (qaParams.get("qa-map") === "1") {
          window.__smartRouteMap = map.current;
          // eslint-disable-next-line no-console
          console.info("[smart-route-map/qa-map] window.__smartRouteMap exposed");
        }
      } catch {
        // URLSearchParams or window access guarded -- safe to silently skip.
      }
    }

    function handleMapBackgroundClick(event: maplibregl.MapMouseEvent) {
      if (!map.current) return;
      if (
        subwayVisualModeActiveRef.current &&
        !subwayVisualFallbackUsedRef.current &&
        map.current.getLayer(SUBWAY_FILL_LAYER_ID)
      ) {
        const visualHits = map.current.queryRenderedFeatures(event.point, {
          layers: [SUBWAY_FILL_LAYER_ID],
        });
        const feature = visualHits[0];
        if (DEBUG_LIVE_MAP && feature?.properties) {
          const properties = feature.properties;
          // eslint-disable-next-line no-console
          console.info("[smart-route-map/subway-visual-click]", {
            route_ids: mapFeatureArrayProperty(properties.route_ids),
            color_group: mapFeatureArrayProperty(properties.color_route_ids),
            route_id: properties.route_id,
            representative_route_id: properties.representative_route_id,
            color: properties.color,
            corridor_id: properties.corridor_id,
            bundle_id: properties.bundle_id,
            visual_feature_type: properties.visual_feature_type,
            stop_pair: properties.stop_pair,
            length_m: properties.length_m,
            source_shape_ids: mapFeatureArrayProperty(properties.source_shape_ids),
            source_edge_ids: mapFeatureArrayProperty(properties.source_edge_ids),
            lane_slot: properties.lane_slot,
            lane_group_id: properties.lane_group_id,
            lane_slot_source: properties.lane_slot_source,
            lane_order_basis: mapFeatureArrayProperty(properties.lane_order_basis),
            visual_z_order: properties.visual_z_order,
          });
        }
      }
    }

    map.current.on("style.load", () => {
      if (!map.current) return;

      applyDarkMapTheme(map.current);

      // Native 3D buildings (fill-extrusion). Installed now over the
      // basemap; re-ordered below the subway lines once those load.
      ensureBuildingsLayer(map.current);
      ensureRouteStopLayers(map.current);
      // If the subway lane + stop data has already finished loading by
      // the time the basemap is ready, install the polyline + dot
      // layers now. Otherwise the data-loaded effect picks it up.
      if (subwayLanesRef.current && subwayStopsRef.current) {
        const beforeId = firstSymbolLayerId(map.current);
        const stationMarkers = subwayStationMarkersRef.current;
        void ensureMtaBulletImages(
          map.current,
          stationMarkerRouteIds(stationMarkers),
        );
        ensureSubwayNetworkLayers(
          map.current,
          beforeId,
          subwayLanesRef.current,
          subwayStopsRef.current,
          stationMarkers,
        );
        // Push native buildings BELOW the subway polylines now that the
        // subway glow/casing layers exist.
        syncBuildingsOrder();
      }
      mapReadyRef.current = true;
      setMapStyleVersion((version) => version + 1);

      onMapReady?.({
        recenter: () => {
          const origin = originRef.current;
          if (origin && map.current) {
            map.current.flyTo({ center: origin, zoom: 15.6, pitch: 0, bearing: 0, duration: 1500 });
          }
        },
        zoomIn: () => {
          map.current?.easeTo({ zoom: Math.min((map.current?.getZoom() ?? 14.5) + 0.8, 18.5), duration: 220 });
        },
        zoomOut: () => {
          map.current?.easeTo({ zoom: Math.max((map.current?.getZoom() ?? 14.5) - 0.8, 9.5), duration: 220 });
        },
        resetNorth: () => {
          map.current?.easeTo({ bearing: 0, pitch: 0, duration: 260 });
        },
      });
    });

    map.current.on("click", handleMapBackgroundClick);

    function syncCurrentLocationAccuracy() {
      if (!map.current || !markerElement.current || !originRef.current) return;
      updateCurrentLocationDot(markerElement.current, {
        lng: originRef.current[0],
        lat: originRef.current[1],
        zoom: map.current.getZoom(),
        accuracyMeters: originAccuracyRef.current,
      });
    }

    map.current.on("zoom", syncCurrentLocationAccuracy);
    map.current.on("zoomend", syncCurrentLocationAccuracy);

    function handlePosition(coords: {
      lng: number;
      lat: number;
      accuracyMeters?: number | null;
    }, fallback = false) {
      onLocationUpdateRef.current?.({
        lng: coords.lng,
        lat: coords.lat,
        fallback: fallback ? true : undefined,
      });
      originRef.current = [coords.lng, coords.lat];
      originAccuracyRef.current =
        typeof coords.accuracyMeters === "number" &&
        Number.isFinite(coords.accuracyMeters)
          ? coords.accuracyMeters
          : null;

      if (map.current && !initialFlyDoneRef.current) {
        initialFlyDoneRef.current = true;
        map.current.flyTo({
          center: [coords.lng, coords.lat],
          zoom: 15.6,
          pitch: 0,
          bearing: 0,
          duration: 2000,
        });
      }

      if (marker.current) {
        marker.current.setLngLat([coords.lng, coords.lat]);
      } else if (map.current) {
        markerElement.current = createCurrentLocationDot();
        marker.current = new maplibregl.Marker({
          element: markerElement.current,
          anchor: "center",
        })
          .setLngLat([coords.lng, coords.lat])
          .addTo(map.current);
      }
      syncCurrentLocationAccuracy();
    }

    let watchId: number | undefined;

    if (navigator.geolocation) {
      watchId = navigator.geolocation.watchPosition(
        (position) => {
          handlePosition({
            lng: position.coords.longitude,
            lat: position.coords.latitude,
            accuracyMeters: position.coords.accuracy,
          });
        },
        (error) => {
          if (error.code !== error.PERMISSION_DENIED) {
            // eslint-disable-next-line no-console
            console.warn("Geolocation unavailable:", error.message);
          }
          handlePosition({ ...DEFAULT_LOCATION, accuracyMeters: null }, true);
        },
        { enableHighAccuracy: true, maximumAge: 10000, timeout: 5000 },
      );
    } else {
      handlePosition(DEFAULT_LOCATION, true);
    }

    return () => {
      const currentMap = map.current;
      currentMap?.off("styleimagemissing", handleMissingBaseStyleImage);
      currentMap?.off("zoom", syncCurrentLocationAccuracy);
      currentMap?.off("zoomend", syncCurrentLocationAccuracy);
      currentMap?.remove();
      marker.current = null;
      markerElement.current = null;
      for (const waypointMarker of waypointMarkersRef.current) waypointMarker.remove();
      waypointMarkersRef.current = [];
      if (watchId !== undefined) navigator.geolocation.clearWatch(watchId);
    };
  }, [onMapReady]);

  useEffect(() => {
    let cancelled = false;
    const stationsPromise = fetch(artifactUrl("subway-network.stations.geojson"))
      .then((res) => {
        if (!res.ok) {
          throw new Error(
            `Failed to load stations: ${res.status} ${res.statusText}`,
          );
        }
        return res.json() as Promise<GeoJSON.FeatureCollection>;
      })
      .catch((error) => {
        if (DEBUG_LIVE_MAP) {
          // eslint-disable-next-line no-console
          console.warn(
            "[smart-route-map/subway-network] failed to load stations geometry",
            error,
          );
        }
        return null;
      });

    stationsPromise
      .then(async (stations) => {
        if (cancelled) return;

        // The visual network is the single subway renderer.
        const visual = await loadVisualSubwayNetworkOrNull();
        if (cancelled) return;
        const visualLanes = visual
          ? buildSubwayLaneFeaturesFromVisual(visual)
          : null;
        const lanes: GeoJSON.FeatureCollection = visualLanes ?? {
          type: "FeatureCollection",
          features: [],
        };
        subwayVisualModeActiveRef.current = visual != null;
        subwayVisualFallbackUsedRef.current = visual == null;
        subwayLanesRef.current = lanes;
        const stationAnchors = visual
          ? await loadSubwayStationAnchorsOrNull()
          : null;
        if (cancelled) return;
        subwayStationMarkersRef.current = stationAnchors
          ? splitStationAnchorFeatureCollections(stationAnchors)
          : null;

        if (subwayStationMarkersRef.current) {
          subwayStopsRef.current = { type: "FeatureCollection", features: [] };
        } else if (stations) {
          subwayStopsRef.current = buildSubwayStopFeatures(stations);
        } else {
          subwayStopsRef.current = { type: "FeatureCollection", features: [] };
        }
        // Bump version → triggers the layer-setup effect below.
        setSubwayLayerDataVersion((v) => v + 1);
      })
      .catch((error) => {
        if (DEBUG_LIVE_MAP) {
          // eslint-disable-next-line no-console
          console.warn("[smart-route-map/subway-network] failed to load geometry", error);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Add (or refresh) the subway polyline + stop-dot layers whenever the
  // network data finishes loading OR the map becomes ready. Both conditions
  // can fire in either order; this effect runs after either flips, and
  // ensureSubwayNetworkLayers is idempotent so calling it twice is fine.
  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;
    const lanes = subwayLanesRef.current;
    const stops = subwayStopsRef.current;
    if (!lanes || !stops) return;
    const beforeId = firstSymbolLayerId(map.current);
    const stationMarkers = subwayStationMarkersRef.current;
    void ensureMtaBulletImages(
      map.current,
      stationMarkerRouteIds(stationMarkers),
    );
    ensureSubwayNetworkLayers(map.current, beforeId, lanes, stops, stationMarkers);
    // Push native buildings below the subway polylines now that the subway
    // casing/glow layers exist.
    syncBuildingsOrder();
  }, [subwayLayerDataVersion, mapStyleVersion]);


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
      stopRotation({
        rotationTimeout: rotationTimeoutRef,
        rotationInterval: rotationIntervalRef,
      });
      stopAnimation();
    }

    function clearRouteFromMap() {
      clearBadges(stationMarkersRef.current);
      clearRouteStopData(m);
    }

    if (!routeData) {
      stopAll();
      clearRouteFromMap();
      return stopAll;
    }

    // Route active: render the FULL route statically and zoom out to frame it.
    // No animated path draw and no camera follow/rotation: the map stays
    // simple with the path, stop dots, and board/arrive endpoints framed in a
    // single zoom-out.
    stopAll();
    clearBadges(stationMarkersRef.current);
    const steps = routeData.steps;
    if (steps && steps.length > 0) {
      const { stepCoords } = buildTrips(steps);

      setRouteStopData(m, steps);
      // Only the first boarding stop gets a pill. The destination is already
      // carried by the arrival pin, so a final station pill adds clutter.
      // Rail modes (LIRR, Metro-North, PATH, light rail, tram) get the same
      // boarding pill as subway/bus; only the first transit boarding stop is
      // marked, and non-subway modes render as a colored letter badge.
      const transitSteps = steps.filter(isTransitStep);
      const boardingStop =
        transitSteps.length > 0
          ? {
              step: transitSteps[0],
              point: transitSteps[0].departure_coords,
              name: transitSteps[0].departure_stop,
            }
          : null;
      let badgeCount = 0;
      const badgeKeys = new Set<string>();
      for (const { step, point, name } of boardingStop ? [boardingStop] : []) {
        if (!point || !name) continue;
        const coords = toLngLat(point);
        const key = `${coords[0].toFixed(4)},${coords[1].toFixed(4)}`;
        if (badgeKeys.has(key)) continue;
        badgeKeys.add(key);
        const color =
          step.type === "BUS"
            ? "#0057B8"
            : step.line_color || getLineColor(step.train_line || "");
        const letter =
          step.train_line ||
          (step.type === "BUS" ? "BUS" : step.route_id || "?");
        const mk = addStationBadge(
          m,
          coords,
          name,
          letter,
          color,
          badgeCount++,
          step.type === "SUBWAY",
        );
        stationMarkersRef.current.push(mk);
      }
      bringRouteStopsToTop(m);

      // One zoom-out to frame the whole journey -- the route geometry plus the
      // user's actual location and the destination -- so both endpoints are
      // guaranteed in view, not just the transit polyline.
      const fitCoords = stepCoords.flat();
      if (originRef.current) fitCoords.push(originRef.current);
      for (const waypoint of routeData.itinerary?.waypoints ?? []) {
        const point = canonicalWaypointCoordinates(waypoint);
        if (point) fitCoords.push(point);
      }
      if (destCoords) fitCoords.push([destCoords.lng, destCoords.lat]);
      if (fitCoords.length > 0) {
        flyToRoute(m, fitCoords, { duration: 900, maxZoom: 16 });
      }
    }

    return stopAll;
  }, [routeData, destCoords, mobileSheetState, mapStyleVersion]);

  // Focus mode: hide the ambient subway network while a route is displayed
  // so the picked path reads as the hero. subwayLayerDataVersion re-applies
  // the hide after the async network layers (re)build.
  const routeActive = Boolean(routeData);
  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;
    setSubwayNetworkHidden(map.current, routeActive);
  }, [routeActive, subwayLayerDataVersion, mapStyleVersion]);

  // Destination marker — use primitive deps to avoid spurious re-runs
  const destLng = destCoords?.lng ?? null;
  const destLat = destCoords?.lat ?? null;

  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;
    if (routeData) {
      routePreviewFitKeyRef.current = null;
      return;
    }
    if (destLng == null || destLat == null || !isFinite(destLng) || !isFinite(destLat)) {
      routePreviewFitKeyRef.current = null;
      return;
    }

    const origin = originRef.current;
    if (!origin) return;

    const key = [
      origin[0].toFixed(5),
      origin[1].toFixed(5),
      destLng.toFixed(5),
      destLat.toFixed(5),
    ].join("|");
    if (routePreviewFitKeyRef.current === key) return;
    routePreviewFitKeyRef.current = key;
    flyToRoute(map.current, [origin, [destLng, destLat]], {
      duration: 760,
      maxZoom: 15.4,
    });
  }, [routeData, destLng, destLat, mobileSheetState]);

  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;

    if (destMarker.current) {
      destMarker.current.remove();
      destMarker.current = null;
    }

    if (destLng == null || destLat == null || !isFinite(destLng) || !isFinite(destLat)) {
      return;
    }

    const el = createDestinationPin();

    destMarker.current = new maplibregl.Marker({
      element: el,
      anchor: "bottom",
      offset: [0, 0],
    })
      .setLngLat([destLng, destLat])
      .addTo(map.current);

    return () => {
      if (destMarker.current) {
        destMarker.current.remove();
        destMarker.current = null;
      }
    };
  }, [destLng, destLat]);

  // A chained itinerary has real intermediate destinations, not transfer
  // stations. Render them separately from ordinary route-stop dots and retain
  // the canonical geometry for camera fitting above.
  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;
    for (const waypointMarker of waypointMarkersRef.current) waypointMarker.remove();
    waypointMarkersRef.current = [];

    if (!routeData?.itinerary) return;
    for (const waypoint of routeData.itinerary.waypoints ?? []) {
      const point = canonicalWaypointCoordinates(waypoint);
      if (!point) continue;
      const label = canonicalWaypointLabel(waypoint);
      if (!label) continue;
      const dwell = typeof waypoint.dwell_minutes === "number"
        ? waypoint.dwell_minutes
        : undefined;
      const marker = new maplibregl.Marker({
        element: createWaypointMarker(label, dwell),
        anchor: "left",
        offset: [10, 0],
      })
        .setLngLat(point)
        .addTo(map.current);
      waypointMarkersRef.current.push(marker);
    }
    return () => {
      for (const waypointMarker of waypointMarkersRef.current) waypointMarker.remove();
      waypointMarkersRef.current = [];
    };
  }, [routeData, mapStyleVersion]);

  return (
    <div ref={mapContainer} className="absolute inset-0 w-full h-full" />
  );
}

function canonicalWaypointCoordinates(waypoint: {
  lat?: number | null;
  lng?: number | null;
  latitude?: number | null;
  longitude?: number | null;
}): [number, number] | null {
  const lat = waypoint.lat ?? waypoint.latitude;
  const lng = waypoint.lng ?? waypoint.longitude;
  return typeof lat === "number" && Number.isFinite(lat) &&
    typeof lng === "number" && Number.isFinite(lng)
    ? [lng, lat]
    : null;
}

function canonicalWaypointLabel(waypoint: {
  display_name?: string | null;
  label?: string | null;
  name?: string | null;
  address?: string | null;
}): string | null {
  for (const value of [waypoint.display_name, waypoint.label, waypoint.name, waypoint.address]) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}
