"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — line badge

   Renders one route id as the official MTA bullet artwork from
   public/mta-bullets (via the shared TrainBullet component), matching the
   signage riders actually see. TrainBullet handles the non-subway case
   itself (bus routes render as its bus chip), so every chat surface —
   Near You row, route cards, arrivals card — goes through one component
   and a route never renders as the wrong shape.
   ════════════════════════════════════════════════════════════════════════ */

import { TrainBullet } from "@/components/smart-route/train-bullet";

export function LineBadge({ line, size = 22 }: { line: string; size?: number }) {
  return <TrainBullet line={line} size={size} />;
}
