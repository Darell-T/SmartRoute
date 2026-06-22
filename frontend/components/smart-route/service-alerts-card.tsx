"use client";

import {
  AlertTriangle,
  ArrowUpRight,
  ChevronRight,
  Info,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import type { ServiceAlertDetail } from "@/types";
import { TrainBullet } from "./train-bullet";

interface Props {
  alerts: ServiceAlertDetail[];
  maxItems?: number;
  compact?: boolean;
  constrained?: boolean;
  title?: string;
  subtitle?: string;
  variant?: "card" | "rail";
  mode?: "list" | "categorical";
}

export type Category = "delays" | "planned" | "change";

const CATEGORY_META: Record<
  Category,
  { label: string; tone: string; Icon: typeof AlertTriangle }
> = {
  delays: { label: "Major Delays", tone: "#ff7f7f", Icon: XCircle },
  planned: { label: "Planned Work", tone: "#f3b247", Icon: AlertTriangle },
  change: { label: "Service Change", tone: "#78b6ff", Icon: Info },
};

export function categoryForAlert(alert: ServiceAlertDetail): Category {
  const text = `${alert.header || ""} ${alert.description || ""}`.toUpperCase();
  if (/SUSPEND|NO SERVICE|PART SUSPEND|MAJOR DELAY|SKIP|BYPASS/.test(text)) {
    return "delays";
  }
  if (/PLANNED|WORK|CONSTRUCTION|MAINTENANCE/.test(text)) {
    return "planned";
  }
  if (/DELAY/.test(text)) {
    return "delays";
  }
  return "change";
}

function classifyAlert(alert: ServiceAlertDetail) {
  const text = `${alert.header || ""} ${alert.description || ""}`.toUpperCase();
  if (/SUSPEND|NO SERVICE|PART SUSPEND|MAJOR DELAY|SKIP|BYPASS/.test(text)) {
    return {
      label: "Disruption",
      tone: "#ff7f7f",
      Icon: XCircle,
    };
  }
  if (/PLANNED|WORK|CONSTRUCTION|MAINTENANCE/.test(text)) {
    return {
      label: "Planned Work",
      tone: "#f3b247",
      Icon: AlertTriangle,
    };
  }
  if (/DELAY|SERVICE CHANGE|REROUTE|SHUTTLE/.test(text)) {
    return {
      label: "Delays",
      tone: "#f3b247",
      Icon: TriangleAlert,
    };
  }
  return {
    label: "Service Change",
    tone: "#78b6ff",
    Icon: Info,
  };
}

export function relativeAlertTime(alert: ServiceAlertDetail) {
  const base = alert.start ?? null;
  if (!base) return "Active now";
  const delta = Math.max(0, Math.floor(Date.now() / 1000) - base);
  if (delta < 60) return "Started now";
  const minutes = Math.floor(delta / 60);
  if (minutes < 60) return `Started ${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `Started ${hours}h ago`;
}

export function normalizeRoutes(alert: ServiceAlertDetail) {
  const routes = (alert.routeIds || alert.route_ids || [])
    .map((routeId) =>
      String(routeId || "")
        .trim()
        .toUpperCase(),
    )
    .filter(Boolean);
  return Array.from(new Set(routes)).slice(0, 4);
}

export function alertDetail(alert: ServiceAlertDetail) {
  const detail = (alert.description || "").trim();
  if (detail) return detail;
  return "Operating conditions are shifting on this nearby line set.";
}

export function alertTitle(alert: ServiceAlertDetail) {
  const title = (alert.header || "").trim();
  return title || "Service condition update";
}

export function ServiceAlertsCard({
  alerts,
  maxItems = 4,
  compact = false,
  constrained = false,
  title = "SERVICE ALERTS",
  subtitle = "AFFECTING NEARBY LINES",
  variant = "card",
  mode = "list",
}: Props) {
  if (variant === "rail" && mode === "categorical") {
    const counts: Record<Category, number> = {
      delays: 0,
      planned: 0,
      change: 0,
    };
    for (const alert of alerts) {
      counts[categoryForAlert(alert)] += 1;
    }
    const categoryOrder: Category[] = ["delays", "planned", "change"];

    return (
      <section className="sr-intel-section sr-intel-section--alerts">
        <div className="sr-intel-section__header">
          <div className="sr-section-label !mb-0">{title}</div>
          <div className="sr-intel-section__meta">
            Live <span aria-hidden="true">{"\u00b7"}</span> {alerts.length}
          </div>
        </div>

        <div className="sr-service-alerts-card__categories">
          {categoryOrder.map((cat) => {
            const meta = CATEGORY_META[cat];
            const count = counts[cat];
            const Icon = meta.Icon;

            return (
              <div
                key={cat}
                className="sr-service-alert-category-row"
                data-empty={count === 0 ? "true" : "false"}
              >
                <span
                  className="sr-service-alert-category-row__icon"
                  style={{
                    borderColor: `${meta.tone}44`,
                    background: `${meta.tone}14`,
                    color: meta.tone,
                  }}
                >
                  <Icon size={12} strokeWidth={2} />
                </span>
                <span className="sr-service-alert-category-row__label">
                  {meta.label}
                </span>
                <span
                  className="sr-service-alert-category-row__count"
                  style={{ color: count > 0 ? meta.tone : undefined }}
                >
                  {count}
                </span>
                <ChevronRight
                  size={12}
                  strokeWidth={1.8}
                  className="sr-service-alert-category-row__chev"
                  aria-hidden="true"
                />
              </div>
            );
          })}
        </div>
      </section>
    );
  }

  const items = alerts.slice(0, maxItems);

  return (
    <div
      className="sr-card sr-service-alerts-card"
      data-constrained={constrained ? "true" : "false"}
      style={{
        padding: compact ? 12 : 14,
        background:
          "linear-gradient(180deg, rgba(11,16,24,0.98), rgba(7,11,18,0.98))",
      }}
    >
      <div
        className="flex items-center justify-between gap-3"
        style={{ marginBottom: 12 }}
      >
        <div className="sr-section-label !mb-0">{title}</div>
        <div
          style={{
            fontFamily: "var(--font-jetbrains-mono), monospace",
            fontSize: 9.5,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "rgba(148,163,184,0.78)",
          }}
        >
          {subtitle}
        </div>
      </div>

      <div className="sr-service-alerts-card__list flex flex-col gap-2.5">
        {items.length === 0 ? (
          <div
            style={{
              fontFamily: "var(--font-geist), sans-serif",
              fontSize: 11.5,
              lineHeight: 1.55,
              color: "rgba(148,163,184,0.82)",
            }}
          >
            Nearby lines are not carrying active service notices right now.
          </div>
        ) : (
          items.map((alert, index) => {
            const severity = classifyAlert(alert);
            const Icon = severity.Icon;
            const routes = normalizeRoutes(alert);

            return (
              <div
                key={alert.alert_id || `${alert.header}-${index}`}
                className="sr-service-alert-row"
              >
                <div className="sr-service-alert-row__lead">
                  {routes.length > 0 ? (
                    routes.map((routeId) => (
                      <TrainBullet key={routeId} line={routeId} size={22} />
                    ))
                  ) : (
                    <div
                      className="grid h-[22px] w-[22px] place-items-center rounded-full"
                      style={{
                        border: "1px solid rgba(255,255,255,0.12)",
                        background: "rgba(255,255,255,0.04)",
                        fontFamily: "var(--font-jetbrains-mono), monospace",
                        fontSize: 9,
                        color: "rgba(255,255,255,0.62)",
                      }}
                    >
                      SYS
                    </div>
                  )}
                </div>

                <div className="min-w-0">
                  <div className="sr-service-alert-row__headline">
                    <span
                      className="inline-flex h-6 w-6 items-center justify-center rounded-full"
                      style={{
                        border: `1px solid ${severity.tone}44`,
                        background: `${severity.tone}12`,
                        color: severity.tone,
                      }}
                    >
                      <Icon size={13} strokeWidth={1.9} />
                    </span>
                    <span>{alertTitle(alert)}</span>
                  </div>

                  <div className="sr-service-alert-row__title">
                    {routes.length > 0
                      ? `${routes.join(" / ")} · ${severity.label}`
                      : severity.label}
                  </div>
                  <div className="sr-service-alert-row__detail">
                    {alertDetail(alert)}
                  </div>
                </div>

                <div className="sr-service-alert-row__meta">
                  {relativeAlertTime(alert)}
                </div>
              </div>
            );
          })
        )}
      </div>

      <button
        type="button"
        className="flex items-center gap-1.5"
        style={{
          marginTop: 12,
          padding: 0,
          background: "transparent",
          border: "none",
          cursor: "pointer",
          fontFamily: "var(--font-geist), sans-serif",
          fontSize: 11,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "rgba(148,163,184,0.78)",
        }}
      >
        View all alerts
        <ArrowUpRight size={13} strokeWidth={1.8} />
      </button>
    </div>
  );
}
