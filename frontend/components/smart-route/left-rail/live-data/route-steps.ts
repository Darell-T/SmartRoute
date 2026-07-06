import type { RouteStep as ApiRouteStep } from "@/types/api";
import type { RouteDetailStep, RouteStep, RouteStripSegment } from "../types";
import { cleanDestinationLabel } from "./formatters";

export function routeStepToRailStep(step: ApiRouteStep, index: number): RouteStep {
  if (step.type === "WALK") {
    const walkTarget = cleanDestinationLabel(step.arrival_stop);
    return {
      type: index === 0 ? "walk" as const : "exit" as const,
      action: "Walk",
      title: "Walk",
      detail: walkTarget ? `To ${walkTarget}` : "Continue on foot",
      duration: typeof step.minutes_until_arrival === "number" ? `${Math.round(step.minutes_until_arrival)} min` : "walk",
    };
  }

  const line = step.train_line || step.route_id || (step.type === "BUS" ? "BUS" : "");
  const departsIn = step.minutes_until_train_arrives;
  const hasLiveDeparture =
    typeof departsIn === "number" && Number.isFinite(departsIn);
  return {
    type: index === 0 ? "board" as const : "ride" as const,
    action: index === 0 ? "Board" : "Ride",
    line,
    title: `${line} ${step.type === "BUS" ? "bus" : "train"}`,
    detail:
      cleanDestinationLabel(step.direction || step.arrival_stop)
      || "Transit segment",
    note: hasLiveDeparture
      ? `Departs in ${Math.max(1, Math.round(departsIn))} min`
      : undefined,
    live: hasLiveDeparture || undefined,
    duration: typeof step.minutes_until_arrival === "number" ? `${Math.round(step.minutes_until_arrival)} min` : "live",
  };
}


export function mergeConsecutiveWalks(steps: ApiRouteStep[]): ApiRouteStep[] {
  // Google Routes can split walking into back-to-back legs (approach + final
  // walk). The rail should show one "Walk" row per continuous walk, not a
  // repeated "Continue on foot". Fold a run of WALK steps into the first one,
  // extending its end to the run's last stop and keeping the later arrival ETA.
  const out: ApiRouteStep[] = [];
  for (const step of steps) {
    const prev = out[out.length - 1];
    if (step.type === "WALK" && prev?.type === "WALK") {
      out[out.length - 1] = {
        ...prev,
        arrival_stop: step.arrival_stop || prev.arrival_stop,
        minutes_until_arrival: step.minutes_until_arrival ?? prev.minutes_until_arrival,
      };
    } else {
      out.push(step);
    }
  }
  return out;
}

/* Compact visual route strip: [walk 2 min] › [Q] › [5] › [walk 5 min].
   One segment per merged walk or transit leg, in journey order. */
export function stripFromSteps(steps: ApiRouteStep[] | undefined): RouteStripSegment[] {
  return mergeConsecutiveWalks(steps ?? []).map((step) =>
    step.type === "WALK"
      ? {
          kind: "walk" as const,
          minutes:
            typeof step.minutes_until_arrival === "number"
              ? Math.max(1, Math.round(step.minutes_until_arrival))
              : undefined,
        }
      : {
          kind: "ride" as const,
          routeId: (step.route_id || step.train_line || "").toUpperCase(),
          mode: step.type === "BUS" ? ("bus" as const) : ("subway" as const),
        },
  );
}

function walkDetailTitle(
  target: string,
  nextStep: ApiRouteStep | undefined,
  isLast: boolean,
): string {
  if (isLast) return "Walk to destination";
  if (target) {
    if (nextStep?.type === "SUBWAY") {
      return /station$/i.test(target)
        ? `Walk to ${target}`
        : `Walk to ${target} station`;
    }
    return `Walk to ${target}`;
  }
  return nextStep?.type === "BUS" ? "Walk to bus stop" : "Walk to station";
}

/* Full Apple Maps-style details chain: explicit walk / board / ride rows
   with headsigns, live departures, stop counts, and transfer hand-offs.
   The UI adds the Start and Arrive endpoint rows. */
export function detailStepsFromSteps(steps: ApiRouteStep[] | undefined): RouteDetailStep[] {
  const merged = mergeConsecutiveWalks(steps ?? []);
  const transits = merged.filter(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const out: RouteDetailStep[] = [];
  let transitIndex = 0;

  merged.forEach((step, index) => {
    if (step.type === "WALK") {
      const target = cleanDestinationLabel(step.arrival_stop);
      const isLast = index === merged.length - 1;
      const nextStep = merged[index + 1];
      const minutes =
        typeof step.minutes_until_arrival === "number"
          ? Math.max(1, Math.round(step.minutes_until_arrival))
          : undefined;
      out.push({
        kind: "walk",
        title: walkDetailTitle(target, nextStep, isLast),
        subtitle: typeof minutes === "number" ? `About ${minutes} min` : undefined,
      });
      return;
    }

    const routeId = (step.route_id || step.train_line || "").toUpperCase();
    const mode = step.type === "BUS" ? ("bus" as const) : ("subway" as const);
    const vehicle = mode === "bus" ? "bus" : "train";
    const headsign = cleanDestinationLabel(step.direction);
    const departsIn = step.minutes_until_train_arrives;
    const hasLiveDeparture =
      typeof departsIn === "number" && Number.isFinite(departsIn);
    out.push({
      kind: "board",
      routeId,
      mode,
      title: `Board the ${routeId} ${vehicle}`,
      subtitle: headsign
        ? /bound|to /i.test(headsign)
          ? headsign
          : `Toward ${headsign}`
        : undefined,
      note: hasLiveDeparture
        ? `Departs in ${Math.max(1, Math.round(departsIn))} min`
        : undefined,
      live: hasLiveDeparture || undefined,
    });

    const stopCount =
      typeof step.stop_count === "number" && step.stop_count > 0
        ? step.stop_count
        : step.intermediate_stops?.length
          ? step.intermediate_stops.length + 1
          : undefined;
    const rideMinutes =
      typeof step.minutes_until_arrival === "number"
        ? Math.max(1, Math.round(step.minutes_until_arrival))
        : undefined;
    const next = transits[transitIndex + 1];
    const nextRouteId = next
      ? (next.route_id || next.train_line || "").toUpperCase()
      : undefined;
    out.push({
      kind: "ride",
      routeId,
      mode,
      title: `Ride the ${routeId}`,
      fromStop: cleanDestinationLabel(step.departure_stop) || undefined,
      toStop: cleanDestinationLabel(step.arrival_stop) || undefined,
      rideMeta: [
        typeof stopCount === "number"
          ? `Ride ${stopCount} stop${stopCount === 1 ? "" : "s"}`
          : "Ride",
        typeof rideMinutes === "number" ? `${rideMinutes} min` : null,
      ]
        .filter(Boolean)
        .join(" · "),
      transferTo: nextRouteId || undefined,
      transferMode: next
        ? next.type === "BUS"
          ? ("bus" as const)
          : ("subway" as const)
        : undefined,
    });
    transitIndex += 1;
  });

  return out;
}
