import type { IncidentType } from "./incident-marker-types";
import { INCIDENT_MARKER_TOKENS, normalizeIncidentType } from "./incident-marker-tokens";
import { MTA_ROUTE_COLORS } from "@/lib/mta-colors";

export interface IncidentPopupViewModel {
  id: string;
  type: IncidentType;
  label: string;
  category: string;
  title: string;
  station: string;
  description: string;
  routeIds: string[];
  active: boolean;
  accentColor: string;
  source: string;
  elapsedLabel: string;
}

type IncidentPopupProperties = {
  id?: unknown;
  incident_type?: unknown;
  title?: unknown;
  description?: unknown;
  station?: unknown;
  route_ids?: unknown;
  active?: unknown;
  category?: unknown;
  source?: unknown;
  time_ago_sec?: unknown;
  hue?: unknown;
};

// Single source of truth lives in lib/mta-colors.json.
const ROUTE_COLORS: Record<string, string> = MTA_ROUTE_COLORS;

function asString(value: unknown, fallback = "") {
  if (value == null) return fallback;
  const text = String(value).trim();
  return text.length > 0 ? text : fallback;
}

function asBoolean(value: unknown) {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") return value === "true";
  return Boolean(value);
}

function routeIdsFromProperty(value: unknown) {
  return asString(value)
    .split(",")
    .map((routeId) => routeId.trim())
    .filter(Boolean)
    .slice(0, 5);
}

export function escapeHtml(value: unknown) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    switch (char) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      case "'":
        return "&#39;";
      default:
        return char;
    }
  });
}

export function formatIncidentElapsed(seconds: unknown) {
  const safeSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = safeSeconds % 60;
  if (minutes < 60) {
    return `T+${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `T+${hours}h ${String(remainingMinutes).padStart(2, "0")}m`;
}

export function incidentFeatureToPopupViewModel(
  properties: IncidentPopupProperties | null | undefined,
): IncidentPopupViewModel {
  const type = normalizeIncidentType(asString(properties?.incident_type, "general"));
  const token = INCIDENT_MARKER_TOKENS[type];
  const title = asString(properties?.title, token.label);
  const station = asString(properties?.station, "Nearby subway corridor");
  return {
    id: asString(properties?.id, "incident"),
    type,
    label: token.label,
    category: asString(properties?.category, token.category),
    title,
    station,
    description: asString(properties?.description, "Live incident intelligence is updating for this marker."),
    routeIds: routeIdsFromProperty(properties?.route_ids),
    active: asBoolean(properties?.active),
    accentColor: asString(properties?.hue, token.color),
    source: asString(properties?.source, "ATLAS INTEL"),
    elapsedLabel: formatIncidentElapsed(properties?.time_ago_sec),
  };
}

function routeBulletHtml(routeId: string) {
  const normalizedRouteId = routeId.toUpperCase();
  const background = ROUTE_COLORS[normalizedRouteId] ?? "#8a92ab";
  const foreground = background === "#FCCC0A" || background === "#A7A9AC" ? "#111827" : "#ffffff";
  return `<span class="sr-incident-popup__line" style="--sr-line-bg:${escapeHtml(background)};--sr-line-fg:${escapeHtml(foreground)}">${escapeHtml(normalizedRouteId)}</span>`;
}

export function renderIncidentPopupHtml(model: IncidentPopupViewModel) {
  const locationParts = model.station.split("·").map((part) => part.trim()).filter(Boolean);
  const primaryLocation = locationParts[0] ?? model.station;
  const secondaryLocation = locationParts.slice(1).join(" · ");
  const linesHtml =
    model.routeIds.length > 0
      ? model.routeIds.map(routeBulletHtml).join("")
      : `<span class="sr-incident-popup__line sr-incident-popup__line--empty">--</span>`;

  return `
    <article class="sr-incident-popup" style="--sr-incident-popup-accent:${escapeHtml(model.accentColor)}">
      <div class="sr-incident-popup__bg" aria-hidden="true"></div>
      <div class="sr-incident-popup__beam" aria-hidden="true"></div>
      <div class="sr-incident-popup__rail" aria-hidden="true"></div>
      <div class="sr-incident-popup__content">
        <div class="sr-incident-popup__row">
          <span class="sr-incident-popup__category">${escapeHtml(model.category)}</span>
          <span class="sr-incident-popup__time">${escapeHtml(model.elapsedLabel)}</span>
        </div>
        <h3 class="sr-incident-popup__headline">${escapeHtml(model.title)}</h3>
        <p class="sr-incident-popup__loc">
          <b>${escapeHtml(primaryLocation)}</b>${secondaryLocation ? ` · ${escapeHtml(secondaryLocation)}` : ""}
        </p>
        <div class="sr-incident-popup__rule" aria-hidden="true"></div>
        <p class="sr-incident-popup__summary">${escapeHtml(model.description)}</p>
        <div class="sr-incident-popup__meta">
          <span class="sr-incident-popup__src">${escapeHtml(model.source)}</span>
          <span class="sr-incident-popup__lines" aria-label="Affected routes">${linesHtml}</span>
        </div>
        <div class="sr-incident-popup__status" aria-label="${model.active ? "Incident active" : "Incident advisory"}">
          <span class="sr-incident-popup__status-label">${model.active ? "LIVE INCIDENT" : "INCIDENT ADVISORY"}</span>
          <span class="sr-incident-popup__status-arrow">→</span>
        </div>
      </div>
    </article>
  `.trim();
}
