import type { RouteCandidate } from "@/types";
import { formatNycRouteClock } from "@/lib/nyc-route-clock";

export interface CanonicalRouteSummary {
  arriveLabel: string | null;
  totalLabel: string | null;
}

function formatArrival(iso: string | undefined): string | null {
  return formatNycRouteClock(iso);
}

/**
 * Formats server-owned route facts without deriving a duration, arrival, or
 * transfer fallback from route steps. Missing canonical facts stay unavailable.
 */
export function formatCanonicalRouteSummary(
  candidate: RouteCandidate | null,
): CanonicalRouteSummary | null {
  if (
    !candidate?.itinerary?.itinerary_id ||
    typeof candidate.total_minutes !== "number" ||
    !Number.isFinite(candidate.total_minutes)
  ) {
    return null;
  }
  return {
    arriveLabel: formatArrival(candidate.arrival_at),
    totalLabel: `${candidate.total_minutes} min`,
  };
}
