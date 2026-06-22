"use client";

import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { BrandLogo } from "@/components/brand-logo";

export type TabId = "livemap" | "atlas" | "alerts";

const TABS: { id: TabId; label: string }[] = [
  { id: "livemap", label: "Live feed" },
  { id: "atlas", label: "ATLAS Intel" },
  { id: "alerts", label: "Service alerts" },
];

export interface ShellMetric {
  label: string;
  value: string;
  tone?: string;
  dot?: string;
}

interface SmartRouteShellProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  accent: string;
  weatherSlot?: ReactNode;
  serviceAlertsConnectionState?: "connecting" | "open" | "closed";
  workspace: ReactNode;
  rail?: ReactNode;
  footerLeft: ShellMetric[];
  footerRight: ShellMetric[];
  bottomTray?: ReactNode;
  hideFooter?: boolean;
}

function useLiveClock() {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const interval = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(interval);
  }, []);

  return now
    ? now.toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      })
    : "--:-- --";
}

export function SmartRouteShell({
  activeTab,
  onTabChange,
  accent,
  weatherSlot,
  serviceAlertsConnectionState = "open",
  workspace,
  rail,
  footerLeft,
  footerRight,
  bottomTray,
  hideFooter = false,
}: SmartRouteShellProps) {
  const clockStr = useLiveClock();

  return (
    <div
      className="sr-app-shell"
      data-active-tab={activeTab}
    >
      <header className="sr-shell__header">
        <div className="sr-shell__brand">
          <BrandLogo size="header" connectionState={serviceAlertsConnectionState} />
        </div>

        <nav className="sr-shell__tabs" aria-label="Primary">
          {TABS.map((tab) => {
            const active = tab.id === activeTab;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => onTabChange(tab.id)}
                className="sr-shell__tab"
                data-active={active}
                style={
                  active
                    ? ({
                        "--sr-tab-accent": accent,
                      } as CSSProperties)
                    : undefined
                }
              >
                {tab.label}
              </button>
            );
          })}
        </nav>

        <div className="sr-shell__status">
          <span
            className="sr-shell__live"
            aria-label="Live feed status"
            data-state={
              serviceAlertsConnectionState === "open"
                ? "live"
                : serviceAlertsConnectionState === "connecting"
                  ? "connecting"
                  : "offline"
            }
          >
            <span aria-hidden="true" />
            LIVE
          </span>
          <div className="sr-shell__status-clock">
            <span>{clockStr}</span>
            <span aria-hidden="true">/</span>
            <span>NYC</span>
            {weatherSlot ? (
              <>
                <span aria-hidden="true">·</span>
                {weatherSlot}
              </>
            ) : null}
          </div>
        </div>
      </header>

      <div className="sr-shell__main" data-has-rail={rail ? "true" : "false"}>
        <main className="sr-shell__workspace">{workspace}</main>
        {rail ? (
          <aside className="sr-shell__rail" aria-label="Operational rail">
            {rail}
          </aside>
        ) : null}
      </div>

      {bottomTray ? (
        <div className="sr-shell__bottom-tray">{bottomTray}</div>
      ) : hideFooter ? null : (
        <footer className="sr-shell__footer">
          <div className="sr-shell__footer-group">
            {footerLeft.map((item) => (
              <FooterMetric key={`${item.label}-${item.value}`} {...item} />
            ))}
          </div>
          <div className="sr-shell__footer-group sr-shell__footer-group--right">
            {footerRight.map((item) => (
              <FooterMetric key={`${item.label}-${item.value}`} {...item} />
            ))}
          </div>
        </footer>
      )}
    </div>
  );
}

function FooterMetric({ label, value, tone, dot }: ShellMetric) {
  return (
    <div className="sr-shell__metric">
      <span className="sr-shell__metric-label">{label}</span>
      {dot ? (
        <span className="sr-shell__metric-value" style={{ color: tone || "#dfe7f1" }}>
          <span
            className="sr-shell__metric-dot"
            style={{ background: dot, boxShadow: `0 0 8px ${dot}55` }}
          />
          {value}
        </span>
      ) : (
        <span className="sr-shell__metric-value" style={{ color: tone || "#dfe7f1" }}>
          {value}
        </span>
      )}
    </div>
  );
}
