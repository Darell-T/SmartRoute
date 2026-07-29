import type { RoutePlan, RouteStep } from "./types";

export type RecommendedRouteDisplay = {
  walkMinutes?: number;
  transfers?: number;
};

// Legacy presentation fallback for routes whose backend contract predates
// canonical transfer/walk display fields. Canonical `RoutePlan` facts still
// win in the renderer; remove this only when the backend owns both values.
export function recommendedCandidateFromPlan(
  plan: RoutePlan,
): RecommendedRouteDisplay {
  const timing = timingFromSteps(plan.steps);
  return {
    walkMinutes: timing.walkMinutes,
    transfers: timing.transfers,
  };
}

export function routeResultKey(plan: RoutePlan): string {
  return [
    plan.isAlternativeRoute ? "selected" : "recommended",
    plan.pickedLine || "walk",
    plan.headsign || "",
    plan.totalTime || "",
  ].join(":");
}

export function formatDurationLabel(totalTime: string): string {
  const minutes = parseMinutes(totalTime);
  if (typeof minutes !== "number") {
    return totalTime;
  }
  if (minutes < 60) {
    return `${minutes} min`;
  }

  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours} hr ${rest} min` : `${hours} hr`;
}

function timingFromSteps(steps: RouteStep[]): RecommendedRouteDisplay {
  let walkMinutes = 0;
  let boardCount = 0;
  for (const step of steps) {
    const minutes = parseMinutes(step.duration) ?? 0;
    if (
      step.type === "walk" ||
      step.type === "exit" ||
      step.type === "destination"
    ) {
      walkMinutes += minutes;
    } else if (step.line) {
      boardCount += 1;
    }
  }

  return {
    walkMinutes: walkMinutes || undefined,
    transfers: Math.max(0, boardCount - 1),
  };
}

function parseMinutes(value: string | undefined): number | undefined {
  if (!value) {
    return undefined;
  }

  const match = value.match(/-?\d+/);
  if (!match) {
    return undefined;
  }

  return Math.max(0, Number(match[0]));
}
