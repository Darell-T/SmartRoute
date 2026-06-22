import maplibregl from "maplibre-gl";
import type { IncidentSeverity, IncidentType, MapIncident } from "./incident-marker-types";
import {
  INCIDENT_MARKER_TOKENS,
  INCIDENT_PRIORITY,
  normalizeIncidentType,
  shouldPulseIncident,
} from "./incident-marker-tokens";
import {
  buildIncidentMarkerSvg,
  INCIDENT_MARKER_BASE_HEIGHT,
  INCIDENT_MARKER_BASE_WIDTH,
} from "./incident-marker-artwork";

export const INCIDENT_MAPLIBRE_SOURCE_ID = "sr-map-incidents";
export const INCIDENT_MAPLIBRE_HALO_LAYER_ID = "sr-map-incident-halos";
export const INCIDENT_MAPLIBRE_LAYER_ID = "sr-map-incident-markers";
export const INCIDENT_MAPLIBRE_IMAGE_PREFIX = "sr-incident-marker";

type IncidentFeatureProperties = {
  id: string;
  incident_type: IncidentType;
  marker_image: string;
  title: string;
  description?: string;
  station?: string;
  route_ids?: string;
  active: boolean;
  severity?: IncidentSeverity;
  category: string;
  source: string;
  time_ago_sec: number;
  hue: string;
  critical: boolean;
  pulse: boolean;
  priority: number;
};

type IncidentFeatureCollection = GeoJSON.FeatureCollection<
  GeoJSON.Point,
  IncidentFeatureProperties
>;

const INCIDENT_IMAGE_SIZE = {
  width: INCIDENT_MARKER_BASE_WIDTH,
  height: INCIDENT_MARKER_BASE_HEIGHT,
  pixelRatio: 2,
};

function isFiniteCoord(lon: number, lat: number) {
  return Number.isFinite(lon) && Number.isFinite(lat);
}

function incidentImageId(type: IncidentType) {
  return `${INCIDENT_MAPLIBRE_IMAGE_PREFIX}-${type}`;
}

function incidentMarkerDataUrl(type: IncidentType) {
  const imageId = incidentImageId(type);
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
    buildIncidentMarkerSvg(type, {
      size: INCIDENT_IMAGE_SIZE.width,
      uid: imageId,
      withPulse: false,
    }),
  )}`;
}

function loadIncidentMarkerImage(m: maplibregl.Map, type: IncidentType) {
  const imageId = incidentImageId(type);
  if (m.hasImage(imageId)) return Promise.resolve();

  return new Promise<void>((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = INCIDENT_IMAGE_SIZE.width * INCIDENT_IMAGE_SIZE.pixelRatio;
        canvas.height = INCIDENT_IMAGE_SIZE.height * INCIDENT_IMAGE_SIZE.pixelRatio;
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
          if (!m.hasImage(imageId)) {
            m.addImage(imageId, data, { pixelRatio: INCIDENT_IMAGE_SIZE.pixelRatio });
          }
        }
      } catch {
        // If SVG rasterization is unavailable, keep the data source valid and
        // let MapLibre skip the missing image until a later registration pass.
      }
      resolve();
    };
    img.onerror = () => resolve();
    img.src = incidentMarkerDataUrl(type);
  });
}

function ensureIncidentMarkerImages(m: maplibregl.Map) {
  return Promise.all(
    Object.keys(INCIDENT_MARKER_TOKENS).map((type) =>
      loadIncidentMarkerImage(m, type as IncidentType),
    ),
  );
}

export function mapIncidentsToFeatureCollection(
  incidents: MapIncident[],
): IncidentFeatureCollection {
  const features = incidents
    .filter((incident) => isFiniteCoord(incident.lon, incident.lat))
    .map((incident): GeoJSON.Feature<GeoJSON.Point, IncidentFeatureProperties> => {
      const type = normalizeIncidentType(incident.type);
      const token = INCIDENT_MARKER_TOKENS[type];
      const pulse = shouldPulseIncident({ type, active: incident.active });
      return {
        type: "Feature",
        properties: {
          id: incident.id,
          incident_type: type,
          marker_image: incidentImageId(type),
          title: incident.title,
          description: incident.description,
          station: incident.station,
          route_ids: incident.routeIds?.join(","),
          active: Boolean(incident.active),
          severity: incident.severity,
          category: token.category,
          source: "ATLAS INTEL",
          time_ago_sec: 0,
          hue: token.color,
          critical: token.critical,
          pulse,
          priority: INCIDENT_PRIORITY[type] ?? 0,
        },
        geometry: {
          type: "Point",
          coordinates: [incident.lon, incident.lat],
        },
      };
    });

  return {
    type: "FeatureCollection",
    features,
  };
}

export function ensureIncidentMapLibreLayers(m: maplibregl.Map) {
  if (!m.getSource(INCIDENT_MAPLIBRE_SOURCE_ID)) {
    m.addSource(INCIDENT_MAPLIBRE_SOURCE_ID, {
      type: "geojson",
      data: mapIncidentsToFeatureCollection([]),
    });
  }

  void ensureIncidentMarkerImages(m);

  if (!m.getLayer(INCIDENT_MAPLIBRE_HALO_LAYER_ID)) {
    m.addLayer({
      id: INCIDENT_MAPLIBRE_HALO_LAYER_ID,
      type: "circle",
      source: INCIDENT_MAPLIBRE_SOURCE_ID,
      filter: ["==", ["get", "pulse"], true],
      paint: {
        "circle-color": ["coalesce", ["get", "hue"], "#ef4444"],
        "circle-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          9,
          0.05,
          12,
          0.12,
          15,
          0.18,
        ],
        "circle-radius": [
          "step",
          ["zoom"],
          14,
          12,
          20,
          14,
          30,
        ],
        "circle-stroke-color": ["coalesce", ["get", "hue"], "#ef4444"],
        "circle-stroke-opacity": 0.42,
        "circle-stroke-width": [
          "step",
          ["zoom"],
          1,
          12,
          1.5,
          14,
          2,
        ],
      },
    });
  }

  if (!m.getLayer(INCIDENT_MAPLIBRE_LAYER_ID)) {
    m.addLayer({
      id: INCIDENT_MAPLIBRE_LAYER_ID,
      type: "symbol",
      source: INCIDENT_MAPLIBRE_SOURCE_ID,
      layout: {
        "icon-image": ["get", "marker_image"],
        "icon-anchor": "bottom",
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
        "icon-size": [
          "step",
          ["zoom"],
          24 / INCIDENT_MARKER_BASE_WIDTH,
          12,
          32 / INCIDENT_MARKER_BASE_WIDTH,
          14,
          48 / INCIDENT_MARKER_BASE_WIDTH,
        ],
        "symbol-sort-key": ["coalesce", ["get", "priority"], 0],
      },
    });
  }
}

export function setIncidentMapLibreData(
  m: maplibregl.Map,
  incidents: MapIncident[],
) {
  const source = m.getSource(INCIDENT_MAPLIBRE_SOURCE_ID) as
    | maplibregl.GeoJSONSource
    | undefined;
  if (!source) return;
  source.setData(mapIncidentsToFeatureCollection(incidents));
}
