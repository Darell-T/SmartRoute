import type { RouteCandidate, RouteStep as ApiRouteStep } from "@/types/api";
import type { Alternative } from "../types";
import { cleanDestinationLabel, formatClockAt } from "./formatters";
import { stripFromSteps } from "./route-steps";
import type { CandidateDelta } from "./types";

function routeEtaMinutes(steps: ApiRouteStep[] | undefined): number | null {
  const sourceTotal = steps?.find(
    (step) =>
      typeof step.route_total_minutes === "number" &&
      Number.isFinite(step.route_total_minutes),
  )?.route_total_minutes;
  if (typeof sourceTotal === "number" && Number.isFinite(sourceTotal)) {
    return Math.max(1, Math.round(sourceTotal));
  }
  // Trip steps carry minutes relative to now; the largest arrival figure is
  // the trip's ETA. Good enough for candidate-vs-candidate deltas.
  let max: number | null = null;
  for (const step of steps ?? []) {
    const minutes = step.minutes_until_arrival;
    if (typeof minutes === "number" && Number.isFinite(minutes)) {
      max = max === null ? minutes : Math.max(max, minutes);
    }
  }
  return max;
}

export function candidateEtaMinutes(candidate: RouteCandidate | null | undefined): number | null {
  if (
    typeof candidate?.total_minutes === "number" &&
    Number.isFinite(candidate.total_minutes)
  ) {
    return Math.max(1, Math.round(candidate.total_minutes));
  }
  return routeEtaMinutes(candidate?.steps);
}

export function candidateDelta(candidate: RouteCandidate, active: RouteCandidate | null | undefined): CandidateDelta {
  const candidateEta = candidateEtaMinutes(candidate);
  const activeEta = candidateEtaMinutes(active);
  if (candidateEta === null || activeEta === null) {
    return { delta: "n/a", sev: "low" };
  }
  const diff = Math.round(candidateEta - activeEta);
  const delta = diff === 0 ? "same time" : `${diff > 0 ? "+" : ""}${diff} min`;
  const magnitude = Math.abs(diff);
  const sev = magnitude >= 8 ? "high" : magnitude >= 3 ? "medium" : "low";
  return { delta, sev };
}

export function firstTransitStep(steps: ApiRouteStep[] | undefined): ApiRouteStep | undefined {
  // Local rather than importing lib/route-planning: this file is exercised
  // by the node --test runner, which cannot resolve extensionless VALUE
  // imports from .ts (type-only imports are erased and fine).
  return steps?.find((step) => step.type === "SUBWAY" || step.type === "BUS");
}

/* Route signature for dedup: mode sequence, route ids, boarding stops, and
   final arrival. Candidates that only differ in departure time collapse to
   the same signature. */
export function candidateSignature(candidate: RouteCandidate | null | undefined): string {
  const transitSteps = (candidate?.steps ?? []).filter(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const legs = transitSteps.map((step) =>
    [
      step.type,
      (step.route_id || step.train_line || "").toUpperCase(),
      step.departure_stop ?? "",
      step.arrival_stop ?? "",
    ].join(":"),
  );
  return legs.join(">") || "walk-only";
}

export function transitRouteIdsFromSteps(steps: ApiRouteStep[] | undefined): string[] {
  const ids: string[] = [];
  for (const step of steps ?? []) {
    if (step.type !== "SUBWAY" && step.type !== "BUS") continue;
    const id = (step.route_id || step.train_line || "").trim().toUpperCase();
    if (id && !ids.includes(id)) ids.push(id);
  }
  return ids;
}

/* Apple Maps-style option card facts, all read from the candidate's own
   precomputed steps: total time, live departure, ETA clock, and the
   boarding → alighting path. Selecting the card never replans. */
function alternativeCardFields(
  candidate: RouteCandidate,
  nowMs: number,
): Pick<
  Alternative,
  | "lines"
  | "totalMinutes"
  | "departsInMinutes"
  | "leavesLabel"
  | "arriveLabel"
  | "fromStop"
  | "toStop"
  | "strip"
> {
  const transitSteps = (candidate.steps ?? []).filter(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
  const first = transitSteps[0];
  const last = transitSteps[transitSteps.length - 1];
  const totalMinutes = candidateEtaMinutes(candidate) ?? undefined;
  const departsIn = first?.minutes_until_train_arrives;
  const departsInMinutes =
    typeof departsIn === "number" && Number.isFinite(departsIn)
      ? Math.max(1, Math.round(departsIn))
      : undefined;
  return {
    lines: transitRouteIdsFromSteps(candidate.steps),
    totalMinutes,
    departsInMinutes,
    leavesLabel:
      typeof departsInMinutes === "number" && nowMs > 0
        ? formatClockAt(nowMs + departsInMinutes * 60_000)
        : undefined,
    arriveLabel:
      typeof totalMinutes === "number" && nowMs > 0
        ? formatClockAt(nowMs + totalMinutes * 60_000)
        : undefined,
    fromStop: cleanDestinationLabel(first?.departure_stop) || undefined,
    toStop: cleanDestinationLabel(last?.arrival_stop) || undefined,
    strip: stripFromSteps(candidate.steps),
  };
}

export function buildAlternatives(
  routeCandidates: RouteCandidate[] | undefined,
  activeRouteCandidate: RouteCandidate | null | undefined,
  nowMs: number,
): Alternative[] {
  if (!routeCandidates?.length || !activeRouteCandidate) return [];
  const activeSignature = candidateSignature(activeRouteCandidate);
  const seenSignatures = new Set<string>();
  const alternatives: Alternative[] = [];

  for (const candidate of routeCandidates) {
    if (candidate.id === activeRouteCandidate.id) continue;
    const transit = firstTransitStep(candidate.steps);
    const { delta, sev } = candidateDelta(candidate, activeRouteCandidate);
    const signature = candidateSignature(candidate);

    // Same route as the one already shown: keep at most one, labeled by
    // its real distinction instead of repeating an identical-looking row.
    // Same-time clones are dropped without consuming that one slot.
    if (signature === activeSignature) {
      if (seenSignatures.has(signature)) continue;
      if (delta === "same time") continue;
      seenSignatures.add(signature);
      alternatives.push({
        id: candidate.id,
        line: (transit?.route_id || transit?.train_line || "WALK").toUpperCase(),
        dest: "Later departure",
        delta,
        sev,
        reason: "Later departure",
        status: "rejected" as const,
        ...alternativeCardFields(candidate, nowMs),
      });
      continue;
    }

    // Duplicate of an already-kept alternate: candidates arrive ranked, so
    // the first (better) one wins.
    if (seenSignatures.has(signature)) continue;
    seenSignatures.add(signature);

    alternatives.push({
      id: candidate.id,
      line: (transit?.route_id || transit?.train_line || "WALK").toUpperCase(),
      dest:
        cleanDestinationLabel(transit?.direction || transit?.arrival_stop)
        || "Alternate routing",
      delta,
      sev,
      reason: normalizeAlternateReason(
        candidate.rejection_reason ?? candidate.recommendation_reason,
        delta,
      ),
      status: candidate.is_recommended ? ("recommended" as const) : ("rejected" as const),
      ...alternativeCardFields(candidate, nowMs),
    });
  }

  return alternatives;
}

/* Passenger-facing fallback when the backend supplied no reason — derived
   from the time delta, never from internal scoring text. */
function fallbackAlternateReason(delta: string): string {
  const diff = Number(delta.match(/[+-]?\d+/)?.[0]);
  if (Number.isFinite(diff) && diff > 0) return `Slower by ${diff} min`;
  if (Number.isFinite(diff) && diff < 0) return "Faster · lower reliability";
  return "Similar time";
}

const REASON_NUMBER_WORDS: Record<string, number> = {
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
  eleven: 11,
  twelve: 12,
  thirteen: 13,
  fourteen: 14,
  fifteen: 15,
  sixteen: 16,
  seventeen: 17,
  eighteen: 18,
  nineteen: 19,
  twenty: 20,
};

function reasonMinutes(value: string, delta: string): number | null {
  const numeric = value.match(/\b(\d+)\s*(?:min|minute|minutes)\b/i);
  if (numeric) return Number(numeric[1]);
  const word = value.match(
    /\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s*(?:min|minute|minutes)\b/i,
  )?.[1];
  if (word) return REASON_NUMBER_WORDS[word.toLowerCase()] ?? null;
  const diff = Number(delta.match(/[+-]?\d+/)?.[0]);
  return Number.isFinite(diff) && diff !== 0 ? Math.abs(diff) : null;
}

function extraTransferCount(value: string): number | null {
  const numeric = value.match(/\b(\d+)\s+extra\s+transfer/i);
  if (numeric) return Number(numeric[1]);
  const word = value.match(
    /\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+extra\s+transfer/i,
  )?.[1];
  return word ? REASON_NUMBER_WORDS[word.toLowerCase()] ?? null : null;
}

export function trimReason(value: string): string {
  return value
    .replace(/\s+/g, " ")
    .replace(/\babout\s+/gi, "")
    .replace(/\bapproximately\s+/gi, "")
    .replace(/\bunder current service conditions\b/gi, "service conditions")
    .replace(/\bunder current conditions\b/gi, "current conditions")
    .replace(/\bminutes\b/gi, "min")
    .replace(/\bminute\b/gi, "min")
    .replace(/[.!]+$/g, "")
    .trim();
}

function trimCause(value: string): string {
  return trimReason(value)
    .replace(/^an?\s+alert:\s*/i, "")
    .replace(/^active\s+/i, "")
    .replace(/^reported\s+/i, "")
    .trim();
}

function mentionsInternalCandidate(value: string): boolean {
  return /\b(?:route|candidate|option)\s+#?\d+\b/i.test(value);
}

function disruptionCause(value: string): string | null {
  const match = value.match(
    /\b(?:and|but)?\s*(?:affected by|due to|because of)\s+(.+)$/i,
  );
  if (!match) return null;
  const cause = trimCause(match[1]);
  if (!cause) return null;
  if (/^(?:current|service)\s+conditions$/i.test(cause)) return null;
  return cause.length > 34 ? `${cause.slice(0, 31).trim()}...` : cause;
}

export function normalizeAlternateReason(
  reason: string | null | undefined,
  delta: string,
): string {
  const cleaned = trimReason(reason ?? fallbackAlternateReason(delta));
  if (!cleaned) return fallbackAlternateReason(delta);
  if (mentionsInternalCandidate(cleaned)) return fallbackAlternateReason(delta);
  const lower = cleaned.toLowerCase();

  if (/same route.*depart|departing later|later departure/.test(lower)) {
    return "Later departure";
  }

  const transferCount = extraTransferCount(cleaned);
  if (transferCount) {
    return `${transferCount} extra transfer${transferCount === 1 ? "" : "s"}`;
  }

  if (/\bmore walking\b/.test(lower)) return "More walking";

  if (/\bslower\b/.test(lower)) {
    const minutes = reasonMinutes(cleaned, delta);
    if (minutes) {
      const cause = disruptionCause(cleaned);
      const serviceNote = cause
        ? ` · ${cause}`
        : /\bservice conditions\b/.test(lower)
        ? " · service conditions"
        : "";
      return `Slower by ${minutes} min${serviceNote}`;
    }
    return "Slower route";
  }

  if (/\bfaster\b/.test(lower)) {
    const minutes = reasonMinutes(cleaned, delta);
    const cause = disruptionCause(cleaned);
    if (minutes && cause) return `Faster by ${minutes} min · ${cause}`;
    if (minutes && /\bextra transfer\b/.test(lower)) {
      return `Faster by ${minutes} min · extra transfer`;
    }
  }

  if (/\baffected by delays\b|\bdelays?\b|\bdelayed\b/.test(lower)) {
    return "Affected by delays";
  }

  if (/\bfaster\b/.test(lower) && /\breliability\b/.test(lower)) {
    return "Faster · lower reliability";
  }

  if (/\bsimilar time\b/.test(lower)) return "Similar time";

  return cleaned.length > 44 ? fallbackAlternateReason(delta) : cleaned;
}
