import type { LiveFeedIncident } from "@/types/api";

// Shared incident formatting used by both the rail header strip (left-rail.tsx)
// and the Route tab's IncidentsSection (route-view.tsx).

/** Relative-time label for an incident's last update: "Live" / "Now" / "12m" / "3h". */
export function incidentTimeLabel(updatedAt: number | undefined): string {
  if (!updatedAt) return "Live";
  const minutes = Math.max(0, Math.round(Date.now() / 1000 / 60 - updatedAt / 60));
  if (minutes < 1) return "Now";
  if (minutes < 60) return `${minutes}m`;
  return `${Math.round(minutes / 60)}h`;
}

/** Maps incident severity to the rail's tone palette. */
export function incidentTone(incident: LiveFeedIncident): "coral" | "amber" | "cyan" {
  if (incident.severity === "critical" || incident.severity === "high") return "coral";
  if (incident.severity === "medium") return "amber";
  return "cyan";
}
