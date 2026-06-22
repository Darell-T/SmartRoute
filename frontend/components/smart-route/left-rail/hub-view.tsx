"use client";

import { useMemo, useState } from "react";
import { Dot, LineBullet, Meta } from "./atoms";
import { X } from "lucide-react";
import { ALL_LINES, sevColor, type FeedEvent } from "./types";
export function HubView({
  feed,
  lineState,
  atlasScanOn,
  onAtlasScanToggle,
}: {
  feed: FeedEvent[];
  lineState: Record<string, "major" | "minor" | "planned">;
  atlasScanOn: boolean;
  onAtlasScanToggle?: () => void;
}) {
  const [filter, setFilter] = useState<string | null>(null);
  const [lastSweep, setLastSweep] = useState(4);

  return (
    <>
      <AtlasScanControl
        on={atlasScanOn}
        onToggle={onAtlasScanToggle}
        lastSweep={lastSweep}
        onRefresh={() => setLastSweep(0)}
      />
      <NetworkPulse
        filter={filter}
        onFilterChange={setFilter}
        lineState={lineState}
      />
      <UnifiedFeed feed={feed} filter={filter} atlasScanOn={atlasScanOn} />
    </>
  );
}

function AtlasScanControl({
  on,
  onToggle,
  lastSweep,
  onRefresh,
}: {
  on: boolean;
  onToggle?: () => void;
  lastSweep: number;
  onRefresh: () => void;
}) {
  return (
    <section style={{ padding: "18px 24px 0" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "11px 12px",
          border: "1px solid var(--sr-rule)",
          background: "var(--sr-surface-2)",
        }}
      >
        <Meta tone={on ? "ink" : "muted"} style={{ fontWeight: 500 }}>
          ATLAS scan
        </Meta>
        <button
          type="button"
          className="sr-switch"
          onClick={onToggle}
          aria-pressed={on}
          aria-label="Toggle ATLAS scan"
        />
        <span style={{ flex: 1 }} />
        <Meta>
          {on && (
            <Dot
              color="var(--sr-cyan)"
              size={5}
              pulse
              style={{ marginRight: 6, verticalAlign: "middle" }}
            />
          )}
          {on ? `Swept ${lastSweep}m ago` : "Paused"}
        </Meta>
        {on && (
          <button type="button" className="sr-ghost-btn" onClick={onRefresh}>
            Refresh
          </button>
        )}
      </div>
      <p
        style={{
          margin: "8px 0 0",
          fontFamily: "var(--sr-display)",
          fontSize: 11.5,
          lineHeight: 1.5,
          color: "var(--sr-muted)",
        }}
      >
        {on
          ? "Sweeps verified incident feeds every 10 minutes."
          : "Incident scan paused. Enable ATLAS to show live incident markers."}
      </p>
    </section>
  );
}

function NetworkPulse({
  filter,
  onFilterChange,
  lineState,
}: {
  filter: string | null;
  onFilterChange: (next: string | null) => void;
  lineState: Record<string, "major" | "minor" | "planned">;
}) {
  const issuesCount = Object.keys(lineState).length;

  return (
    <section style={{ padding: "22px 24px 18px" }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <Meta tone="ink">Network pulse</Meta>
        {issuesCount > 0 ? (
          <Meta tone="amber">
            <Dot
              color="var(--sr-amber)"
              size={5}
              pulse
              style={{ marginRight: 6, verticalAlign: "middle" }}
            />
            {issuesCount} with issues
          </Meta>
        ) : (
          <Meta>All clear</Meta>
        )}
      </div>
      <div
        className="sr-pulse-grid"
        data-filtered={filter ? "true" : "false"}
        style={{ display: "flex", gap: 5, flexWrap: "wrap" }}
      >
        {ALL_LINES.map((l) => {
          const state = lineState[l];
          const active = filter === l;
          return (
            <button
              key={l}
              type="button"
              className="sr-line-chip"
              onClick={() => onFilterChange(active ? null : l)}
              aria-pressed={active}
              data-status={state}
              title={state ? `${l} — ${state}` : `${l} — good service`}
            >
              <LineBullet line={l} size={22} />
            </button>
          );
        })}
      </div>
      {(issuesCount > 0 || filter) && (
        <div
          style={{
            marginTop: 14,
            display: "flex",
            gap: 14,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          {issuesCount > 0 && (
            <>
              <LegendDot color="var(--sr-coral)" label="Major" />
              <LegendDot color="var(--sr-amber)" label="Minor" />
              <LegendDot color="var(--sr-cyan)" label="Planned" />
            </>
          )}
          {filter && (
            <button
              type="button"
              className="sr-ghost-btn"
              style={{ marginLeft: "auto" }}
              onClick={() => onFilterChange(null)}
            >
              Clear filter
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <Dot color={color} size={5} />
      <Meta>{label}</Meta>
    </span>
  );
}

function UnifiedFeed({
  feed,
  filter,
  atlasScanOn,
}: {
  feed: FeedEvent[];
  filter: string | null;
  atlasScanOn: boolean;
}) {
  const items = useMemo(
    () =>
      (filter ? feed.filter((f) => f.line === filter) : feed).filter(
        (f) => atlasScanOn || f.src !== "ATLAS",
      ),
    [feed, filter, atlasScanOn],
  );

  return (
    <section
      style={{
        borderTop: "1px solid var(--sr-rule)",
        paddingBottom: 90,
      }}
    >
      <div
        style={{
          padding: "22px 24px 12px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <Meta tone="ink">Live feed</Meta>
        <Meta>
          <Dot
            color="var(--sr-cyan)"
            size={5}
            pulse
            style={{ marginRight: 6, verticalAlign: "middle" }}
          />
          {items.length} · scanning
        </Meta>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {items.length === 0 && (
          <li
            style={{
              padding: "36px 24px 40px",
              borderTop: "1px solid var(--sr-rule)",
              textAlign: "center",
            }}
          >
            {filter && (
              <div style={{ marginBottom: 12, opacity: 0.9 }}>
                <LineBullet line={filter} size={26} />
              </div>
            )}
            <div
              style={{
                fontFamily: "var(--sr-display)",
                fontSize: 13.5,
                fontWeight: 500,
                color: "var(--sr-fg-2)",
              }}
            >
              {filter
                ? `Nothing reported on the ${filter}`
                : "Quiet across the network"}
            </div>
            <div
              style={{
                marginTop: 5,
                fontFamily: "var(--sr-display)",
                fontSize: 11.5,
                lineHeight: 1.5,
                color: "var(--sr-muted)",
              }}
            >
              MTA and ATLAS signals land here as they come in.
            </div>
          </li>
        )}
        {items.map((x, i) => (
          <FeedRow key={`${x.src}-${i}`} event={x} />
        ))}
      </ul>
    </section>
  );
}

function FeedRow({ event }: { event: FeedEvent }) {
  const sc = sevColor(event.sev);
  return (
    <li
      style={{
        padding: "14px 24px",
        borderTop: "1px solid var(--sr-rule)",
        display: "grid",
        gridTemplateColumns: "28px 1fr auto",
        gap: 12,
        alignItems: "start",
      }}
    >
      <div style={{ paddingTop: 2 }}>
        {event.line ? (
          <LineBullet line={event.line} size={22} />
        ) : (
          <div
            style={{
              width: 22,
              height: 22,
              border: `1px solid ${sc}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: "var(--sr-mono)",
              fontSize: 10,
              color: sc,
            }}
          >
            ⚠
          </div>
        )}
      </div>
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              fontFamily: "var(--sr-mono)",
              fontSize: 9.5,
              letterSpacing: "0.16em",
              color: sc,
              fontWeight: 600,
              textTransform: "uppercase",
            }}
          >
            {event.src} · {event.sev}
          </span>
        </div>
        <div
          style={{
            marginTop: 5,
            fontFamily: "var(--sr-display)",
            fontSize: 14,
            fontWeight: 500,
            color: "var(--sr-fg)",
            lineHeight: 1.25,
            letterSpacing: "-0.005em",
          }}
        >
          {event.title}
        </div>
        <div
          style={{
            marginTop: 4,
            fontFamily: "var(--sr-display)",
            fontSize: 12.5,
            color: "var(--sr-fg-3)",
            lineHeight: 1.5,
          }}
        >
          {event.detail}
        </div>
      </div>
      <Meta>{event.time}</Meta>
    </li>
  );
}

