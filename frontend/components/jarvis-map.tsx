"use client";

import { useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Layer } from "@deck.gl/core";
import { MapboxOverlay } from "@deck.gl/mapbox";

import type { TransitRouteData, Coordinates, LiveVehicle } from "@/types";
import { DEFAULT_LOCATION } from "@/lib/api";
import { createOrb, createOrbMarker } from "./map/orbs";
import { flyToRoute, stopRotation } from "./map/camera";
import { addStationBadge, clearBadges } from "./map/station-badges";
import { buildTrips, getLineColor, type Trip } from "./map/route-layers";
import { ensureBuildingsLayer, BUILDINGS_LAYER_ID } from "./map/buildings-layer";
import {
  ensureIncidentMapLibreLayers,
  INCIDENT_MAPLIBRE_LAYER_ID,
  setIncidentMapLibreData,
} from "./map/incidents/incident-maplibre-layer";
import {
  incidentFeatureToPopupViewModel,
  renderIncidentPopupHtml,
} from "./map/incidents/incident-popup";
import type { MapIncident } from "./map/incidents/incident-marker-types";
import {
  buildSubwayLaneFeaturesFromVisual,
  buildSubwayStopFeatures,
  emptySubwayStationMarkerCollections,
  ensureMtaBulletImages,
  ensureSubwayNetworkLayers,
  splitStationAnchorFeatureCollections,
  stationMarkerRouteIds,
  summarizeVisualLanes,
  setSubwayNetworkHidden,
  SUBWAY_CASING_LAYER_ID,
  SUBWAY_FILL_LAYER_ID,
  SUBWAY_GLOW_LAYER_ID,
  type SubwayStationMarkerCollections,
} from "./map/subway-network";
import {
  bringRouteStopsToTop,
  clearRouteStopData,
  ensureRouteStopLayers,
  setRouteStopData,
  ROUTE_WALK_LINE_LAYER_ID,
} from "./map/route-stops";

import {
  toLngLat,
  artifactUrl,
  mapFeatureArrayProperty,
  selectedRouteLayers,
  firstSymbolLayerId,
  ensureLiveTrainLayers,
  setLiveTrainLayerData,
  clearLiveTrainLayerData,
  distanceMeters,
  projectPointToIndexedLine,
  sliceIndexedLineBetween,
  syncRailTargetVisual,
  buildLiveTrainFeatures,
  buildSubwayNetworkIndex,
  resolveVehicleTrack,
  isRailTrackTarget,
  deconflictVehicleTargets,
  speedLabel,
  isExpiredStaleVehicle,
  dedupVehiclesByTripId,
  stabilizeTrackTarget,
  vehicleSnapshotKey,
  setMarkerBearing,
  renderTrainMarker,
  disposeVehicleMarker,
  animateMarkerAlong,
  setMarkerImmediately,
  LIVE_TRAIN_SOURCE_ID,
  ROUTE_SNAP_MAX_METERS,
  DEBUG_LIVE_MAP,
  loadCanonicalSubwayNetwork,
  loadVisualSubwayNetworkOrNull,
  loadSubwayStationAnchorsOrNull,
  LIVE_TRAIN_JITTER_METERS,
  LIVE_TRAIN_ANIMATION_MS,
  LIVE_TRAIN_REPOSITION_METERS,
  type VehicleMarkerEntry,
  type RailTrackTarget,
  type ResolvedVehicleTarget,
  type SubwayNetworkIndex,
} from "./jarvis-map-helpers";
interface JarvisMapProps {
  onLocationUpdate?: (coords: { lng: number; lat: number }) => void;
  routeData?: TransitRouteData | null;
  isSpeaking?: boolean;
  destCoords?: { lat: number; lng: number } | null;
  mode?: "planner" | "liveFeed";
  vehicles?: LiveVehicle[];
  liveVehicleScopeKey?: string;
  focusedRouteIds?: string[];
  incidentRouteIds?: string[];
  incidents?: MapIncident[];
  /** Called with map controls once the map is ready */
  onMapReady?: (actions: {
    recenter: () => void;
    zoomIn: () => void;
    zoomOut: () => void;
    resetNorth: () => void;
    focusIncident: (incident: MapIncident) => void;
  }) => void;
}

export function JarvisMap({
  onLocationUpdate,
  routeData,
  isSpeaking,
  destCoords,
  mode = "planner",
  vehicles = [],
  liveVehicleScopeKey,
  incidents = [],
  onMapReady,
}: JarvisMapProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const marker = useRef<maplibregl.Marker | null>(null);
  const destMarker = useRef<maplibregl.Marker | null>(null);
  const onLocationUpdateRef = useRef(onLocationUpdate);
  const mapReadyRef = useRef(false);
  const rotationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const rotationTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const originRef = useRef<[number, number] | null>(null);
  const stationMarkersRef = useRef<maplibregl.Marker[]>([]);
  const vehicleMarkersRef = useRef<Map<string, VehicleMarkerEntry>>(new Map());
  const liveTrainLayerFrameRef = useRef<number | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);
  const [subwayNetworkIndex, setSubwayNetworkIndex] = useState<SubwayNetworkIndex>({});
  const mapZoomRef = useRef(14.5);
  const initialFlyDoneRef = useRef(false);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const routeDeckLayersRef = useRef<Layer[]>([]);
  const subwayLanesRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const subwayStopsRef = useRef<GeoJSON.FeatureCollection | null>(null);
  const subwayStationMarkersRef =
    useRef<SubwayStationMarkerCollections | null>(null);
  const subwayVisualModeActiveRef = useRef(false);
  const subwayVisualFallbackUsedRef = useRef(false);
  const [subwayLayerDataVersion, setSubwayLayerDataVersion] = useState(0);
  const incidentsRef = useRef<MapIncident[]>([]);
  const incidentPopupRef = useRef<maplibregl.Popup | null>(null);
  const rotationRefs = {
    rotationTimeout: rotationTimeoutRef,
    rotationInterval: rotationIntervalRef,
  };

  // The deck overlay now carries ONLY the route path layers (buildings moved
  // to a native MapLibre fill-extrusion). Most of the time this is empty, so
  // the interleaved overlay costs almost nothing during pan.
  const syncDeckOverlay = () => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    overlay.setProps({ layers: [...routeDeckLayersRef.current] });
  };

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

  const setRouteDeckLayers = (layers: Layer[]) => {
    routeDeckLayersRef.current = layers;
    syncDeckOverlay();
  };

  function focusIncidentOnMap(incident: MapIncident) {
    if (!map.current || !Number.isFinite(incident.lon) || !Number.isFinite(incident.lat)) {
      return;
    }

    const coordinates: [number, number] = [incident.lon, incident.lat];
    map.current.flyTo({
      center: coordinates,
      zoom: Math.max(map.current.getZoom(), 15.7),
      pitch: 0,
      bearing: map.current.getBearing(),
      duration: 850,
    });

    const model = incidentFeatureToPopupViewModel({
      id: incident.id,
      incident_type: incident.type,
      title: incident.title,
      description: incident.description,
      station: incident.station,
      route_ids: incident.routeIds?.join(","),
      active: incident.active,
      source: "ATLAS INTEL",
      time_ago_sec: 0,
    });

    incidentPopupRef.current?.remove();
    const popup = new maplibregl.Popup({
      anchor: "bottom-left",
      className: "sr-incident-maplibre-popup",
      closeButton: false,
      closeOnClick: false,
      maxWidth: "320px",
      offset: [24, -50],
    })
      .setLngLat(coordinates)
      .setHTML(renderIncidentPopupHtml(model))
      .addTo(map.current);

    incidentPopupRef.current = popup;
  }

  function requestLiveTrainLayerSync() {
    if (liveTrainLayerFrameRef.current != null) return;
    liveTrainLayerFrameRef.current = requestAnimationFrame(() => {
      liveTrainLayerFrameRef.current = null;
      if (!map.current || !mapReadyRef.current || mode !== "liveFeed") return;
      setLiveTrainLayerData(
        map.current,
        buildLiveTrainFeatures(vehicleMarkersRef.current, mapZoomRef.current),
      );
    });
  }

  useEffect(() => {
    onLocationUpdateRef.current = onLocationUpdate;
  }, [onLocationUpdate]);

  // Map initialization
  useEffect(() => {
    if (mode === "liveFeed") {
      setSelectedVehicleId(null);
    }
  }, [mode, liveVehicleScopeKey]);

  useEffect(() => {
    if (!mapContainer.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
      center: [DEFAULT_LOCATION.lng, DEFAULT_LOCATION.lat],
      zoom: 14.5,
      pitch: 45,
      bearing: 0,
      // antialias OFF: MSAA on the whole WebGL canvas is a heavy fragment
      // cost (multiplied by devicePixelRatio) for negligible gain on vector
      // content, which already has its own edge AA. Pan-smoothness win.
      canvasContextAttributes: { antialias: false },
    });

    // QA debug handle: dev-only exposure for Playwright-driven route QA. Gated by
    // process.env.NODE_ENV !== "production" AND URL param `qa-map=1`. Never set
    // in production; ignored by normal app behavior; reads only the URL once at
    // map construction time, so subsequent navigation cannot toggle it on.
    if (typeof window !== "undefined" && process.env.NODE_ENV !== "production") {
      try {
        const qaParams = new URLSearchParams(window.location.search);
        if (qaParams.get("qa-map") === "1") {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (window as any).__jarvisMap = map.current;
          // eslint-disable-next-line no-console
          console.info("[jarvis-map/qa-map] window.__jarvisMap exposed");
        }
      } catch {
        // URLSearchParams or window access guarded -- safe to silently skip.
      }
    }

    function setMapCursor(cursor: string) {
      const canvas = map.current?.getCanvas();
      if (canvas) canvas.style.cursor = cursor;
    }

    function openIncidentPopup(
      feature?: maplibregl.MapGeoJSONFeature,
      fallbackLngLat?: maplibregl.LngLat,
    ) {
      if (!map.current || !feature?.properties) return;

      const model = incidentFeatureToPopupViewModel(feature.properties);
      const coordinates: [number, number] | null =
        feature.geometry.type === "Point"
          ? (feature.geometry.coordinates as [number, number])
          : fallbackLngLat
            ? [fallbackLngLat.lng, fallbackLngLat.lat]
            : null;
      if (!coordinates) return;

      incidentPopupRef.current?.remove();
      const popup = new maplibregl.Popup({
        anchor: "bottom-left",
        className: "sr-incident-maplibre-popup",
        closeButton: false,
        closeOnClick: false,
        maxWidth: "320px",
        offset: [24, -50],
      })
        .setLngLat(coordinates)
        .setHTML(renderIncidentPopupHtml(model))
        .addTo(map.current);

      incidentPopupRef.current = popup;
    }

    function handleIncidentClick(event: maplibregl.MapLayerMouseEvent) {
      const feature = event.features?.[0];
      openIncidentPopup(feature, event.lngLat);
    }

    function handleMapBackgroundClick(event: maplibregl.MapMouseEvent) {
      if (!map.current) return;
      const layers = [
        INCIDENT_MAPLIBRE_LAYER_ID,
      ].filter((layerId) => Boolean(map.current?.getLayer(layerId)));
      const hits =
        layers.length > 0
          ? map.current.queryRenderedFeatures(event.point, { layers })
          : [];

      if (hits.length === 0) {
        if (
          subwayVisualModeActiveRef.current &&
          !subwayVisualFallbackUsedRef.current &&
          map.current.getLayer(SUBWAY_FILL_LAYER_ID)
        ) {
          const visualHits = map.current.queryRenderedFeatures(event.point, {
            layers: [SUBWAY_FILL_LAYER_ID],
          });
          const feature = visualHits[0];
          if (feature?.properties) {
            const properties = feature.properties;
            console.info("[jarvis-map/subway-visual-click]", {
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
            return;
          }
        }
        setSelectedVehicleId(null);
        incidentPopupRef.current?.remove();
        incidentPopupRef.current = null;
      }
    }

    map.current.on("style.load", () => {
      if (!map.current) return;

      // Gotham-noir basemap. Recolor the CARTO Dark Matter ground into a deep
      // indigo night-city: charcoal-indigo land in shadow, a readable steel-blue
      // harbour, noir-green parks, streets with a cold gleam -- all still quieter
      // than the colored transit lines so they stay the loudest thing. Pure paint
      // overrides on the existing
      // base layers (matched by type + id, since CARTO's ids live in the remote
      // style.json) -- delete this loop to restore stock Dark Matter. Each layer
      // is wrapped so a paint prop missing on a future CARTO revision can't throw.
      for (const layer of map.current.getStyle().layers ?? []) {
        const id = layer.id;
        const type = layer.type;
        try {
          if (
            type === "line" &&
            /road|street|tunnel|bridge|motorway|trunk|primary|secondary|tertiary/i.test(id)
          ) {
            // Streets catch a cold steel-blue gleam -- visible, still quiet under
            // the transit lines.
            map.current.setPaintProperty(id, "line-color", "#2B3A4D");
            map.current.setPaintProperty(id, "line-opacity", 0.55);
          } else if (type === "fill" && /water|ocean|river|bay/i.test(id)) {
            // Gotham harbour: lifted from near-black to a readable deep steel-blue.
            map.current.setPaintProperty(id, "fill-color", "#1B3A52");
          } else if (
            type === "fill" &&
            /park|wood|grass|forest|cemetery/i.test(id)
          ) {
            // Parks + grass read as a clear dark forest green -- the Dark
            // Knight's green lungs in the indigo city.
            map.current.setPaintProperty(id, "fill-color", "#1C4327");
            map.current.setPaintProperty(id, "fill-opacity", 0.72);
          } else if (
            type === "fill" &&
            /land|landuse|sand/i.test(id)
          ) {
            // Charcoal-indigo land blocks: the city's deep shadow.
            map.current.setPaintProperty(id, "fill-color", "#161E2E");
            map.current.setPaintProperty(id, "fill-opacity", 0.66);
          } else if (type === "background") {
            map.current.setPaintProperty(id, "background-color", "#0D1220");
          } else if (type === "symbol") {
            if (/poi/i.test(id)) {
              map.current.setLayoutProperty(id, "visibility", "none");
            } else {
              map.current.setPaintProperty(id, "text-opacity", 0.55);
              map.current.setPaintProperty(id, "text-halo-color", "#05070A");
            }
          }
        } catch {
          // This CARTO style revision lacks the targeted paint prop; skip.
        }
      }

      const overlay = new MapboxOverlay({
        interleaved: true,
        layers: [],
      });
      map.current.addControl(overlay as unknown as maplibregl.IControl);
      overlayRef.current = overlay;
      // Native 3D buildings (fill-extrusion). Installed now over the
      // basemap; re-ordered below the subway lines once those load.
      ensureBuildingsLayer(map.current);
      ensureLiveTrainLayers(map.current);
      ensureIncidentMapLibreLayers(map.current);
      setIncidentMapLibreData(map.current, incidentsRef.current);
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
      map.current.on("click", INCIDENT_MAPLIBRE_LAYER_ID, handleIncidentClick);
      map.current.on("mouseenter", INCIDENT_MAPLIBRE_LAYER_ID, () => setMapCursor("pointer"));
      map.current.on("mouseleave", INCIDENT_MAPLIBRE_LAYER_ID, () => setMapCursor(""));
      syncDeckOverlay();

      mapReadyRef.current = true;

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
        focusIncident: focusIncidentOnMap,
      });
    });

    map.current.on("click", handleMapBackgroundClick);

    map.current.on("zoomend", () => {
      if (map.current) {
        // Native buildings handle their own minzoom; no manual gating.
        mapZoomRef.current = map.current.getZoom();
        requestLiveTrainLayerSync();
      }
    });

    function handlePosition(coords: { lng: number; lat: number }) {
      onLocationUpdateRef.current?.(coords);
      originRef.current = [coords.lng, coords.lat];

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
          if (error.code !== error.PERMISSION_DENIED) {
            console.warn("Geolocation unavailable:", error.message);
          }
          handlePosition(DEFAULT_LOCATION);
        },
        { enableHighAccuracy: true, maximumAge: 10000, timeout: 5000 },
      );
    } else {
      handlePosition(DEFAULT_LOCATION);
    }

    return () => {
      vehicleMarkersRef.current.forEach((entry) => {
        disposeVehicleMarker(entry);
      });
      vehicleMarkersRef.current.clear();
      if (liveTrainLayerFrameRef.current != null) {
        cancelAnimationFrame(liveTrainLayerFrameRef.current);
        liveTrainLayerFrameRef.current = null;
      }
      incidentPopupRef.current?.remove();
      incidentPopupRef.current = null;
      if (map.current && map.current.getSource(LIVE_TRAIN_SOURCE_ID)) {
        clearLiveTrainLayerData(map.current);
      }
      map.current?.remove();
      if (watchId) navigator.geolocation.clearWatch(watchId);
    };
  }, []);

  useEffect(() => {
    incidentsRef.current = incidents;
    if (!map.current || !mapReadyRef.current) return;
    ensureIncidentMapLibreLayers(map.current);
    setIncidentMapLibreData(map.current, incidents);
  }, [incidents]);

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
          console.warn(
            "[jarvis-map/subway-network] failed to load stations geometry",
            error,
          );
        }
        return null;
      });

    Promise.all([loadCanonicalSubwayNetwork(), stationsPromise])
      .then(async ([canonical, stations]) => {
        if (cancelled) return;
        setSubwayNetworkIndex(buildSubwayNetworkIndex(canonical));

        // The visual network is the single subway renderer. The canonical
        // geometry above is kept only to index live-train snapping; it is
        // never drawn.
        const visual = await loadVisualSubwayNetworkOrNull();
        if (cancelled) return;
        const visualLanes = visual
          ? buildSubwayLaneFeaturesFromVisual(visual)
          : null;
        const lanes: GeoJSON.FeatureCollection = visualLanes ?? {
          type: "FeatureCollection",
          features: [],
        };
        const visualSummary = visualLanes
          ? summarizeVisualLanes(visualLanes)
          : null;

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

        if (DEBUG_LIVE_MAP) {
          console.info("[jarvis-map/subway-network] loaded geometry", {
            canonicalFeatures: canonical.features.length,
            laneFeatures: lanes.features.length,
            stopDotFeatures: subwayStopsRef.current.features.length,
            stationAnchorFeatures: stationAnchors?.features.length ?? 0,
            visualFeaturesLoaded: visual?.features.length ?? 0,
            visualRenderFeaturesEmitted: visualSummary?.renderFeatures ?? 0,
            visualDistinctRoutesRepresented: visualSummary?.distinctRoutes ?? 0,
            visualDistinctColorLanesEmitted:
              visualSummary?.distinctColorLanes ?? 0,
            visualDistinctColorGroups: visualSummary?.distinctColorGroups ?? 0,
            visualCorridorsWithMultipleRoutes:
              visualSummary?.corridorsWithMultipleRoutes ?? 0,
            visualMultiColorCorridors: visualSummary?.multiColorCorridors ?? 0,
          });
        }
      })
      .catch((error) => {
        if (DEBUG_LIVE_MAP) {
          console.warn("[jarvis-map/subway-network] failed to load canonical geometry", error);
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
  }, [subwayLayerDataVersion]);

  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;
    ensureLiveTrainLayers(map.current);

    const markers = vehicleMarkersRef.current;
    if (mode !== "liveFeed") {
      markers.forEach((entry) => {
        disposeVehicleMarker(entry);
      });
      markers.clear();
      clearLiveTrainLayerData(map.current);
      setSelectedVehicleId(null);
      return;
    }

    const nextIds = new Set<string>();
    let created = 0;
    let updated = 0;
    let skippedInvalid = 0;
    let skippedNoTrack = 0;
    let fallbackTargets = 0;
    let skippedExpired = 0;
    let railAnimations = 0;
    let fallbackAnimations = 0;
    let railRepositions = 0;
    let jitterClamps = 0;
    let backtrackClamps = 0;
    const nowSeconds = Math.floor(Date.now() / 1000);
    const dedupedVehicles = dedupVehiclesByTripId(vehicles);
    const resolvedVehicles: ResolvedVehicleTarget[] = [];
    for (const vehicle of dedupedVehicles) {
      if (!isFinite(vehicle.lng) || !isFinite(vehicle.lat)) {
        skippedInvalid++;
        continue;
      }
      if (isExpiredStaleVehicle(vehicle, nowSeconds)) {
        skippedExpired++;
        continue;
      }
      const id = vehicle.id || `${vehicle.route_id}-${vehicle.trip_id || vehicle.stop_id || "vehicle"}`;
      const target = resolveVehicleTrack(vehicle, subwayNetworkIndex);
      if (!isRailTrackTarget(target)) fallbackTargets++;
      resolvedVehicles.push({
        id,
        vehicle,
        target,
      });
    }
    deconflictVehicleTargets(resolvedVehicles, mapZoomRef.current);

    for (const { vehicle, id, target } of resolvedVehicles) {
      nextIds.add(id);
      const targetIsRail = isRailTrackTarget(target);
      let effectiveTarget = targetIsRail ? syncRailTargetVisual(target) : target;

      const existing = markers.get(id);
      if (existing) {
        existing.onFrame = requestLiveTrainLayerSync;
        let stabilized:
          | { target: RailTrackTarget; forceReposition: boolean; clampedBacktrack: boolean }
          | null = null;
        if (targetIsRail) {
          stabilized = stabilizeTrackTarget(existing, target, vehicle);
          effectiveTarget = syncRailTargetVisual(stabilized.target);
          if (stabilized.clampedBacktrack) {
            backtrackClamps++;
          }
        }
        const snapshotKey = vehicleSnapshotKey(vehicle, effectiveTarget);
        const current = existing.currentLngLat;
        const visualDistance = distanceMeters(current, effectiveTarget.lngLat);
        const targetDistance = distanceMeters(existing.targetLngLat, effectiveTarget.lngLat);
        const snapshotChanged =
          existing.lastSnapshotKey !== snapshotKey ||
          targetDistance > LIVE_TRAIN_JITTER_METERS;
        const speed = snapshotChanged
          ? speedLabel(current, effectiveTarget.lngLat, existing.lastTimestamp, vehicle.timestamp)
          : existing.lastSpeedLabel || "-- MPH";
        if (!existing.frame) {
          setMarkerBearing(existing, effectiveTarget.bearing);
        }
        existing.vehicle = vehicle;
        existing.lastTripId = vehicle.trip_id;
        existing.direction = effectiveTarget.direction ?? existing.direction ?? 1;
        existing.marker.getElement().dataset.stale = String(vehicle.stale);
        existing.marker.getElement().title = `${vehicle.route_id} train${vehicle.stale ? " - stale position" : ""}`;
        const selectCb = () => setSelectedVehicleId((currentId) => (currentId === id ? null : id));
        renderTrainMarker(existing, vehicle, selectedVehicleId === id, speed, selectCb);
        if (vehicle.stale) {
          if (existing.frame) cancelAnimationFrame(existing.frame);
          existing.frame = undefined;
          existing.lastSnapshotKey = snapshotKey;
          existing.lastSpeedLabel = "0 MPH";
          requestLiveTrainLayerSync();
        } else if (!targetIsRail) {
          existing.lastSnapshotKey = snapshotKey;
          existing.lastSpeedLabel = speed;
          existing.lastTimestamp = vehicle.timestamp;
          if (!snapshotChanged || visualDistance <= LIVE_TRAIN_JITTER_METERS) {
            setMarkerImmediately(existing, effectiveTarget, false);
          } else {
            animateMarkerAlong(
              existing,
              effectiveTarget,
              [current, effectiveTarget.lngLat],
              LIVE_TRAIN_ANIMATION_MS,
            );
            fallbackAnimations++;
          }
        } else if (snapshotChanged) {
          if (!isRailTrackTarget(effectiveTarget)) {
            existing.lastSnapshotKey = snapshotKey;
            existing.lastSpeedLabel = speed;
            existing.lastTimestamp = vehicle.timestamp;
            setMarkerImmediately(existing, effectiveTarget, false);
            updated++;
            continue;
          }
          const railTarget = effectiveTarget;
          existing.lastSnapshotKey = snapshotKey;
          existing.lastSpeedLabel = speed;
          existing.lastTimestamp = vehicle.timestamp;

          if (stabilized?.clampedBacktrack || visualDistance <= LIVE_TRAIN_JITTER_METERS) {
            setMarkerImmediately(existing, effectiveTarget, false);
            jitterClamps++;
          } else if (stabilized?.forceReposition || visualDistance >= LIVE_TRAIN_REPOSITION_METERS) {
            setMarkerImmediately(existing, effectiveTarget, true);
            railRepositions++;
          } else {
            let path: [number, number][] | null = null;
            // Prefer currentMeasure on the same line — it's the logical
            // progress already computed on this polyline, guaranteed to sit
            // on the line. Re-projecting the interpolated mid-animation
            // pixel can snap to the wrong segment on curves. On a line
            // change we try re-projection, and if that fails the distance
            // cap we still fall back to the prior measure rather than
            // collapsing to a fade-reposition.
            let fromMeasure: number | undefined;
            const sameLineId = existing.routeLine?.id === railTarget.line.id;
            if (sameLineId && existing.currentMeasure != null) {
              fromMeasure = existing.currentMeasure;
            } else {
              const projection = projectPointToIndexedLine(current, railTarget.line);
              if (projection && projection.distance <= ROUTE_SNAP_MAX_METERS) {
                fromMeasure = projection.distanceAlong;
              } else if (existing.currentMeasure != null) {
                fromMeasure = existing.currentMeasure;
              }
            }

            if (fromMeasure != null) {
              path = sliceIndexedLineBetween(railTarget.line, fromMeasure, railTarget.measure);
            }

            if (!path || path.length < 2) {
              setMarkerImmediately(existing, railTarget, true);
              railRepositions++;
            } else {
              animateMarkerAlong(existing, railTarget, path, LIVE_TRAIN_ANIMATION_MS, fromMeasure);
              railAnimations++;
            }
          }
        } else {
          requestLiveTrainLayerSync();
        }
        updated++;
        continue;
      }

      const el = document.createElement("div");
      el.className = "sr-train-marker";
      el.dataset.stale = String(vehicle.stale);
      el.title = `${vehicle.route_id} train${vehicle.stale ? " - stale position" : ""}`;
      const newMarker = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat(effectiveTarget.lngLat)
        .addTo(map.current);
      const selectCb = () => setSelectedVehicleId((current) => (current === id ? null : id));
      const entry: VehicleMarkerEntry = {
        marker: newMarker,
        root: createRoot(el),
        currentLngLat: effectiveTarget.lngLat,
        targetLngLat: effectiveTarget.lngLat,
        bearing: effectiveTarget.bearing,
        lastTimestamp: vehicle.timestamp,
        lastSnapshotKey: vehicleSnapshotKey(vehicle, effectiveTarget),
        lastSpeedLabel: "-- MPH",
        routeLine: effectiveTarget.line,
        currentMeasure: effectiveTarget.measure,
        targetMeasure: effectiveTarget.measure,
        rawMeasure: effectiveTarget.rawMeasure ?? effectiveTarget.measure,
        direction: effectiveTarget.direction ?? 1,
        lastTripId: vehicle.trip_id,
        missingSnapshots: 0,
        onFrame: requestLiveTrainLayerSync,
        vehicle,
      };
      renderTrainMarker(
        entry,
        vehicle,
        selectedVehicleId === id,
        "-- MPH",
        selectCb,
      );
      markers.set(id, entry);
      requestLiveTrainLayerSync();
      created++;
    }

    let selectedStillExists = false;
    let removed = 0;
    markers.forEach((entry, id) => {
      if (!nextIds.has(id)) {
        disposeVehicleMarker(entry);
        markers.delete(id);
        removed++;
        return;
      }
      if (id === selectedVehicleId) selectedStillExists = true;
    });
    if (selectedVehicleId && !selectedStillExists) {
      setSelectedVehicleId(null);
    }
    if (removed > 0) {
      requestLiveTrainLayerSync();
    }
    if (DEBUG_LIVE_MAP) {
      console.info("[jarvis-map/live-vehicles] marker sync", JSON.stringify({
        inputVehicles: vehicles.length,
        activeMarkers: markers.size,
        created,
        updated,
        removed,
        skippedInvalid,
        skippedNoTrack,
        fallbackTargets,
        skippedExpired,
        railAnimations,
        fallbackAnimations,
        railRepositions,
        jitterClamps,
        backtrackClamps,
        selectedVehicleId,
        sampleVehicles: vehicles.slice(0, 3).map((vehicle) => ({
          id: vehicle.id,
          route_id: vehicle.route_id,
          lat: vehicle.lat,
          lng: vehicle.lng,
          stale: vehicle.stale,
          position_source: vehicle.position_source,
          segment: vehicle.segment,
        })),
      }, null, 2));
    }
  }, [mode, vehicles, selectedVehicleId, subwayNetworkIndex]);

  // Route animation + camera rotation
  useEffect(() => {
    if (!map.current || !mapReadyRef.current || !overlayRef.current) return;

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
    }

    function clearRouteFromMap() {
      clearBadges(stationMarkersRef.current);
      clearRouteStopData(m);
      setRouteDeckLayers([]);
    }

    if (!routeData) {
      stopAll();
      clearRouteFromMap();
      return stopAll;
    }

    // Route active: render the FULL route statically and zoom out to frame it.
    // No animated path draw, no camera follow/rotation -- narration audio still
    // plays via isSpeaking, but the map stays simple: the path, its stop dots,
    // and the board/arrive endpoints, fit in a single zoom-out.
    stopAll();
    clearBadges(stationMarkersRef.current);
    const steps = routeData.steps;
    if (steps && steps.length > 0) {
      const { trips, stepCoords } = buildTrips(steps);
      // WALK segments render as a dashed MapLibre line instead.
      setRouteDeckLayers(selectedRouteLayers(trips.filter((t) => t.type !== "WALK")));

      setRouteStopData(m, steps);
      // Only the trip's true endpoints get a roundel chip -- where you first
      // board and where you finally arrive. Transfers stay as plain stop dots
      // + labels so the heavy badges don't pile up and bury the dots.
      const transitSteps = steps.filter(
        (s) => s.type === "SUBWAY" || s.type === "BUS",
      );
      const endpointSteps =
        transitSteps.length > 0
          ? [
              { step: transitSteps[0], point: transitSteps[0].departure_coords, name: transitSteps[0].departure_stop },
              {
                step: transitSteps[transitSteps.length - 1],
                point: transitSteps[transitSteps.length - 1].arrival_coords,
                name: transitSteps[transitSteps.length - 1].arrival_stop,
              },
            ]
          : [];
      let badgeCount = 0;
      const badgeKeys = new Set<string>();
      for (const { step, point, name } of endpointSteps) {
        if (!point || !name) continue;
        const coords = toLngLat(point);
        const key = `${coords[0].toFixed(4)},${coords[1].toFixed(4)}`;
        if (badgeKeys.has(key)) continue;
        badgeKeys.add(key);
        const color = step.type === "SUBWAY"
          ? (step.line_color || getLineColor(step.train_line || ""))
          : "#0057B8";
        const letter = step.train_line || (step.type === "BUS" ? "BUS" : "?");
        const mk = addStationBadge(m, coords, name, letter, color, badgeCount++, step.type === "SUBWAY");
        stationMarkersRef.current.push(mk);
      }
      bringRouteStopsToTop(m);

      // One zoom-out to frame the whole journey -- the route geometry plus the
      // user's actual location and the destination -- so both endpoints are
      // guaranteed in view, not just the transit polyline.
      const fitCoords = stepCoords.flat();
      if (originRef.current) fitCoords.push(originRef.current);
      if (destCoords) fitCoords.push([destCoords.lng, destCoords.lat]);
      if (fitCoords.length > 0) flyToRoute(m, fitCoords);
    }

    return stopAll;
  }, [routeData, destCoords]);

  // Focus mode: hide the ambient subway network while a route is displayed
  // so the picked path reads as the hero. subwayLayerDataVersion re-applies
  // the hide after the async network layers (re)build.
  const routeActive = Boolean(routeData);
  useEffect(() => {
    if (!map.current || !mapReadyRef.current) return;
    setSubwayNetworkHidden(map.current, routeActive);
  }, [routeActive, subwayLayerDataVersion]);

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

    destMarker.current = new maplibregl.Marker({ element: el, anchor: "center" })
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
