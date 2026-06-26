import type { MapIncident } from "@/components/map/incidents/incident-marker-types";

/** Imperative handle the map exposes to the shell for camera + incident focus. */
export type MapActions = {
  recenter: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  resetNorth: () => void;
  focusIncident: (incident: MapIncident) => void;
};
