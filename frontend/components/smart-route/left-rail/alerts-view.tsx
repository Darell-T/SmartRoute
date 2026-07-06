"use client";

import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Activity,
  ArrowLeftRight,
  ChevronRight,
  MapPin,
  Navigation,
  TramFront,
  TriangleAlert,
} from "lucide-react";
import {
  ALERT_ROUTE_TO_FAMILY,
  latestAlertUpdateLabel,
  normalizeAlertFeedItems,
} from "./alert-feed";
import { BusChip, RouteBullet, RouteBulletGroup, TransitText } from "./atoms";
import type {
  AlertFeedItem,
  AlertFeedSeverity,
  FeedEvent,
  ServiceAlert,
} from "./types";
import { LINE_COLORS } from "./types";
import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";

const SYSTEMWIDE_ROUTE_THRESHOLD = 8;
const FEATURED_LIMIT = 2;

type AlertLineGroupModel = {
  id: string;
  name: string;
  routeIds: string[];
  items: AlertFeedItem[];
  firstIndex: number;
  rank: number;
};

function isSystemwide(item: AlertFeedItem): boolean {
  return item.routeIds.length >= SYSTEMWIDE_ROUTE_THRESHOLD;
}

export function AlertsView({
  alerts,
  feed,
  nearbyRouteIds = [],
}: {
  alerts: ServiceAlert[];
  feed: FeedEvent[];
  nearbyRouteIds?: string[];
}) {
  // Resolved alerts drop off the board entirely — the rail shows what's
  // affecting service now, not a settled-history log.
  const items = useMemo(
    () =>
      normalizeAlertFeedItems(alerts, feed).filter(
        (item) => item.lifecycle !== "resolved",
      ),
    [alerts, feed],
  );
  const updatedLabel = useMemo(() => latestAlertUpdateLabel(items), [items]);

  const { featured, rest } = useMemo(() => {
    const near = new Set(nearbyRouteIds.map((route) => route.toUpperCase()));
    const eligible = items.filter(
      (item) =>
        !isSystemwide(item) &&
        item.routeIds.some((route) => near.has(route)),
    );
    const ranked = [...eligible].sort((left, right) => {
      const leftNear = left.routeIds.some((route) => near.has(route)) ? 1 : 0;
      const rightNear = right.routeIds.some((route) => near.has(route)) ? 1 : 0;
      return rightNear - leftNear;
    });
    const lead = ranked.slice(0, FEATURED_LIMIT);
    const chosen = new Set(lead.map((item) => item.id));
    return {
      featured: lead,
      rest: items.filter((item) => !chosen.has(item.id)),
    };
  }, [items, nearbyRouteIds]);
  const otherAlertGroups = useMemo(
    () => groupAlertItemsByLine(rest),
    [rest],
  );

  return (
    <section className="sr-alerts-panel">
      <section className="sr-rail-section sr-alerts-header">
        <h1 className="sr-rail-title">Service alerts</h1>
        {updatedLabel && (
          <p className="sr-alerts-updated">
            <Navigation
              className="sr-alerts-updated__icon"
              size={12}
              strokeWidth={0}
              fill="currentColor"
              aria-hidden="true"
            />
            {updatedLabel}
          </p>
        )}
      </section>

      <section className="sr-rail-section sr-alert-feed">
        <div className="sr-alerts-scroll">
          {featured.length > 0 && (
            <section className="sr-alert-group">
              <div className="sr-section-header">
                <h2>Near you</h2>
              </div>
              <ul className="sr-alert-card-list" aria-label="Alerts near you">
                {featured.map((item) => (
                  <AlertCard key={item.id} item={item} />
                ))}
              </ul>
            </section>
          )}

          {rest.length > 0 && (
            <section className="sr-alert-group">
              {featured.length > 0 && <SectionHeader title="Other alerts" />}
              <AlertLineGroupList
                groups={otherAlertGroups}
                aria-label={
                  featured.length > 0
                    ? "Other service alerts"
                    : "All service alerts"
                }
              />
            </section>
          )}

          {items.length === 0 && (
            <div className="sr-empty-row">
              <strong>No active alerts right now.</strong>
              <small>Service updates from today will appear here.</small>
            </div>
          )}
        </div>
      </section>
    </section>
  );
}

function AlertCard({ item }: { item: AlertFeedItem }) {
  const updated = updatedLabelFor(item);
  const bodyText = featuredAlertBody(item);
  const adviceText = featuredAlertAdvice(item, bodyText);
  const stripe = stripeColor(item);
  const stripeStyle = {
    "--sr-alert-stripe-color": stripe,
  } as CSSProperties;

  const body = (
    <>
      <span className="sr-alert-card__toprow">
        <span className="sr-alert-card__identity">
          {isSystemwide(item) ? (
            <span className="sr-alert-systemwide">Systemwide</span>
          ) : item.routeIds.length > 2 ? (
            <RouteBadge routeId={item.routeIds[0]} size={26} />
          ) : (
            <RouteBadgeGroup routeIds={item.routeIds} limit={2} size={26} />
          )}
          <span className="sr-alert-card__service">{item.serviceName}</span>
        </span>
        <StatusMeta item={item} />
      </span>
      <span className="sr-alert-card__headline sr-alert-card__headline--full">
        <TransitText text={item.title} bulletSize={17} />
      </span>
      {bodyText && (
        <span className="sr-alert-card__summary sr-alert-card__summary--full">
          <TransitText text={bodyText} bulletSize={13} />
        </span>
      )}
      {adviceText && (
        <span className="sr-alert-card__advice">
          <TransitText text={adviceText} bulletSize={12} />
        </span>
      )}
      {updated && (
        <span className="sr-alert-card__footer">
          <time>{updated}</time>
        </span>
      )}
    </>
  );

  return (
    <li
      className="sr-alert-card smart-route-liquid-card"
      data-lifecycle={item.lifecycle}
      style={stripeStyle}
    >
      <span className="sr-alert-card__stripe" aria-hidden="true" />
      <div className="sr-alert-card__header" data-static="true">
        {body}
      </div>
    </li>
  );
}

function AlertLineGroupList({
  groups,
  "aria-label": ariaLabel,
}: {
  groups: AlertLineGroupModel[];
  "aria-label": string;
}) {
  return (
    <div
      className="sr-alert-line-group-list"
      role="list"
      aria-label={ariaLabel}
    >
      <AnimatePresence initial={false}>
        {groups.map((group) => (
          <AlertLineGroup key={group.id} group={group} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function AlertLineGroup({ group }: { group: AlertLineGroupModel }) {
  return (
    <motion.article
      className="sr-alert-line-group sr-station-group"
      role="listitem"
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <AlertLineGroupHeader group={group} />
      <ul className="sr-alert-line-list">
        <AnimatePresence initial={false}>
          {group.items.map((item) => (
            <AlertLineRow key={item.id} item={item} />
          ))}
        </AnimatePresence>
      </ul>
    </motion.article>
  );
}

/* One line only: trunk name + quiet count. The rows below each carry their
   own specific route bullet, so repeating the family's bullet strip here was
   pure noise. */
function AlertLineGroupHeader({ group }: { group: AlertLineGroupModel }) {
  const alertCount =
    group.items.length === 1 ? "1 alert" : `${group.items.length} alerts`;

  return (
    <header className="sr-alert-line-header sr-station-header">
      <span className="sr-station-header__title">
        <strong>{group.name}</strong>
        <span className="sr-station-header__walk">{alertCount}</span>
      </span>
    </header>
  );
}

function AlertLineRow({ item }: { item: AlertFeedItem }) {
  const [open, setOpen] = useState(false);
  const reduceMotion = useReducedMotion();
  const detailView = buildAlertDetailView(item);
  const hasDetail = Boolean(detailView);
  const subtitle = lineAlertSubtitle(item);
  const timeLabel = shortTimeLabel(item.timestampLabel);

  const header = (
    <>
      <span className="sr-alert-line-row__media">
        {item.routeIds.length > 0 ? (
          <RouteBadgeGroup routeIds={item.routeIds} limit={3} size={20} />
        ) : (
          <AlertSeverityDot tone={alertDotTone(item)} />
        )}
      </span>
      <span className="sr-alert-line-row__copy">
        <strong
          className="sr-alert-line-row__title"
          title={severityLabel(item.severity)}
        >
          <TransitText text={item.title} bulletSize={13} />
        </strong>
        {subtitle && (
          <small>
            <TransitText text={subtitle} bulletSize={12} />
          </small>
        )}
      </span>
      <span className="sr-alert-line-row__meta">
        {timeLabel && <time>{timeLabel}</time>}
        {hasDetail && (
          <ChevronRight
            className="sr-alert-line-row__chevron"
            size={15}
            strokeWidth={1.8}
            aria-hidden="true"
          />
        )}
      </span>
    </>
  );

  if (!hasDetail) {
    return (
      <li
        className="sr-alert-line-row"
        data-severity={item.severity}
        data-lifecycle={item.lifecycle}
      >
        <div className="sr-alert-line-row__summary" data-static="true">
          {header}
        </div>
      </li>
    );
  }

  return (
    <li
      className="sr-alert-line-row"
      data-severity={item.severity}
      data-lifecycle={item.lifecycle}
      data-open={open ? "true" : "false"}
    >
      <button
        type="button"
        className="sr-alert-line-row__summary"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {header}
      </button>
      <AnimatePresence initial={false}>
        {open && detailView && (
          <motion.div
            key="detail"
            className="sr-alert-detail-wrap sr-alert-line-detail-wrap"
            initial={reduceMotion ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
            style={{ overflow: "hidden" }}
          >
            <AlertDetailPanel item={item} detail={detailView} />
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

function groupAlertItemsByLine(items: AlertFeedItem[]): AlertLineGroupModel[] {
  const groups = new Map<string, AlertLineGroupModel>();

  items.forEach((item, index) => {
    const seed = alertLineGroupSeed(item);
    const existing = groups.get(seed.id);
    if (existing) {
      existing.items.push(item);
      existing.routeIds = mergeRouteIds(existing.routeIds, seed.routeIds);
      existing.firstIndex = Math.min(existing.firstIndex, index);
      existing.rank = Math.min(existing.rank, seed.rank);
      return;
    }

    groups.set(seed.id, {
      ...seed,
      routeIds: uniqueRoutes(seed.routeIds),
      items: [item],
      firstIndex: index,
    });
  });

  return Array.from(groups.values()).sort(
    (left, right) =>
      left.rank - right.rank || left.firstIndex - right.firstIndex,
  );
}

function alertLineGroupSeed(
  item: AlertFeedItem,
): Omit<AlertLineGroupModel, "items" | "firstIndex"> {
  const routeIds = uniqueRoutes(item.routeIds);
  if (isSystemwide(item) || routeIds.length === 0) {
    return {
      id: "systemwide",
      name: "Systemwide",
      routeIds,
      rank: 900,
    };
  }

  const subwayRoutes = routeIds.filter((routeId) =>
    SUBWAY_BULLET_ROUTES.has(routeId),
  );
  const busRoutes = routeIds.filter(
    (routeId) => !SUBWAY_BULLET_ROUTES.has(routeId),
  );
  const families = uniqueFamilyIds(subwayRoutes);

  if (families.length === 1 && busRoutes.length === 0) {
    const family = ALERT_ROUTE_TO_FAMILY.get(subwayRoutes[0]);
    if (family) {
      return {
        id: family.id,
        name: family.name,
        routeIds: family.routeIds,
        rank: family.rank,
      };
    }
  }

  if (subwayRoutes.length > 0) {
    return {
      id: "multiple-lines",
      name: "Multiple lines",
      routeIds,
      rank: 800,
    };
  }

  const primaryRoute = busRoutes[0] ?? routeIds[0];
  return {
    id: `bus-${primaryRoute}`,
    name: `${primaryRoute} bus`,
    routeIds: [primaryRoute],
    rank: 700,
  };
}

function uniqueFamilyIds(routeIds: string[]): string[] {
  return Array.from(
    new Set(
      routeIds
        .map((routeId) => ALERT_ROUTE_TO_FAMILY.get(routeId)?.id)
        .filter((id): id is string => Boolean(id)),
    ),
  );
}

function uniqueRoutes(routeIds: string[]): string[] {
  return Array.from(
    new Set(routeIds.map((routeId) => routeId.trim().toUpperCase())),
  )
    .filter(Boolean)
    .sort(routeSortRank);
}

function mergeRouteIds(left: string[], right: string[]): string[] {
  return uniqueRoutes([...left, ...right]);
}

function routeSortRank(left: string, right: string): number {
  const leftFamily = ALERT_ROUTE_TO_FAMILY.get(left);
  const rightFamily = ALERT_ROUTE_TO_FAMILY.get(right);
  if (leftFamily || rightFamily) {
    const leftRank = leftFamily
      ? leftFamily.rank * 10 + familyRoutePosition(leftFamily, left)
      : 9999;
    const rightRank = rightFamily
      ? rightFamily.rank * 10 + familyRoutePosition(rightFamily, right)
      : 9999;
    return leftRank - rightRank;
  }
  if (SUBWAY_BULLET_ROUTES.has(left) !== SUBWAY_BULLET_ROUTES.has(right)) {
    return SUBWAY_BULLET_ROUTES.has(left) ? -1 : 1;
  }
  return left.localeCompare(right);
}

function familyRoutePosition(
  family: { routeIds: string[] },
  routeId: string,
): number {
  const index = family.routeIds.indexOf(routeId);
  return index >= 0 ? index : family.routeIds.length;
}

function lineAlertSubtitle(item: AlertFeedItem): string | undefined {
  const candidates = [
    item.summary,
    item.details?.impact,
    item.details?.currentStatus,
    item.routeIds.length > 2 ? "Multiple lines affected" : item.serviceName,
  ].filter((value): value is string => {
    if (!value?.trim()) return false;
    return !isGenericAlertCopy(value);
  });
  const visibleTitle = item.title.toLowerCase();
  return candidates.find(
    (value) => !visibleTitle.includes(value.toLowerCase()),
  );
}

/* Condensed detail: the raw MTA body bundles the disruption and the "take
   the X instead" guidance into one blob, so we split it — Impact carries the
   disruption, Travel alternatives the guidance — and drop the rows that
   merely restated other rows (a raw "Alert text" echo, a single synthetic
   "First reported" update). Affected stops stays because the chips scan
   faster than the same names buried in a sentence. */
type AlertDetailView = {
  impact?: string;
  alternatives?: string;
  statusText?: string;
  stops: string[];
};

function AlertDetailPanel({
  item,
  detail,
}: {
  item: AlertFeedItem;
  detail: AlertDetailView;
}) {
  return (
    <div className="sr-alert-detail">
      {detail.impact && (
        <DetailRow
          icon={<TriangleAlert size={14} strokeWidth={1.8} />}
          label="Impact"
        >
          <TransitText text={detail.impact} bulletSize={13} />
        </DetailRow>
      )}
      {!isSystemwide(item) && item.routeIds.length > 0 && (
        <DetailRow
          icon={<TramFront size={14} strokeWidth={1.8} />}
          label="Affected service"
        >
          <span className="sr-alert-detail__routes">
            <RouteBadgeGroup routeIds={item.routeIds} limit={6} size={18} />
            <span>{item.serviceName}</span>
          </span>
        </DetailRow>
      )}
      {detail.alternatives && (
        <DetailRow
          icon={<ArrowLeftRight size={14} strokeWidth={1.8} />}
          label="Travel alternatives"
        >
          <TransitText text={detail.alternatives} bulletSize={13} />
        </DetailRow>
      )}
      {detail.statusText && (
        <DetailRow
          icon={<Activity size={14} strokeWidth={1.8} />}
          label="Current status"
        >
          <TransitText text={detail.statusText} bulletSize={13} />
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
    </div>
  );
}

/* The MTA often runs disruption and guidance together with no sentence break
   ("…and Longwood Av For service to these stations take the 6…"). Cut at the
   first guidance phrase: everything before is the Impact, everything after is
   the Travel alternatives. Falls back to the pre-parsed alternatives when the
   body has no inline guidance. */
const GUIDANCE_BOUNDARY =
  /\b(for service to these stations|for alternative service|take (?:the|a|nearby)\b|use (?:nearby|the)\b|board the\b|or take\b)/i;

function disruptionAndGuidance(
  item: AlertFeedItem,
  raw = detailSummary(item),
): {
  impact?: string;
  alternatives?: string;
} {
  const parsed = item.details?.alternatives?.trim();
  if (!raw) {
    return { impact: undefined, alternatives: parsed };
  }
  const match = raw.match(GUIDANCE_BOUNDARY);
  if (!match || match.index === undefined) {
    // No inline guidance — keep the body as Impact; show pre-parsed
    // alternatives only when they add something the body doesn't.
    const alt =
      parsed && !raw.toLowerCase().includes(parsed.toLowerCase())
        ? parsed
        : undefined;
    return { impact: raw, alternatives: alt };
  }
  const impact = tidySentence(raw.slice(0, match.index));
  const guidance = tidySentence(
    raw.slice(match.index).replace(/^[\s,;:.-]+/, ""),
  );
  return { impact, alternatives: guidance ?? parsed };
}

function tidySentence(value: string): string | undefined {
  const trimmed = value.replace(/[\s,;:-]+$/, "").trim();
  if (!trimmed) return undefined;
  const cased = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(cased) ? cased : `${cased}.`;
}

function DetailRow({
  icon,
  label,
  children,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
}) {
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

function buildAlertDetailView(item: AlertFeedItem): AlertDetailView | null {
  const detail = item.details;
  if (!detail || !item.expandable) return null;
  const summary = detailSummary(item);
  const { impact, alternatives } = disruptionAndGuidance(item, summary);
  const statusText = distinctStatus(item, summary);
  const stops = detail.affectedStops ?? [];
  const hasDetail = Boolean(
    impact ||
      alternatives ||
      statusText ||
      stops.length > 0,
  );
  if (!hasDetail) return null;
  return { impact, alternatives, statusText, stops };
}

function featuredAlertBody(item: AlertFeedItem): string | undefined {
  const visibleTitle = item.title.toLowerCase();
  const candidates = [
    item.details?.impact,
    item.details?.currentStatus,
    item.summary,
  ].filter((value): value is string => {
    if (!value?.trim()) return false;
    return !isGenericAlertCopy(value);
  });
  const fallbackSummary =
    item.summary && !isGenericAlertCopy(item.summary) ? item.summary : undefined;

  const body =
    candidates.find((value) => !visibleTitle.includes(value.toLowerCase())) ??
    fallbackSummary;
  return body ? stripTitleEcho(body, item.title) : undefined;
}

/* MTA bodies often open by repeating the headline verbatim ("Take the
   [A][C][D][Q] instead Take the…") — drop the echoed prefix so the card
   never says the same sentence twice. */
function stripTitleEcho(value: string, title: string): string | undefined {
  const trimmedTitle = title.trim();
  const trimmedValue = value.trim();
  if (
    trimmedTitle &&
    trimmedValue.toLowerCase().startsWith(trimmedTitle.toLowerCase())
  ) {
    const rest = trimmedValue
      .slice(trimmedTitle.length)
      .replace(/^[\s.,:;-]+/, "");
    if (!rest) return undefined;
    return rest.charAt(0).toUpperCase() + rest.slice(1);
  }
  return trimmedValue;
}

function isGenericAlertCopy(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return (
    normalized === "active service notice" ||
    normalized === "service change in effect." ||
    normalized === "service change in effect" ||
    normalized === "trains are running with delays." ||
    normalized === "trains are running with delays"
  );
}

function featuredAlertAdvice(
  item: AlertFeedItem,
  bodyText: string | undefined,
): string | undefined {
  const alternatives = item.details?.alternatives?.trim();
  if (!alternatives) return undefined;
  // Suppress when the headline or body already says it (ignoring the
  // trailing period a verbatim sentence carries) — never the same guidance
  // twice on one card.
  const key = alternatives.toLowerCase().replace(/[.!?]+$/, "");
  const visible = `${item.title} ${bodyText ?? ""}`.toLowerCase();
  if (key && visible.includes(key)) return undefined;
  return alternatives;
}

function detailSummary(item: AlertFeedItem): string | undefined {
  const fuller = item.details?.impact ?? item.details?.currentStatus;
  if (!fuller) return undefined;
  const visible = `${item.title} ${item.summary ?? ""}`.toLowerCase();
  return visible.includes(fuller.toLowerCase()) ? undefined : fuller;
}

function distinctStatus(
  item: AlertFeedItem,
  impact: string | undefined,
): string | undefined {
  const status = item.details?.currentStatus;
  if (!status) return undefined;
  if (impact && impact.toLowerCase().includes(status.toLowerCase())) {
    return undefined;
  }
  const visible = `${item.title} ${item.summary ?? ""}`.toLowerCase();
  return visible.includes(status.toLowerCase()) ? undefined : status;
}

function alertDotTone(item: AlertFeedItem): "red" | "orange" | "amber" | "green" {
  if (item.lifecycle === "resolved") return "green";
  if (item.severity === "major" || item.severity === "suspension") return "red";
  if (item.severity === "planned") return "orange";
  return "amber";
}

const TONE_COLORS: Record<"red" | "orange" | "amber" | "green", string> = {
  red: "#ef4444",
  orange: "#f97316",
  amber: "#f59e0b",
  green: "#22c55e",
};

function stripeColor(item: AlertFeedItem): string {
  const routeColor = item.routeIds
    .map((route) => LINE_COLORS[route])
    .find((color): color is string => Boolean(color));
  if (routeColor) return routeColor;
  return TONE_COLORS[alertDotTone(item)];
}

function StatusMeta({ item }: { item: AlertFeedItem }) {
  if (!item.statusLabel) return null;
  const tone = alertDotTone(item);
  return (
    <span className="sr-alert-status" data-tone={tone}>
      <AlertSeverityDot tone={tone} />
      {item.statusLabel}
    </span>
  );
}

function AlertSeverityDot({
  tone,
}: {
  tone: "red" | "orange" | "amber" | "green";
}) {
  return (
    <span
      className="sr-alert-severity-dot"
      data-tone={tone}
      aria-hidden="true"
    />
  );
}

function RouteBadgeGroup({
  routeIds,
  limit = 2,
  size,
}: {
  routeIds: string[];
  limit?: number;
  size?: number;
}) {
  const subwayRoutes = routeIds.filter((routeId) =>
    SUBWAY_BULLET_ROUTES.has(routeId),
  );
  if (subwayRoutes.length === routeIds.length) {
    return <RouteBulletGroup lines={routeIds} size={size ?? 26} limit={limit} />;
  }

  return (
    <span className="sr-alert-route-badges">
      {routeIds.slice(0, limit).map((routeId) => (
        <RouteBadge key={routeId} routeId={routeId} size={size ?? 26} />
      ))}
    </span>
  );
}

function RouteBadge({ routeId, size }: { routeId: string; size: number }) {
  if (SUBWAY_BULLET_ROUTES.has(routeId)) {
    return <RouteBullet line={routeId} size={size} />;
  }
  return <BusChip route={routeId} />;
}

function SectionHeader({ title, meta }: { title: string; meta?: ReactNode }) {
  return (
    <div className="sr-section-header">
      <h2>{title}</h2>
      {meta && <span>{meta}</span>}
    </div>
  );
}

function updatedLabelFor(item: AlertFeedItem): string | undefined {
  const label = shortTimeLabel(item.timestampLabel);
  return label ? `Updated ${label}` : undefined;
}

/* Only render a timestamp when the data carries a real elapsed age. "now"/
   "live"/"just now" is the adapter's default when there's no genuine update
   time — surfacing it would show a perpetual "just now" to every viewer, so
   those yield nothing and the row simply omits the time. */
function shortTimeLabel(label: string): string | undefined {
  if (!label) return undefined;
  const normalized = label.toLowerCase();
  if (normalized === "now" || normalized === "live" || normalized === "just now") {
    return undefined;
  }
  const minutes = label.match(/^(\d+)m$/);
  if (minutes) return `${minutes[1]} min ago`;
  const hours = label.match(/^(\d+)h$/);
  if (hours) return `${hours[1]} hr ago`;
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function severityLabel(severity: AlertFeedSeverity): string {
  if (severity === "planned") return "Planned work";
  if (severity === "minor") return "Minor delay";
  if (severity === "major") return "Major disruption";
  if (severity === "suspension") return "Suspension";
  if (severity === "incident") return "Nearby incident";
  return "Service change";
}
