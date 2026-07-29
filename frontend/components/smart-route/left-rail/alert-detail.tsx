import type { ReactNode } from "react";
import {
  Activity,
  ArrowLeftRight,
  MapPin,
  TramFront,
  TriangleAlert,
} from "lucide-react";
import { AlertRouteBadgeGroup } from "./alert-badges";
import { isSystemwideAlert } from "./alert-view-model";
import { TransitText } from "./atoms";
import type {
  AlertFeedItem,
  AlertFeedSeverity,
  AlertUpdateEntry,
} from "./types";

export type AlertDetailView = {
  impact?: string;
  alternatives?: string;
  statusText?: string;
  stops: string[];
  updates: AlertUpdateEntry[];
};

type AlertDetailPanelProps = {
  item: AlertFeedItem;
  detail: AlertDetailView;
};

export function AlertDetailPanel({ item, detail }: AlertDetailPanelProps) {
  return (
    <div className="sr-alert-detail">
      {detail.impact && (
        <DetailRow
          icon={<TriangleAlert size={14} strokeWidth={1.8} />}
          label="Impact"
        >
          <TransitText text={detail.impact} bulletSize={13} mode="paragraph" />
        </DetailRow>
      )}
      {!isSystemwideAlert(item) && item.routeIds.length > 0 && (
        <DetailRow
          icon={<TramFront size={14} strokeWidth={1.8} />}
          label="Affected service"
        >
          <span className="sr-alert-detail__routes">
            <AlertRouteBadgeGroup routeIds={item.routeIds} limit={6} size={18} />
            <span>{item.serviceName}</span>
          </span>
        </DetailRow>
      )}
      {detail.alternatives && (
        <DetailRow
          icon={<ArrowLeftRight size={14} strokeWidth={1.8} />}
          label="Travel alternatives"
        >
          <TransitText
            text={detail.alternatives}
            bulletSize={13}
            mode="paragraph"
          />
        </DetailRow>
      )}
      {detail.statusText && (
        <DetailRow
          icon={<Activity size={14} strokeWidth={1.8} />}
          label="Current status"
        >
          <TransitText text={detail.statusText} bulletSize={13} mode="paragraph" />
        </DetailRow>
      )}
      {detail.stops.length > 0 && (
        <DetailRow
          icon={<MapPin size={14} strokeWidth={1.8} />}
          label={detail.stops.length > 1 ? "Affected stops" : "Affected stop"}
        >
          <span className="sr-alert-row__stops">
            {detail.stops.slice(0, 8).map((stop) => (
              <span key={stop}>{stop}</span>
            ))}
          </span>
        </DetailRow>
      )}
      {detail.updates.length > 0 && <AlertUpdateTimeline entries={detail.updates} />}
    </div>
  );
}

export function buildAlertDetailView(
  item: AlertFeedItem,
): AlertDetailView | null {
  const detail = item.details;
  if (!detail) {
    return null;
  }

  const summary = detailSummary(item);
  const { impact, alternatives } = disruptionAndGuidance(item, summary);
  let statusText = distinctStatus(item, summary);
  const stops = detail.affectedStops ?? [];
  const updates = (detail.updates ?? []).filter(
    (update) => Boolean(update.title?.trim() || update.summary?.trim()),
  );
  let hasDetail = Boolean(
    impact || alternatives || statusText || stops.length > 0 || updates.length > 0,
  );

  if (!hasDetail && item.expandable) {
    statusText =
      detail.currentStatus?.trim() ??
      detail.whatHappened?.trim() ??
      item.summary?.trim() ??
      undefined;
    hasDetail = Boolean(statusText);
  }

  return hasDetail
    ? { impact, alternatives, statusText, stops, updates }
    : null;
}

export function featuredAlertBody(item: AlertFeedItem): string | undefined {
  const visibleTitle = item.title.toLowerCase();
  const candidates = [
    item.details?.impact,
    item.details?.currentStatus,
    item.summary,
  ].filter((value): value is string => {
    if (typeof value !== "string" || !value.trim()) {
      return false;
    }

    return !isGenericAlertCopy(value);
  });
  const fallbackSummary =
    item.summary && !isGenericAlertCopy(item.summary) ? item.summary : undefined;
  const body =
    candidates.find((value) => !visibleTitle.includes(value.toLowerCase())) ??
    fallbackSummary;

  return body ? stripTitleEcho(body, item.title) : undefined;
}

export function featuredAlertAdvice(
  item: AlertFeedItem,
  bodyText: string | undefined,
): string | undefined {
  const alternatives = item.details?.alternatives?.trim();
  if (!alternatives) {
    return undefined;
  }

  const key = alternatives.toLowerCase().replace(/[.!?]+$/, "");
  return key && `${item.title} ${bodyText ?? ""}`.toLowerCase().includes(key)
    ? undefined
    : alternatives;
}

export function lineAlertSubtitle(item: AlertFeedItem): string | undefined {
  const candidates = [
    item.summary,
    item.details?.impact,
    item.details?.currentStatus,
    item.routeIds.length > 2 ? "Multiple lines affected" : item.serviceName,
  ].filter((value): value is string => {
    if (typeof value !== "string" || !value.trim()) {
      return false;
    }

    return !isGenericAlertCopy(value);
  });
  const visibleTitle = item.title.toLowerCase();

  return candidates.find((value) => !visibleTitle.includes(value.toLowerCase()));
}

export function shortAlertTimeLabel(label: string): string | undefined {
  if (!label || ["now", "live", "just now"].includes(label.toLowerCase())) {
    return undefined;
  }

  const minutes = label.match(/^(\d+)m$/);
  if (minutes) {
    return `${minutes[1]} min ago`;
  }

  const hours = label.match(/^(\d+)h$/);
  return hours ? `${hours[1]} hr ago` : label.charAt(0).toUpperCase() + label.slice(1);
}

export function alertSeverityLabel(severity: AlertFeedSeverity): string {
  if (severity === "planned") {
    return "Planned work";
  }
  if (severity === "minor") {
    return "Minor delay";
  }
  if (severity === "major") {
    return "Major disruption";
  }
  if (severity === "suspension") {
    return "Suspension";
  }

  return severity === "incident" ? "Nearby incident" : "Service change";
}

function disruptionAndGuidance(
  item: AlertFeedItem,
  raw = detailSummary(item),
): { impact?: string; alternatives?: string } {
  const parsed = item.details?.alternatives?.trim();
  if (!raw) {
    return { impact: undefined, alternatives: parsed };
  }

  const match = raw.match(
    /\b(for service to these stations|for alternative service|take (?:the|a|nearby)\b|use (?:nearby|the)\b|board the\b|or take\b)/i,
  );
  if (!match || match.index === undefined) {
    return {
      impact: raw,
      alternatives:
        parsed && !raw.toLowerCase().includes(parsed.toLowerCase())
          ? parsed
          : undefined,
    };
  }

  const impact = tidySentence(raw.slice(0, match.index));
  const alternatives = tidySentence(
    raw.slice(match.index).replace(/^[\s,;:.-]+/, ""),
  );
  return { impact, alternatives: alternatives ?? parsed };
}

type DetailRowProps = {
  icon: ReactNode;
  label: string;
  children: ReactNode;
};

function DetailRow({ icon, label, children }: DetailRowProps) {
  return (
    <div className="sr-alert-detail__row">
      <span className="sr-alert-detail__icon" aria-hidden="true">
        {icon}
      </span>
      <span className="sr-alert-detail__key">{label}</span>
      <span className="sr-alert-detail__value">{children}</span>
    </div>
  );
}

function AlertUpdateTimeline({ entries }: { entries: AlertUpdateEntry[] }) {
  return (
    <div className="sr-alert-detail__timeline" aria-label="Earlier alert updates">
      {entries.slice(0, 4).map((update, index) => (
        <span
          key={`${update.time}-${update.title}-${index}`}
          className="sr-alert-detail__update"
          data-tone={update.tone ?? "muted"}
        >
          <span className="sr-alert-detail__update-dot" aria-hidden="true" />
          <span className="sr-alert-detail__update-copy">
            <strong>{update.title}</strong>
            {update.summary && (
              <small>
                <TransitText
                  text={update.summary}
                  bulletSize={12}
                  mode="paragraph"
                />
              </small>
            )}
          </span>
          {update.time && (
            <time>{shortAlertTimeLabel(update.time) ?? update.time}</time>
          )}
        </span>
      ))}
    </div>
  );
}

function detailSummary(item: AlertFeedItem): string | undefined {
  const fuller = item.details?.impact ?? item.details?.currentStatus;
  return fuller &&
    !`${item.title} ${item.summary ?? ""}`
      .toLowerCase()
      .includes(fuller.toLowerCase())
    ? fuller
    : undefined;
}

function distinctStatus(
  item: AlertFeedItem,
  impact: string | undefined,
): string | undefined {
  const status = item.details?.currentStatus;
  if (!status || impact?.toLowerCase().includes(status.toLowerCase())) {
    return undefined;
  }

  return `${item.title} ${item.summary ?? ""}`
    .toLowerCase()
    .includes(status.toLowerCase())
    ? undefined
    : status;
}

function tidySentence(value: string): string | undefined {
  const trimmed = value.replace(/[\s,;:-]+$/, "").trim();
  if (!trimmed) {
    return undefined;
  }

  const cased = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(cased) ? cased : `${cased}.`;
}

function stripTitleEcho(value: string, title: string): string | undefined {
  const valueText = value.trim();
  const titleText = title.trim();
  if (!titleText || !valueText.toLowerCase().startsWith(titleText.toLowerCase())) {
    return valueText;
  }

  const rest = valueText.slice(titleText.length).replace(/^[\s.,:;-]+/, "");
  return rest ? rest.charAt(0).toUpperCase() + rest.slice(1) : undefined;
}

function isGenericAlertCopy(value: string): boolean {
  return [
    "active service notice",
    "service change in effect.",
    "service change in effect",
    "trains are running with delays.",
    "trains are running with delays",
  ].includes(value.trim().toLowerCase());
}
