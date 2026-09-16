import type { RouteCandidate, RouteStep as ApiRouteStep } from "@/types/api";
import { isTransitStep } from "@/lib/route-planning";
import type { Alternative } from "../types";
import { cleanDestinationLabel, formatClockAt } from "./formatters";
import { stripFromSteps } from "./route-steps";
import type { CandidateDelta } from "./types";

export function clockFromIso(iso: string | null | undefined): string | null {
  const trimmed = iso?.trim();
  if (!trimmed) return null;
  const parsed = Date.parse(trimmed);
  if (!Number.isFinite(parsed)) return null;
  return formatClockAt(parsed) || null;
}

export function canonicalDurationMinutes(
  candidate: RouteCandidate | null | undefined,
): number | null {
  const seconds = candidate?.itinerary?.total_duration_seconds;
  if (seconds != null && Number.isFinite(seconds) && seconds >= 0) {
    return Math.round(seconds / 60);
  }
  const minutes = candidate?.total_minutes;
  if (minutes != null && Number.isFinite(minutes) && minutes >= 0) {
    return Math.round(minutes);
  }
  return null;
}

export function canonicalTransferCount(
  candidate: RouteCandidate | null | undefined,
): number | undefined {
  const count = candidate?.itinerary?.transfer_count;
  if (count != null && Number.isFinite(count) && count >= 0) {
    return Math.round(count);
  }
  return undefined;
}

export function candidateDelta(
  candidate: RouteCandidate,
  active: RouteCandidate | null | undefined,
): CandidateDelta {
  const candidateEta = canonicalDurationMinutes(candidate);
  const activeEta = canonicalDurationMinutes(active);
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
  return steps?.find(isTransitStep);
}

export function candidateSignature(candidate: RouteCandidate | null | undefined): string {
  const transitSteps = (candidate?.steps ?? []).filter(isTransitStep);
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
    if (!isTransitStep(step)) continue;
    const id = (step.route_id || step.train_line || "").trim().toUpperCase();
    if (id && !ids.includes(id)) ids.push(id);
  }
  return ids;
}

function finiteMinutes(value: number | undefined): number | undefined {
  if (value == null || !Number.isFinite(value)) return undefined;
  return Math.max(0, Math.round(value));
}

function candidateArriveLabel(candidate: RouteCandidate): string | undefined {
  const fromItinerary = clockFromIso(candidate.itinerary?.arrival_at);
  if (fromItinerary) return fromItinerary;
  return clockFromIso(candidate.arrival_at) ?? undefined;
}

function alternativeCardFields(
  candidate: RouteCandidate,
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
  const transitSteps = (candidate.steps ?? []).filter(isTransitStep);
  const first = transitSteps[0];
  const last = transitSteps.at(-1);
  return {
    lines: transitRouteIdsFromSteps(candidate.steps),
    totalMinutes: canonicalDurationMinutes(candidate) ?? undefined,
    departsInMinutes: finiteMinutes(first?.minutes_until_train_arrives),
    leavesLabel: clockFromIso(candidate.itinerary?.departure_at) ?? undefined,
    arriveLabel: candidateArriveLabel(candidate),
    fromStop: cleanDestinationLabel(first?.departure_stop) || undefined,
    toStop: cleanDestinationLabel(last?.arrival_stop) || undefined,
    strip: stripFromSteps(candidate.steps),
  };
}

function alternateLine(candidate: RouteCandidate): string {
  const transit = firstTransitStep(candidate.steps);
  return (transit?.route_id || transit?.train_line || "WALK").toUpperCase();
}

function laterDepartureAlternate(
  candidate: RouteCandidate,
  delta: CandidateDelta,
): Alternative | null {
  if (delta.delta === "same time" || delta.delta === "n/a") return null;
  return {
    id: candidate.id,
    line: alternateLine(candidate),
    dest: "Later departure",
    delta: delta.delta,
    sev: delta.sev,
    reason: "Later departure",
    status: "rejected",
    ...alternativeCardFields(candidate),
  };
}

function distinctAlternate(
  candidate: RouteCandidate,
  delta: CandidateDelta,
): Alternative {
  const transit = firstTransitStep(candidate.steps);
  return {
    id: candidate.id,
    line: alternateLine(candidate),
    dest:
      cleanDestinationLabel(transit?.direction || transit?.arrival_stop)
      || "Alternate routing",
    delta: delta.delta,
    sev: delta.sev,
    reason: normalizeAlternateReason(
      candidate.rejection_reason ?? candidate.recommendation_reason,
    ),
    status: candidate.is_recommended ? "recommended" : "rejected",
    ...alternativeCardFields(candidate),
  };
}

export function buildAlternatives(
  routeCandidates: RouteCandidate[] | undefined,
  activeRouteCandidate: RouteCandidate | null | undefined,
): Alternative[] {
  if (!routeCandidates?.length || !activeRouteCandidate) return [];
  const activeSignature = candidateSignature(activeRouteCandidate);
  const seenSignatures = new Set<string>();
  const alternatives: Alternative[] = [];

  for (const candidate of routeCandidates) {
    if (candidate.id === activeRouteCandidate.id) continue;
    const signature = candidateSignature(candidate);
    if (seenSignatures.has(signature)) continue;
    const delta = candidateDelta(candidate, activeRouteCandidate);
    const alternate =
      signature === activeSignature
        ? laterDepartureAlternate(candidate, delta)
        : distinctAlternate(candidate, delta);
    if (!alternate) continue;
    seenSignatures.add(signature);
    alternatives.push(alternate);
  }

  return alternatives;
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

function mentionsInternalCandidate(value: string): boolean {
  return /\b(?:route|candidate|option)\s+#?\d+\b/i.test(value);
}

export function normalizeAlternateReason(reason: string | null | undefined): string {
  const cleaned = trimReason(reason ?? "");
  if (!cleaned || mentionsInternalCandidate(cleaned)) return "";
  const lower = cleaned.toLowerCase();
  if (/same route.*depart|departing later|later departure/.test(lower)) {
    return "Later departure";
  }
  if (/\bmore walking\b/.test(lower)) return "More walking";
  return cleaned.length > 44 ? `${cleaned.slice(0, 41).trim()}...` : cleaned;
}
