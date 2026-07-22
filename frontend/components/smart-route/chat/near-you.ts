/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — "Near You" derivation

   Pure helpers only (no React, no fetching). The route-id list mirrors the
   left rail's own `nearbyRouteIds` derivation (left-rail.tsx lines ~94-106:
   nearbyTransitGroups + arrivals + nearbyBusArrivals, deduped, uppercased)
   so the chat top bar's bullets are "the same derivation the left rail
   uses" per the design spec, without importing rail internals or standing
   up a second WebSocket — the caller passes down the `LeftRailLiveData`
   page.tsx already computes from the one shared `useLiveFeed` connection.
   ════════════════════════════════════════════════════════════════════════ */

import type { Arrival, NearbyTransitGroup } from "@/components/smart-route/left-rail/types";
import type { ArrivalsTurnDirectionGroup, ArrivalsTurnPayload } from "@/lib/use-agent-chat";

/** Up to `limit` route ids the rider is standing near, nearest first (the
 *  inputs are already proximity-sorted by `buildLeftRailData`). */
export function deriveNearbyRouteIds(data: {
  nearbyTransitGroups?: NearbyTransitGroup[];
  arrivals?: Arrival[];
  nearbyBusArrivals?: Arrival[];
}): string[] {
  const seen = new Set<string>();
  for (const group of data.nearbyTransitGroups ?? []) {
    for (const routeId of group.routeIds) seen.add(routeId.toUpperCase());
  }
  for (const arrival of data.arrivals ?? []) {
    for (const routeId of arrival.routeIds) seen.add(routeId.toUpperCase());
  }
  for (const arrival of data.nearbyBusArrivals ?? []) {
    for (const routeId of arrival.routeIds) seen.add(routeId.toUpperCase());
  }
  return Array.from(seen);
}

/** The nearest nearby-transit-group's station name that actually serves
 *  `routeId`, falling back to the rider's overall nearest stop. Used as the
 *  ArrivalsCard header ("125 St") when a Near You bullet is tapped. */
export function stationNameForRoute(
  routeId: string,
  nearbyTransitGroups: NearbyTransitGroup[],
  fallback: string,
): string {
  const normalized = routeId.toUpperCase();
  const group = nearbyTransitGroups.find((candidate) =>
    candidate.routeIds.some((id) => id.toUpperCase() === normalized),
  );
  return group?.name ?? fallback;
}

const DIRECTION_LABEL: Record<"uptown" | "downtown", string> = {
  uptown: "Uptown",
  downtown: "Downtown",
};

/** Groups the rider's live arrivals for one route into the two direction
 *  buckets an `ArrivalsCard` renders ("Uptown · 2, 7, 12 min"). Arrivals
 *  with an unresolved direction ("unknown" — mostly buses on an E/W street)
 *  are omitted rather than guessed at. */
export function buildArrivalsPayloadForRoute(
  routeId: string,
  arrivals: Arrival[],
  stationName: string,
  station?: {
    walkMinutes?: number;
    distanceMiles?: number;
    coordinates?: { lat: number; lng: number };
  },
): ArrivalsTurnPayload {
  const normalized = routeId.toUpperCase();
  const minutesByDirection = new Map<"uptown" | "downtown", number[]>();

  for (const arrival of arrivals) {
    if (!arrival.routeIds.some((id) => id.toUpperCase() === normalized)) continue;
    if (arrival.direction !== "uptown" && arrival.direction !== "downtown") continue;
    const bucket = minutesByDirection.get(arrival.direction) ?? [];
    bucket.push(...arrival.arrivalMinutes);
    minutesByDirection.set(arrival.direction, bucket);
  }

  const groups: ArrivalsTurnDirectionGroup[] = [];
  for (const direction of ["uptown", "downtown"] as const) {
    const minutes = minutesByDirection.get(direction);
    if (!minutes || minutes.length === 0) continue;
    const sorted = Array.from(new Set(minutes)).sort((a, b) => a - b).slice(0, 3);
    groups.push({ direction, label: DIRECTION_LABEL[direction], minutes: sorted });
  }

  const guidanceParts: string[] = [];
  if (station?.walkMinutes !== undefined) {
    guidanceParts.push(`${station.walkMinutes} min walk`);
  }
  if (station?.distanceMiles !== undefined) {
    guidanceParts.push(`${station.distanceMiles.toFixed(1)} mi away`);
  }

  return {
    routeId: normalized,
    stationName,
    stationGuidance: guidanceParts.length > 0 ? guidanceParts.join(" · ") : undefined,
    stationCoordinates: station?.coordinates,
    groups,
  };
}
