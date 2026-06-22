export const INCIDENT_TYPES = [
  "shooting",
  "stabbing",
  "medical",
  "fire",
  "police",
  "disruptive",
  "suspicious",
  "general",
] as const;

export type IncidentType = (typeof INCIDENT_TYPES)[number];

export type IncidentMarkerSize = "L" | "M" | "S";
export type IncidentMarkerState = "default" | "pulse" | "selected";

export type IncidentSeverity = "low" | "medium" | "high" | "critical";

export interface MapIncident {
  id: string;
  type: IncidentType;
  severity?: IncidentSeverity;
  title: string;
  description?: string;
  lon: number;
  lat: number;
  station?: string;
  routeIds?: string[];
  active?: boolean;
}

export interface IncidentAtlasEntry {
  x: number;
  y: number;
  width: number;
  height: number;
  anchorX: number;
  anchorY: number;
  mask: false;
}

export type IncidentIconMapping = Record<string, IncidentAtlasEntry>;

/**
 * Adapter — backend `LiveFeedIncident` → marker-system `MapIncident`.
 *
 * Lives next to the marker types so any consumer (map layer, A11y list,
 * legend popup) can hydrate from the same backend payload without each
 * re-deriving lon/lat or normalizing the type union. Pure function, no
 * map-side imports — safe to call from server components.
 *
 * The `LiveFeedIncident.type` union includes `hazard` + `incident` legacy
 * codes that the marker token normalizer collapses into `general`. Status
 * mapping: `severity === "critical"` → active so it pulses by default.
 */
export interface LiveFeedIncidentLike {
  id: string;
  type: string;
  lat: number;
  lng: number;
  title: string;
  detail?: string;
  severity?: IncidentSeverity;
  station?: string;
  routeIds?: string[];
  active?: boolean;
}

export function liveFeedIncidentToMapIncident(
  incident: LiveFeedIncidentLike,
  normalize: (t: string) => IncidentType,
): MapIncident {
  const normalizedType = normalize(incident.type);
  const isActive =
    incident.active ??
    (incident.severity === "critical" || incident.severity === "high");
  return {
    id: incident.id,
    type: normalizedType,
    severity: incident.severity,
    title: incident.title,
    description: incident.detail,
    lon: incident.lng,
    lat: incident.lat,
    station: incident.station,
    routeIds: incident.routeIds,
    active: isActive,
  };
}
