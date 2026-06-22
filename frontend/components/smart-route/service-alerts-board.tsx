"use client";

import { useMemo, useState, type CSSProperties } from "react";
import {
  Bell,
  BusFront,
  ChevronRight,
  Clock3,
  Radio,
  Search,
  Sparkles,
  TrainFront,
  X,
} from "lucide-react";
import type { ServiceAlertDetail } from "@/types";
import {
  buildDashboardMetrics,
  buildDashboardRows,
  buildLineStates,
  feedFreshness,
  filterDashboardRows,
  groupDashboardRows,
  sortDashboardRows,
  type AlertFilterMode,
  type DashboardAlertGroup,
  type DashboardAlertRow,
  type LineAlertState,
} from "@/components/smart-route/service-alert-dashboard-model";
import { TrainBullet } from "@/components/smart-route/train-bullet";

type ServiceAlertConnectionState = "connecting" | "open" | "closed";
type AlertTone = "major" | "minor" | "watch";

interface ServiceAlertsBoardProps {
  alerts: ServiceAlertDetail[];
  updatedAt: number | null;
  activeCount: number;
  affectedRouteCount: number;
  isLoading: boolean;
  error: string | null;
  connectionState?: ServiceAlertConnectionState;
  changedAlertIds?: Set<string>;
}

const FILTERS: Array<{
  id: AlertFilterMode;
  label: string;
  icon: typeof Bell;
}> = [
  { id: "all", label: "All", icon: Bell },
  { id: "train", label: "Train", icon: TrainFront },
  { id: "bus", label: "Bus", icon: BusFront },
  { id: "planned", label: "Planned", icon: Clock3 },
  { id: "active", label: "Active", icon: Radio },
];

function toneForRow(alert: DashboardAlertRow): AlertTone {
  if (alert.tone === "major") return "major";
  if (alert.tone === "watch") return "watch";
  return "minor";
}

function toneForGroup(group: DashboardAlertGroup): AlertTone {
  if (group.tone === "major") return "major";
  if (group.tone === "watch") return "watch";
  return "minor";
}

function lineStateLabel(state: LineAlertState) {
  if (state === "major") return "Major";
  if (state === "some") return "Active";
  return "Clear";
}

function primaryLine(alert: DashboardAlertRow) {
  return alert.primaryRoute ?? alert.routes[0] ?? null;
}

function LineBadge({
  line,
  size = 34,
  className,
}: {
  line: string | null;
  size?: number;
  className?: string;
}) {
  if (!line) {
    return (
      <span
        className={className ? `sr-alert-board__line ${className}` : "sr-alert-board__line"}
        style={{
          width: size,
          height: size,
          borderRadius: 999,
          border: "1px solid rgba(255,255,255,0.16)",
          background: "rgba(255,255,255,0.045)",
          color: "rgba(244,247,251,0.72)",
          fontFamily: "var(--font-jetbrains-mono), monospace",
          fontSize: Math.max(9, size * 0.28),
          fontWeight: 800,
        }}
      >
        SYS
      </span>
    );
  }

  return (
    <TrainBullet
      line={line}
      size={size}
      className={className ? `sr-alert-board__line ${className}` : "sr-alert-board__line"}
    />
  );
}

function StatusPill({ tone, children }: { tone: AlertTone; children: string }) {
  return (
    <span className="sr-alert-board__status-pill" data-tone={tone}>
      {children}
    </span>
  );
}

function FilterPill({
  filter,
  active,
  count,
  onClick,
}: {
  filter: (typeof FILTERS)[number];
  active: boolean;
  count: number;
  onClick: (filter: AlertFilterMode) => void;
}) {
  const Icon = filter.icon;
  return (
    <button
      type="button"
      className="sr-alert-board__filter"
      data-active={active}
      aria-pressed={active}
      onClick={() => onClick(filter.id)}
    >
      <Icon size={16} strokeWidth={1.5} aria-hidden="true" />
      <span>{filter.label}</span>
      <strong>{count}</strong>
    </button>
  );
}

function GroupHeader({ group }: { group: DashboardAlertGroup }) {
  return (
    <div className="sr-alert-board__group-label" data-tone={toneForGroup(group)} data-visible="true">
      <span>{group.label}</span>
      <strong>{group.items.length}</strong>
    </div>
  );
}

function AlertRow({
  alert,
  expanded,
  index,
  changed,
  onToggle,
}: {
  alert: DashboardAlertRow;
  expanded: boolean;
  index: number;
  changed: boolean;
  onToggle: (id: string) => void;
}) {
  const detailId = `alert-detail-${alert.id}`;
  const tone = toneForRow(alert);

  return (
    <article
      className="sr-alert-board__row-wrap"
      data-expanded={expanded}
      data-tone={tone}
      data-changed={changed}
      style={{ "--row-index": index } as CSSProperties}
    >
      <button
        type="button"
        className="sr-alert-board__row"
        aria-expanded={expanded}
        aria-controls={detailId}
        onClick={() => onToggle(alert.id)}
      >
        <span className="sr-alert-board__alert-cell">
          <LineBadge line={primaryLine(alert)} size={34} />
          <span className="sr-alert-board__alert-copy">
            <strong>{alert.displayTitle}</strong>
            <small>{alert.displaySubtitle}</small>
          </span>
        </span>

        <span className="sr-alert-board__status-cell">
          <StatusPill tone={tone}>{alert.statusLabel}</StatusPill>
        </span>

        <span className="sr-alert-board__time-cell">{alert.startedLabel}</span>

        <span className="sr-alert-board__chevron" aria-hidden="true">
          <ChevronRight size={18} strokeWidth={1.5} />
        </span>
      </button>

      <InlineDetail alert={alert} open={expanded} detailId={detailId} />
    </article>
  );
}

function InlineDetail({
  alert,
  open,
  detailId,
}: {
  alert: DashboardAlertRow;
  open: boolean;
  detailId: string;
}) {
  return (
    <div
      id={detailId}
      className="sr-alert-board__drawer-shell"
      data-open={open}
      role="region"
      aria-label={`${alert.displayTitle} details`}
    >
      <div className="sr-alert-board__drawer-clip">
        <div className="sr-alert-board__drawer">
          <AlertDetail alert={alert} />
        </div>
      </div>
    </div>
  );
}

function InlineLine({ line }: { line: string }) {
  return <LineBadge line={line} size={17} className="sr-alert-board__inline-line" />;
}

function AlertDetail({ alert }: { alert: DashboardAlertRow }) {
  const routeList = alert.routes.slice(0, 6);
  const stops = alert.affectedStops.slice(0, 8);
  const activityItems = alert.activityItems.slice(0, 4);

  return (
    <div className="sr-alert-board__detail-grid">
      <section className="sr-alert-board__ai-context">
        <div className="sr-alert-board__detail-label">
          <Sparkles size={15} strokeWidth={1.5} aria-hidden="true" />
          <span>Alert Context</span>
        </div>
        <p>
          {alert.detail}
          {routeList.length > 0 ? (
            <>
              {" "}
              Affected routes:{" "}
              {routeList.map((routeId) => (
                <InlineLine key={routeId} line={routeId} />
              ))}
            </>
          ) : null}
        </p>
        <span className="sr-alert-board__confidence">
          <i aria-hidden="true" />
          Live MTA notice
        </span>
      </section>

      <section className="sr-alert-board__detail-middle">
        <dl className="sr-alert-board__facts">
          <div>
            <dt>Started</dt>
            <dd>{alert.startedLabel}</dd>
          </div>
          <div>
            <dt>Last Update</dt>
            <dd>{alert.lastUpdatedLabel}</dd>
          </div>
          <div>
            <dt>Scope</dt>
            <dd>{alert.affecting}</dd>
          </div>
          <div>
            <dt>Est. Clear</dt>
            <dd>{alert.estimatedClearLabel}</dd>
          </div>
        </dl>

        <div className="sr-alert-board__stops">
          <span className="sr-alert-board__detail-label">Affected Stops</span>
          <div>
            {stops.length > 0 ? (
              stops.map((stop) => <span key={stop}>{stop}</span>)
            ) : (
              <span>Route-wide notice</span>
            )}
          </div>
        </div>
      </section>

      <section className="sr-alert-board__activity">
        <span className="sr-alert-board__detail-label">Activity</span>
        <ol>
          {activityItems.map((item, index) => (
            <li key={`${item.id}-${item.time}`} data-current={index === 0}>
              <span aria-hidden="true" />
              <div>
                <strong>{item.time}</strong>
                <small>{item.label}</small>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function EmptyRows({
  filter,
  isLoading,
  error,
}: {
  filter: AlertFilterMode;
  isLoading: boolean;
  error: string | null;
}) {
  const copy = error
    ? error
    : isLoading
      ? "Pulling the latest MTA service alerts."
      : filter === "bus"
        ? "Bus alerts are not included in this board yet."
        : filter === "planned"
          ? "No planned subway service changes are published for today."
          : filter === "active"
            ? "No active subway alerts are published right now."
            : "No alerts match the current search.";

  return (
    <div className="sr-alert-board__empty" role="status">
      <strong>{error ? "Service alerts unavailable" : isLoading ? "Loading alerts" : "No matching alerts"}</strong>
      <span>{copy}</span>
    </div>
  );
}

export function ServiceAlertsBoard({
  alerts,
  updatedAt,
  activeCount,
  affectedRouteCount,
  isLoading,
  error,
  connectionState = "closed",
  changedAlertIds = new Set<string>(),
}: ServiceAlertsBoardProps) {
  const [activeFilter, setActiveFilter] = useState<AlertFilterMode>("all");
  const [query, setQuery] = useState("");
  const [expandedAlertId, setExpandedAlertId] = useState<string | null>(null);

  const rows = useMemo(() => buildDashboardRows(alerts, updatedAt), [alerts, updatedAt]);
  const metrics = useMemo(
    () => buildDashboardMetrics(rows, activeCount, affectedRouteCount),
    [activeCount, affectedRouteCount, rows],
  );
  const lineStates = useMemo(() => buildLineStates(rows), [rows]);
  const freshness = useMemo(() => feedFreshness(updatedAt), [updatedAt]);

  const filterCounts = useMemo<Record<AlertFilterMode, number>>(
    () => ({
      all: rows.length,
      train: rows.length,
      bus: 0,
      planned: metrics.plannedCount,
      active: rows.filter((row) => row.tone !== "detour").length,
    }),
    [metrics.plannedCount, rows],
  );

  const visibleAlerts = useMemo(
    () => sortDashboardRows(filterDashboardRows(rows, activeFilter, query, null), "severity"),
    [activeFilter, query, rows],
  );

  const groupedAlerts = useMemo(() => groupDashboardRows(visibleAlerts), [visibleAlerts]);

  function toggleAlert(id: string) {
    setExpandedAlertId((current) => (current === id ? null : id));
  }

  const networkLabel = lineStateLabel(metrics.networkState);
  const feedState = connectionState === "open" ? "Live" : connectionState === "connecting" ? "Connecting" : "Polling";
  const topLineStates = lineStates.filter((line) => line.state !== "none").slice(0, 4);

  return (
    <section
      className="sr-alert-board"
      aria-labelledby="service-alert-board-title"
      data-feed-state={connectionState}
      data-network-state={metrics.networkState}
    >
      <div className="sr-alert-board__inner">
        <header className="sr-alert-board__header">
          <h1 id="service-alert-board-title">
            MTA Alert Board
            <span className="sr-only">
              {` ${feedState}. ${networkLabel}. Updated ${freshness.label}. ${metrics.activeCount} active alerts across ${metrics.affectedRouteCount} routes.`}
            </span>
          </h1>
        </header>

        <div className="sr-alert-board__toolbar" aria-label="Service alert controls">
          <div className="sr-alert-board__filters" aria-label="Filter service alerts">
            {FILTERS.map((filter) => (
              <FilterPill
                key={filter.id}
                filter={filter}
                count={filterCounts[filter.id]}
                active={activeFilter === filter.id}
                onClick={setActiveFilter}
              />
            ))}
          </div>

          <label className="sr-alert-board__search">
            <Search size={17} strokeWidth={1.5} aria-hidden="true" />
            <span className="sr-only">Search service alerts</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={
                topLineStates.length > 0
                  ? `Search ${topLineStates.map((line) => line.routeId).join(", ")} or station`
                  : "Search by line, station, or keyword"
              }
            />
            {query ? (
              <button
                type="button"
                aria-label="Clear service alert search"
                onClick={() => setQuery("")}
              >
                <X size={14} strokeWidth={1.5} aria-hidden="true" />
              </button>
            ) : null}
          </label>
        </div>

        <div className="sr-alert-board__table" role="table" aria-label="MTA service alerts">
          <div className="sr-alert-board__columns" role="row" aria-hidden="true">
            <span>Alert</span>
            <span>Status</span>
            <span>Started / Updated</span>
            <span />
          </div>

          {visibleAlerts.length === 0 ? (
            <EmptyRows filter={activeFilter} isLoading={isLoading} error={error} />
          ) : (
            groupedAlerts.map((group) => (
              <section key={group.id} className="sr-alert-board__group">
                <GroupHeader group={group} />
                {group.items.map((alert, index) => (
                  <AlertRow
                    key={alert.id}
                    alert={alert}
                    expanded={expandedAlertId === alert.id}
                    changed={changedAlertIds.has(alert.id)}
                    index={index}
                    onToggle={toggleAlert}
                  />
                ))}
              </section>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
