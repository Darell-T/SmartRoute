import type { IncidentMarkerSize, IncidentMarkerState, IncidentType } from "./incident-marker-types";

export interface IncidentMarkerToken {
  id: IncidentType;
  label: string;
  code: string;
  category: string;
  color: string;
  hue: string;
  deep: string;
  glow: string;
  critical: boolean;
}

export const INCIDENT_MARKER_TOKENS: Record<IncidentType, IncidentMarkerToken> = {
  shooting: {
    id: "shooting",
    label: "Shooting",
    code: "SHT-01",
    category: "Critical · Violence",
    color: "#ef4444",
    hue: "#ef4444",
    deep: "#991b1b",
    glow: "rgba(239,68,68,0.42)",
    critical: true,
  },
  stabbing: {
    id: "stabbing",
    label: "Stabbing",
    code: "STB-02",
    category: "Critical · Violence",
    color: "#f97316",
    hue: "#f97316",
    deep: "#9a3412",
    glow: "rgba(249,115,22,0.42)",
    critical: true,
  },
  medical: {
    id: "medical",
    label: "Medical",
    code: "MED-03",
    category: "Emergency · Health",
    color: "#ec4899",
    hue: "#ec4899",
    deep: "#9d174d",
    glow: "rgba(236,72,153,0.34)",
    critical: false,
  },
  fire: {
    id: "fire",
    label: "Fire",
    code: "FIR-04",
    category: "Critical · Hazard",
    color: "#fb923c",
    hue: "#fb923c",
    deep: "#c2410c",
    glow: "rgba(251,146,60,0.42)",
    critical: true,
  },
  police: {
    id: "police",
    label: "Police Activity",
    code: "POL-05",
    category: "Response · LE",
    color: "#3b82f6",
    hue: "#3b82f6",
    deep: "#1d4ed8",
    glow: "rgba(59,130,246,0.34)",
    critical: false,
  },
  disruptive: {
    id: "disruptive",
    label: "Disruptive Passenger",
    code: "DSP-06",
    category: "Onboard · Passenger",
    color: "#eab308",
    hue: "#eab308",
    deep: "#a16207",
    glow: "rgba(234,179,8,0.34)",
    critical: false,
  },
  suspicious: {
    id: "suspicious",
    label: "Suspicious Package",
    code: "SUS-07",
    category: "Hazard · Object",
    color: "#14b8a6",
    hue: "#14b8a6",
    deep: "#0f766e",
    glow: "rgba(20,184,166,0.34)",
    critical: false,
  },
  general: {
    id: "general",
    label: "General Incident",
    code: "GEN-08",
    category: "Advisory · Unknown",
    color: "#8b5cf6",
    hue: "#8b5cf6",
    deep: "#6d28d9",
    glow: "rgba(139,92,246,0.34)",
    critical: false,
  },
};

export const INCIDENT_MARKER_SIZES: Record<
  IncidentMarkerSize,
  {
    width: number;
    height: number;
    well: number;
    icon: number;
    rim: number;
    anchor: number;
  }
> = {
  L: { width: 48, height: 61, well: 30, icon: 26, rim: 1.5, anchor: 4.5 },
  M: { width: 32, height: 41, well: 20, icon: 18, rim: 1.1, anchor: 3 },
  S: { width: 24, height: 31, well: 15, icon: 13, rim: 0.9, anchor: 2 },
};

export const INCIDENT_PRIORITY: Record<IncidentType, number> = {
  shooting: 100,
  stabbing: 95,
  fire: 90,
  medical: 70,
  police: 65,
  suspicious: 60,
  disruptive: 50,
  general: 30,
};

export function isCriticalIncidentType(type: IncidentType) {
  return INCIDENT_MARKER_TOKENS[type].critical;
}

export function shouldPulseIncident({
  type,
  active,
}: {
  type: IncidentType;
  active?: boolean;
}) {
  return Boolean(active && isCriticalIncidentType(type));
}

export function incidentMarkerSizeForZoom(zoom: number): IncidentMarkerSize {
  if (zoom >= 14) return "L";
  if (zoom >= 12) return "M";
  return "S";
}

export function getIncidentMarkerState({
  active,
  selected,
}: {
  active?: boolean;
  selected?: boolean;
}): IncidentMarkerState {
  if (selected) return "selected";
  if (active) return "pulse";
  return "default";
}

export function getIncidentSpriteKey(
  type: IncidentType,
  size: IncidentMarkerSize,
  state: IncidentMarkerState,
) {
  return `${type}-${size}-${state}`;
}

export function normalizeIncidentType(type: string | null | undefined): IncidentType {
  const value = String(type || "").toLowerCase().trim().replace(/_/g, "-");

  if (value === "weapon" || value === "weapon-incident" || value === "shooting") {
    return "shooting";
  }
  if (value === "passenger" || value === "disruptive" || value === "disruptive-passenger") {
    return "disruptive";
  }
  if (
    value === "suspicious" ||
    value === "suspicious-package" ||
    value === "package" ||
    value === "unattended-package" ||
    value === "hazard-object"
  ) {
    return "suspicious";
  }
  if (value === "police" || value === "police-activity") return "police";
  if (value === "medical" || value === "medical-emergency") return "medical";
  if (
    value === "stabbing" ||
    value === "fire" ||
    value === "general"
  ) {
    return value;
  }

  return "general";
}
