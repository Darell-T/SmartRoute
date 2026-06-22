export type NetworkHealthStatus = "healthy" | "caution" | "disrupted";

export const NETWORK_STATUS_HEX: Record<NetworkHealthStatus, string> = {
  healthy: "#9ccfbf",
  caution: "#6ec4ff",
  disrupted: "#ff6868",
};

export const NETWORK_STATUS_LABEL: Record<NetworkHealthStatus, string> = {
  healthy: "Good Health",
  caution: "Some Disruptions",
  disrupted: "Disrupted",
};

export const NETWORK_SIGNAL_THEME: Record<
  NetworkHealthStatus,
  {
    orbAccent: string;
    beamFrom: string;
    beamTo: string;
    beamGlow: string;
    beamOpacity: number;
    beamActiveOpacity: number;
    beamSize: number;
    beamDuration: number;
  }
> = {
  healthy: {
    orbAccent: NETWORK_STATUS_HEX.healthy,
    beamFrom: "#5eead4",
    beamTo: "#22c55e",
    beamGlow: "rgba(94, 234, 212, 0.14)",
    beamOpacity: 0.34,
    beamActiveOpacity: 0.5,
    beamSize: 220,
    beamDuration: 9.2,
  },
  caution: {
    orbAccent: NETWORK_STATUS_HEX.caution,
    beamFrom: "#6ec4ff",
    beamTo: "#fbbf24",
    beamGlow: "rgba(110, 196, 255, 0.16)",
    beamOpacity: 0.38,
    beamActiveOpacity: 0.56,
    beamSize: 240,
    beamDuration: 8.4,
  },
  disrupted: {
    orbAccent: NETWORK_STATUS_HEX.disrupted,
    beamFrom: "#fb7185",
    beamTo: "#a855f7",
    beamGlow: "rgba(251, 113, 133, 0.22)",
    beamOpacity: 0.46,
    beamActiveOpacity: 0.68,
    beamSize: 270,
    beamDuration: 7.8,
  },
};

export function normalizeNetworkStatus(
  status?: NetworkHealthStatus | null,
): NetworkHealthStatus {
  return status ?? "healthy";
}

export function mapStatusToOrbColor(status?: NetworkHealthStatus | null) {
  return Number.parseInt(
    NETWORK_STATUS_HEX[normalizeNetworkStatus(status)].slice(1),
    16,
  );
}
