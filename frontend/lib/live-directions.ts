import type {
  LiveArrival,
  LiveDirectionSummaryRow,
  LiveVehicle,
} from "@/types";

export function normalizeLiveRouteId(routeId: string) {
  const upper = routeId.toUpperCase();
  if (upper === "6X") return "6";
  if (upper === "7X") return "7";
  if (upper === "FX") return "F";
  if (upper === "FS" || upper === "GS" || upper === "H") return "S";
  if (upper === "SIR") return "SI";
  return upper;
}

function normalizeDirectionLabel(direction?: string | null) {
  const upper = String(direction || "")
    .trim()
    .toUpperCase();
  if (upper === "0") return "UPTOWN";
  if (upper === "1") return "DOWNTOWN";
  if (upper === "N" || upper.includes("UPTOWN") || upper.includes("NORTH")) {
    return "UPTOWN";
  }
  if (upper === "S" || upper.includes("DOWNTOWN") || upper.includes("SOUTH")) {
    return "DOWNTOWN";
  }
  return "UNKNOWN";
}

function directionFromStopId(stopId?: string | null) {
  const normalized = String(stopId || "")
    .trim()
    .toUpperCase();
  if (normalized.endsWith("N")) return "UPTOWN";
  if (normalized.endsWith("S")) return "DOWNTOWN";
  return "UNKNOWN";
}

export function directionFromVehicle(vehicle: LiveVehicle) {
  const direct = directionFromStopId(vehicle.stop_id);
  if (direct !== "UNKNOWN") return direct;
  const segmentTo = directionFromStopId(vehicle.segment?.to_stop_id);
  if (segmentTo !== "UNKNOWN") return segmentTo;
  return directionFromStopId(vehicle.segment?.from_stop_id);
}

function directionLabel(direction: string) {
  if (direction === "UPTOWN") return "Uptown";
  if (direction === "DOWNTOWN") return "Downtown";
  return "Unknown";
}

const ROUTE_DESTINATION_FALLBACKS: Record<
  string,
  Partial<Record<"UPTOWN" | "DOWNTOWN", string>>
> = {
  "1": { UPTOWN: "242 St", DOWNTOWN: "South Ferry" },
  "2": { UPTOWN: "241 St", DOWNTOWN: "Flatbush Av" },
  "3": { UPTOWN: "148 St", DOWNTOWN: "New Lots Av" },
  "4": { UPTOWN: "Woodlawn", DOWNTOWN: "New Lots Av" },
  "5": { UPTOWN: "Dyre Av", DOWNTOWN: "Flatbush Av" },
  "6": { UPTOWN: "Pelham Bay Park", DOWNTOWN: "Brooklyn Bridge" },
  "7": { UPTOWN: "Main St", DOWNTOWN: "Hudson Yards" },
  A: { UPTOWN: "207 St", DOWNTOWN: "Far Rockaway" },
  B: { UPTOWN: "145 St", DOWNTOWN: "Brighton Beach" },
  C: { UPTOWN: "168 St", DOWNTOWN: "Euclid Av" },
  D: { UPTOWN: "205 St", DOWNTOWN: "Coney Island" },
  E: { UPTOWN: "Jamaica Center", DOWNTOWN: "World Trade Center" },
  F: { UPTOWN: "179 St", DOWNTOWN: "Coney Island" },
  G: { UPTOWN: "Court Sq", DOWNTOWN: "Church Av" },
  J: { UPTOWN: "Jamaica Center", DOWNTOWN: "Broad St" },
  L: { UPTOWN: "8 Av", DOWNTOWN: "Canarsie" },
  M: { UPTOWN: "Forest Hills", DOWNTOWN: "Middle Village" },
  N: { UPTOWN: "Ditmars Blvd", DOWNTOWN: "Coney Island" },
  Q: { UPTOWN: "96 St", DOWNTOWN: "Coney Island" },
  R: { UPTOWN: "Forest Hills", DOWNTOWN: "Bay Ridge" },
  W: { UPTOWN: "Ditmars Blvd", DOWNTOWN: "Whitehall St" },
  Z: { UPTOWN: "Jamaica Center", DOWNTOWN: "Broad St" },
  SI: { UPTOWN: "St George", DOWNTOWN: "Tottenville" },
  S: { UPTOWN: "Shuttle", DOWNTOWN: "Shuttle" },
};

function terminalIsJustDirection(label: string, direction: string) {
  const clean = label.trim().toLowerCase();
  const directionOnly = directionLabel(direction).toLowerCase();
  return clean === directionOnly || clean === `${directionOnly} bound`;
}

function destinationLabelForArrival(
  arrival: LiveArrival,
  routeId: string,
  direction: string,
) {
  const terminal = arrival.terminal_stop_name?.trim();
  if (terminal && !terminalIsJustDirection(terminal, direction)) {
    return terminal;
  }
  if (direction === "UPTOWN" || direction === "DOWNTOWN") {
    return ROUTE_DESTINATION_FALLBACKS[routeId]?.[direction] ?? "";
  }
  return "";
}

export function buildLiveDirectionRows(
  arrivals: LiveArrival[],
): LiveDirectionSummaryRow[] {
  const groups = new Map<
    string,
    {
      routeId: string;
      direction: string;
      terminalKey: string;
      terminalLabel: string;
      destinationLabel: string;
      arrivals: Array<LiveArrival & { arrival_time: number }>;
    }
  >();

  for (const arrival of arrivals) {
    if (arrival.arrival_time == null) continue;
    const routeId = normalizeLiveRouteId(arrival.route_id);
    const normalizedDirection = normalizeDirectionLabel(arrival.direction);
    const direction =
      normalizedDirection !== "UNKNOWN"
        ? normalizedDirection
        : directionFromStopId(arrival.stop_id);
    const resolvedDirection = direction === "UNKNOWN" && arrival.mode === "bus"
      ? "UPTOWN"
      : direction;
    if (resolvedDirection === "UNKNOWN") continue;

    const terminalLabel =
      arrival.terminal_stop_name?.trim() || directionLabel(resolvedDirection);
    const destinationLabel = destinationLabelForArrival(arrival, routeId, resolvedDirection);
    const key = `${routeId}:${resolvedDirection}`;
    const existing = groups.get(key) ?? {
      routeId,
      direction: resolvedDirection,
      terminalKey: resolvedDirection,
      terminalLabel,
      destinationLabel,
      arrivals: [],
    };

    if (
      existing.terminalLabel === directionLabel(resolvedDirection) &&
      arrival.terminal_stop_name?.trim()
    ) {
      existing.terminalLabel = arrival.terminal_stop_name.trim();
    }
    if (!existing.destinationLabel && destinationLabel) {
      existing.destinationLabel = destinationLabel;
    }

    existing.arrivals.push(arrival as LiveArrival & { arrival_time: number });
    groups.set(key, existing);
  }

  return Array.from(groups.values())
    .map((group) => {
      const nextArrivals = [...group.arrivals].sort(
        (a, b) => a.arrival_time - b.arrival_time,
      );
      const nextTerminalLabel =
        nextArrivals[0]?.terminal_stop_name?.trim() || group.terminalLabel;
      const nextDestinationLabel =
        destinationLabelForArrival(nextArrivals[0], group.routeId, group.direction) ||
        group.destinationLabel;
      return {
        key: `${group.routeId}:${group.direction}:${group.terminalKey}`,
        routeId: group.routeId,
        direction: group.direction,
        terminalKey: group.terminalKey,
        terminalLabel: nextTerminalLabel,
        destinationLabel: nextDestinationLabel,
        nextArrivalTime: nextArrivals[0].arrival_time,
        arrivals: nextArrivals,
      };
    })
    .sort((a, b) => a.nextArrivalTime - b.nextArrivalTime);
}
