import type { MapIncident } from "./incident-marker-types";
import { INCIDENT_MARKER_TOKENS } from "./incident-marker-tokens";

export function IncidentA11yList({
  incidents,
  selectedIncidentId,
}: {
  incidents: MapIncident[];
  selectedIncidentId?: string | null;
}) {
  return (
    <ul className="sr-only" role="list" aria-live="polite">
      {incidents.map((incident) => {
        const typeLabel = INCIDENT_MARKER_TOKENS[incident.type].label;
        const routeText = incident.routeIds?.length
          ? ` Affected routes: ${incident.routeIds.join(", ")}.`
          : "";
        const stationText = incident.station ? ` Near ${incident.station}.` : "";
        const selectedText = selectedIncidentId === incident.id ? " Selected." : "";
        const activeText = incident.active ? " Active." : "";

        return (
          <li key={incident.id}>
            {typeLabel}: {incident.title}.{stationText}{routeText}{activeText}{selectedText}
          </li>
        );
      })}
    </ul>
  );
}
