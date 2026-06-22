import { nowStamp, type AgentLogEntry } from "@/lib/smart-route";
import type { MapIncident } from "@/components/map/incidents/incident-marker-types";

/** Lavender accent used across the shell's empty / standby states. */
export const ACCENT = "#d4a7ff";

/** Imperative handle the map exposes to the shell for camera + incident focus. */
export type MapActions = {
  recenter: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  resetNorth: () => void;
  focusIncident: (incident: MapIncident) => void;
};

/** Clock label for the shell header; "Awaiting" until the first live tick. */
export function formatShellClock(unix?: number | null) {
  if (!unix) return "Awaiting";
  return new Date(unix * 1000).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}

/** Appends a timestamped entry to the agent log (immutably). */
export function appendLog(
  entries: AgentLogEntry[],
  level: AgentLogEntry["level"],
  text: string,
): AgentLogEntry[] {
  return [...entries, { t: nowStamp(), level, text }];
}

/** Placeholder rail card shown when a panel has no live data yet. */
export function EmptyRailCard({ label, body }: { label: string; body: string }) {
  return (
    <div className="sr-card" style={{ padding: 14 }}>
      <div className="sr-section-label">{label}</div>
      <div
        style={{
          fontFamily: "var(--font-geist), sans-serif",
          fontSize: 12,
          lineHeight: 1.6,
          color: "rgba(255,255,255,0.56)",
        }}
      >
        {body}
      </div>
    </div>
  );
}
