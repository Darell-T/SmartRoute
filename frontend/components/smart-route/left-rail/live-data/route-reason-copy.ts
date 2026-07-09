import type { RouteCandidate, RouteStep as ApiRouteStep } from "@/types/api";
import {
  candidateDelta,
  candidateSignature,
  firstTransitStep,
  normalizeAlternateReason,
  transitRouteIdsFromSteps,
  trimReason,
} from "./route-candidates";

function derivePublicRationale(
  candidate: RouteCandidate | null | undefined,
  steps: ApiRouteStep[] | undefined,
): string {
  const parts: string[] = [
    candidate?.is_recommended === false
      ? "Alternative route"
      : "Fastest available option",
  ];
  const departsIn = firstTransitStep(steps)?.minutes_until_train_arrives;
  if (typeof departsIn === "number" && Number.isFinite(departsIn)) {
    parts.push(`live arrival in ${Math.max(1, Math.round(departsIn))} min`);
  }
  const activeAlerts = candidate?.score_breakdown?.active_alerts;
  if (activeAlerts === 0) {
    parts.push("no service alerts");
  } else if (typeof activeAlerts === "number" && activeAlerts > 0) {
    parts.push(
      `${activeAlerts} service alert${activeAlerts === 1 ? "" : "s"} on route`,
    );
  }
  return `${parts.join(" · ")}.`;
}

function normalizeRecommendationReason(
  reason: string | null | undefined,
  routeCandidates?: RouteCandidate[],
): string {
  const cleaned = trimReason(publicRecommendationText(reason))
    .replace(/^recommend(?:ed|ation)?\s*:\s*/i, "")
    .replace(/^because\s+/i, "")
    .replace(/\boption\b/gi, "route")
    .replace(/\s+·\s+/g, " · ")
    .trim();
  if (!cleaned) return "";

  const withoutMarkdown = cleaned
    .replace(/[#*_`|]/g, "")
    .replace(/\[\/?CANDIDATE_ANALYSIS\]/gi, "")
    .replace(/\[ROUTE:\d+\]/gi, "")
    .replace(/\s+/g, " ")
    .trim();
  if (
    !withoutMarkdown
    || /analysis|comparison|provided transit data|json|payload|route index/i.test(withoutMarkdown)
  ) {
    return "";
  }

  const clipped =
    withoutMarkdown.length > 120
      ? `${withoutMarkdown.slice(0, 119).trim()}...`
      : withoutMarkdown;
  const riderFacing = sanitizeIndexedRouteReferences(
    collapseRepeatedLineReferences(clipped),
    routeCandidates,
  );
  if (!riderFacing || /\b(?:route|candidate|option)\s+#?\d+\b/i.test(riderFacing)) {
    return "";
  }
  const cased = riderFacing.charAt(0).toUpperCase() + riderFacing.slice(1);
  return /[.!?]$/.test(cased) ? cased : `${cased}.`;
}

function cleanStopLabel(value: string | undefined): string {
  return (value ?? "")
    .replace(/\s+/g, " ")
    .replace(/\s+-\s+/g, "-")
    .trim();
}

function transitSteps(candidate: RouteCandidate | null | undefined): ApiRouteStep[] {
  return (candidate?.steps ?? []).filter(
    (step) => step.type === "SUBWAY" || step.type === "BUS",
  );
}

function routeIdsKey(candidate: RouteCandidate | null | undefined): string {
  return transitRouteIdsFromSteps(candidate?.steps ?? []).join("/");
}

function candidateDisplayLabel(
  candidate: RouteCandidate | null | undefined,
  routeCandidates?: RouteCandidate[],
): string {
  const transit = transitSteps(candidate);
  const routeIds = transitRouteIdsFromSteps(candidate?.steps ?? []);
  if (routeIds.length === 0) return "another route";

  const modes = new Set(transit.map((step) => step.type));
  const first = transit[0];
  const primaryKey = routeIdsKey(candidate);
  const duplicates =
    routeCandidates?.filter((row) => routeIdsKey(row) === primaryKey).length ?? 0;
  const routeLabel = routeIds.join("/");
  const base =
    modes.size === 1 && modes.has("BUS")
      ? `${routeLabel} bus option`
      : routeIds.length === 1
        ? `${routeLabel} route`
        : `${routeLabel} subway option`;
  const transferStop = cleanStopLabel(transit[1]?.departure_stop);
  const boardStop = cleanStopLabel(first?.departure_stop);

  if (routeIds.length > 1 && transferStop) {
    return `the ${base} via ${transferStop}`;
  }
  if (duplicates > 1 && boardStop) {
    return `the ${base} from ${boardStop}`;
  }
  return `the ${base}`;
}

function indexedCandidateLabel(
  index: number,
  routeCandidates?: RouteCandidate[],
): string {
  const candidate =
    routeCandidates?.find((row) => row.index === index) ?? routeCandidates?.[index];
  return candidate ? candidateDisplayLabel(candidate, routeCandidates) : "that option";
}

function sanitizeIndexedRouteReferences(
  value: string,
  routeCandidates?: RouteCandidate[],
): string {
  return value
    .replace(/\b(?:route|candidate|option)\s+#?(\d+)\b/gi, (_match, rawIndex: string) =>
      indexedCandidateLabel(Number(rawIndex), routeCandidates),
    )
    .replace(/\broute\s+index\b/gi, "route")
    .replace(/\s+/g, " ")
    .trim();
}

function collapseRepeatedLineReferences(value: string): string {
  return value.replace(
    /\bthe\s+([A-Z0-9]{1,4})(?:\s+(?:train|route))?\s+and\s+the\s+\1(?:\s+(?:train|route))?\b/g,
    "the $1 alternatives",
  );
}

function whyNotPhrase(
  candidate: RouteCandidate,
  reason: string,
  routeCandidates?: RouteCandidate[],
): string {
  const line = candidateDisplayLabel(candidate, routeCandidates);
  const lower = reason.charAt(0).toLowerCase() + reason.slice(1);
  if (/^\d+ extra transfer/.test(lower)) {
    return `${line} because it adds ${lower}`;
  }
  if (/^more walking/.test(lower)) {
    return `${line} because it has more walking`;
  }
  if (/^later departure/.test(lower)) {
    return `${line} because it leaves later`;
  }
  if (/^affected by delays/.test(lower)) {
    return `${line} because it is affected by delays`;
  }
  const fasterRisk = reason.match(/^faster by (\d+) min · (.+)$/i);
  if (fasterRisk) {
    return `${line} because it is affected by ${fasterRisk[2]} despite being ${fasterRisk[1]} min faster`;
  }
  if (/^faster/.test(lower)) {
    return `${line} because it trades speed for lower reliability`;
  }
  if (/^slower/.test(lower)) {
    return `${line} because it is ${lower}`;
  }
  return `${line} because ${lower}`;
}

function buildWhyNotSentence(
  activeCandidate: RouteCandidate,
  routeCandidates: RouteCandidate[] | undefined,
): string {
  if (!routeCandidates?.length) return "";
  const seen = new Set<string>();
  const reasons: string[] = [];
  const activeSignature = candidateSignature(activeCandidate);

  for (const candidate of routeCandidates) {
    if (candidate.id === activeCandidate.id) continue;
    const signature = candidateSignature(candidate);
    if (signature === activeSignature || seen.has(signature)) continue;
    seen.add(signature);
    const delta = candidateDelta(candidate, activeCandidate).delta;
    const reason = normalizeAlternateReason(
      candidate.rejection_reason ?? candidate.recommendation_reason,
      delta,
    );
    if (!reason || /similar time/i.test(reason)) continue;
    reasons.push(whyNotPhrase(candidate, reason, routeCandidates));
    if (reasons.length >= 2) break;
  }

  if (reasons.length === 0) return "";
  if (reasons.length === 1) return `I did not pick ${reasons[0]}.`;
  return `I did not pick ${reasons[0]} or ${reasons[1]}.`;
}

export function buildVisibleRouteReason(
  candidate: RouteCandidate,
  steps: ApiRouteStep[] | undefined,
  routeCandidates: RouteCandidate[] | undefined,
): string {
  const modelReason = normalizeRecommendationReason(
    candidate.is_recommended === false
      ? candidate.rejection_reason || candidate.recommendation_reason
      : candidate.recommendation_reason,
    routeCandidates,
  );
  const primary = modelReason || derivePublicRationale(candidate, steps);
  const whyNot =
    candidate.is_recommended === false
      ? ""
      : buildWhyNotSentence(candidate, routeCandidates);
  return [primary, whyNot].filter(Boolean).join(" ");
}

export function publicRecommendationText(text: string | null | undefined): string {
  const cleaned = text
    ?.replace(/\bATLAS\b/gi, "SmartRoute")
    .replace(/\bVery well,\s*/i, "")
    .replace(/,\s*sir\.?/i, ".")
    .replace(/\bsir\.?\s*/i, "")
    .trim();
  return cleaned ?? "";
}
