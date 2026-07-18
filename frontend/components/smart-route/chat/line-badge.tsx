"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — line badge

   Renders one route id as the real MTA bullet (subway) or the rail's bus
   chip, sharing the exact lookup the left rail uses (`SUBWAY_BULLET_ROUTES`)
   so a route never renders as the wrong shape. Used by the Near You row,
   route cards, and the arrivals card — anywhere a chat surface needs to
   show "this is the A train," never re-implemented per call site.
   ════════════════════════════════════════════════════════════════════════ */

import { RouteBullet, BusChip } from "@/components/smart-route/left-rail/atoms";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";

export function LineBadge({ line, size = 22 }: { line: string; size?: number }) {
  const routeId = line.toUpperCase();
  return SUBWAY_BULLET_ROUTES.has(routeId) ? (
    <RouteBullet line={routeId} size={size} />
  ) : (
    <BusChip route={routeId} />
  );
}
