"use client";

import { useState, type CSSProperties, type ReactNode } from "react";
import { BlurFade, MagicCard } from "./fx";
import { Dot, LineBullet, Meta } from "./atoms";
import type { ServiceAlert, IssueItem } from "./types";

type AlertScope = "all" | "train" | "bus" | "planned" | "active";

export function AlertsView({ alerts }: { alerts: ServiceAlert[] }) {
  const [scope, setScope] = useState<AlertScope>("all");
  const [q, setQ] = useState("");

  const kindCount = (k: AlertScope) =>
    k === "all"
      ? alerts.length
      : k === "active"
      ? alerts.filter((a) => a.sev !== "planned").length
      : k === "planned"
      ? alerts.filter((a) => a.sev === "planned").length
      : alerts.filter((a) => a.kind === k).length;

  const filtered = alerts
    .filter((a) => {
      if (scope === "train") return a.kind === "train";
      if (scope === "bus") return a.kind === "bus";
      if (scope === "planned") return a.sev === "planned";
      if (scope === "active") return a.sev !== "planned";
      return true;
    })
    .filter((a) => {
      if (!q.trim()) return true;
      const hay = (
        a.title +
        " " +
        a.sub +
        " " +
        a.lines.join(" ") +
        " " +
        (a.affectedStops || []).join(" ")
      ).toLowerCase();
      return hay.includes(q.toLowerCase());
    });

  const groups: { key: "major" | "minor" | "planned"; label: string; color: string }[] = [
    { key: "major", label: "Major Disruptions", color: "var(--sr-coral)" },
    { key: "minor", label: "Minor Advisories", color: "var(--sr-amber)" },
    { key: "planned", label: "Planned Changes", color: "var(--sr-cyan)" },
  ];

  return (
    <section style={{ paddingBottom: 90 }}>
      <div style={{ padding: "20px 24px 4px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
          }}
        >
          <Meta tone="cyan" style={{ letterSpacing: "0.28em", fontWeight: 600 }}>
            <Dot
              color="var(--sr-cyan)"
              size={5}
              style={{ marginRight: 6, verticalAlign: "middle" }}
            />
            MTA Alert Board
          </Meta>
          <Meta>Updated 26s ago</Meta>
        </div>
      </div>

      <div
        style={{
          padding: "12px 24px 0",
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
        }}
      >
        {(
          [
            { k: "all", label: "All", icon: "▸" },
            { k: "train", label: "Train", icon: "◉" },
            { k: "bus", label: "Bus", icon: "▭" },
            { k: "planned", label: "Planned", icon: "◆" },
            { k: "active", label: "Active", icon: "●" },
          ] as { k: AlertScope; label: string; icon: string }[]
        ).map((c) => {
          const active = scope === c.k;
          const n = kindCount(c.k);
          return (
            <button
              key={c.k}
              onClick={() => setScope(c.k)}
              aria-pressed={active}
              style={{
                padding: "7px 10px",
                cursor: "pointer",
                border: 0,
                background: active ? "var(--sr-cyan)" : "var(--sr-surface-2)",
                color: active ? "#241704" : "var(--sr-fg-2)",
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                fontFamily: "var(--sr-mono)",
                fontSize: 10,
                letterSpacing: "0.18em",
                textTransform: "uppercase",
                fontWeight: 600,
                transition: "background var(--sr-dur-1), color var(--sr-dur-1)",
              }}
            >
              <span style={{ opacity: 0.7 }}>{c.icon}</span>
              {c.label}
              <span
                style={{
                  fontFamily: "var(--sr-mono)",
                  fontSize: 9.5,
                  padding: "1px 6px",
                  background: active
                    ? "rgba(36,23,4,0.22)"
                    : "rgba(255,255,255,0.08)",
                  color: active ? "#241704" : "var(--sr-fg-3)",
                }}
              >
                {n}
              </span>
            </button>
          );
        })}
      </div>

      <div style={{ padding: "12px 24px 8px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            background: "var(--sr-surface-2)",
            border: "1px solid var(--sr-rule)",
            padding: "8px 12px",
          }}
        >
          <span
            style={{
              fontFamily: "var(--sr-mono)",
              fontSize: 10,
              color: "var(--sr-muted)",
            }}
          >
            □
          </span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search by line, station, or keyword"
            style={{
              flex: 1,
              background: "transparent",
              border: 0,
              outline: "none",
              fontFamily: "var(--sr-display)",
              fontWeight: 400,
              fontSize: 13,
              color: "var(--sr-fg)",
            }}
          />
          {q && (
            <button
              onClick={() => setQ("")}
              aria-label="Clear search"
              style={{
                background: "transparent",
                border: 0,
                cursor: "pointer",
                color: "var(--sr-muted)",
                fontFamily: "var(--sr-mono)",
                fontSize: 11,
              }}
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {groups.map((g) => {
        const rows = filtered.filter((a) => a.sev === g.key);
        if (!rows.length) return null;
        return (
          <div key={g.key} style={{ marginTop: 6 }}>
            <div
              style={{
                padding: "14px 24px 8px",
                borderTop: "1px solid var(--sr-rule)",
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <Dot color={g.color} size={5} pulse={g.key === "major"} />
              <span
                style={{
                  fontFamily: "var(--sr-mono)",
                  fontSize: 10,
                  letterSpacing: "0.16em",
                  color: g.color,
                  textTransform: "uppercase",
                  fontWeight: 600,
                }}
              >
                {g.label}
              </span>
              <Meta>· {rows.length}</Meta>
            </div>
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {rows.map((a, i) => (
                <AlertRow key={`${a.title}-${i}`} alert={a} accent={g.color} />
              ))}
            </ul>
          </div>
        );
      })}

      {filtered.length === 0 && (
        <div style={{ padding: "30px 24px", textAlign: "center" }}>
          <Meta>
            No alerts match &ldquo;{q}&rdquo; in {scope}.
          </Meta>
        </div>
      )}
    </section>
  );
}

function AlertRow({ alert, accent }: { alert: ServiceAlert; accent: string }) {
  const [open, setOpen] = useState(false);
  return (
    <li
      style={{
        borderTop: "1px solid var(--sr-rule)",
        borderLeft: open ? `2px solid ${accent}` : "2px solid transparent",
        transition: "border-color var(--sr-dur-1)",
      }}
    >
      <MagicCard intensity={0.06} size={280}>
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          style={{
            width: "100%",
            textAlign: "left",
            background: "transparent",
            border: 0,
            cursor: "pointer",
            padding: "12px 24px",
            display: "grid",
            gridTemplateColumns: "auto 1fr auto auto",
            gap: 12,
            alignItems: "center",
            color: "var(--sr-fg)",
          }}
        >
          <div style={{ display: "flex", gap: 4 }}>
            {alert.lines.map((l) => (
              <LineBullet key={l} line={l} size={22} />
            ))}
          </div>
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontFamily: "var(--sr-display)",
                fontSize: 13.5,
                fontWeight: 500,
                color: "var(--sr-fg)",
                lineHeight: 1.25,
                letterSpacing: "-0.005em",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {alert.title}
            </div>
            <Meta
              style={{
                display: "block",
                marginTop: 3,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {alert.sub}
            </Meta>
          </div>
          <Meta>{alert.lastUpdate}</Meta>
          <span
            style={{
              color: "var(--sr-muted)",
              fontFamily: "var(--sr-mono)",
              fontSize: 11,
              transform: open ? "rotate(180deg)" : "none",
              transition: "transform var(--sr-dur-2)",
            }}
          >
            ▾
          </span>
        </button>

        {open && (
          <BlurFade>
            <div style={{ padding: "4px 24px 16px" }}>
              {alert.aiContext && (
                <div
                  style={{
                    padding: "12px 14px",
                    border: "1px solid var(--sr-rule-bright)",
                    background: "rgba(216,155,43,0.05)",
                    position: "relative",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      top: -7,
                      left: 10,
                      background: "var(--sr-surface)",
                      padding: "0 6px",
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                    }}
                  >
                    <Dot color="var(--sr-cyan)" size={5} />
                    <Meta tone="cyan" style={{ fontSize: 9.5, letterSpacing: "0.16em" }}>
                      ATLAS CONTEXT
                    </Meta>
                  </div>
                  <p
                    style={{
                      fontFamily: "var(--sr-display)",
                      fontWeight: 400,
                      fontSize: 13,
                      lineHeight: 1.5,
                      color: "var(--sr-fg-2)",
                    }}
                  >
                    {alert.aiContext}
                  </p>
                  {alert.confidence && (
                    <div
                      style={{
                        marginTop: 8,
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "3px 8px",
                        border: "1px solid var(--sr-rule)",
                      }}
                    >
                      <Dot color="var(--sr-cyan)" size={4} />
                      <Meta tone="cyan" style={{ fontSize: 9.5 }}>
                        Confidence: {alert.confidence}
                      </Meta>
                    </div>
                  )}
                </div>
              )}

              {(alert.fullText || alert.sub || alert.title) && (
                <div
                  style={{
                    marginTop: 14,
                    padding: "12px 14px",
                    border: "1px solid var(--sr-rule)",
                    background: "rgba(255,255,255,0.035)",
                  }}
                >
                  <Meta tone="ink" style={{ display: "block", marginBottom: 8 }}>
                    Full alert
                  </Meta>
                  <p
                    style={{
                      margin: 0,
                      fontFamily: "var(--sr-display)",
                      fontSize: 12.8,
                      lineHeight: 1.55,
                      color: "var(--sr-fg-2)",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {alert.fullText || alert.sub || alert.title}
                  </p>
                </div>
              )}

              <div
                style={{
                  marginTop: 14,
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  rowGap: 10,
                  columnGap: 10,
                }}
              >
                <AlertField k="Started" v={alert.startedAgo} />
                <AlertField k="Last update" v={alert.lastUpdate} />
                <AlertField k="Direction" v={alert.direction ?? "—"} />
                <AlertField k="Est. clear" v={alert.estClear ?? "—"} />
              </div>

              {alert.affectedStops && alert.affectedStops.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  <Meta style={{ display: "block", marginBottom: 8 }}>
                    Affected stops
                  </Meta>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {alert.affectedStops.slice(0, 4).map((s) => (
                      <span
                        key={s}
                        style={{
                          padding: "3px 8px",
                          border: "1px solid var(--sr-rule-bright)",
                          fontFamily: "var(--sr-mono)",
                          fontSize: 10,
                          color: "var(--sr-fg-2)",
                          letterSpacing: "0.04em",
                        }}
                      >
                        {s}
                      </span>
                    ))}
                    {alert.affectedStops.length > 4 && (
                      <span
                        style={{
                          padding: "3px 8px",
                          fontFamily: "var(--sr-mono)",
                          fontSize: 10,
                          color: "var(--sr-muted)",
                        }}
                      >
                        +{alert.affectedStops.length - 4} more
                      </span>
                    )}
                  </div>
                </div>
              )}

              {alert.activity && alert.activity.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  <Meta style={{ display: "block", marginBottom: 8 }}>
                    Activity
                  </Meta>
                  <ul
                    style={{
                      listStyle: "none",
                      display: "flex",
                      flexDirection: "column",
                      gap: 8,
                      margin: 0,
                      padding: 0,
                    }}
                  >
                    {alert.activity.map((e, i) => (
                      <li
                        key={`${e.t}-${i}`}
                        style={{
                          display: "grid",
                          gridTemplateColumns: "auto 1fr",
                          gap: 10,
                          alignItems: "baseline",
                        }}
                      >
                        <Dot
                          color={
                            i === 0 ? "var(--sr-cyan)" : "var(--sr-muted)"
                          }
                          size={5}
                        />
                        <div>
                          <div
                            style={{
                              fontFamily: "var(--sr-display)",
                              fontSize: 12.5,
                              color: "var(--sr-fg)",
                              fontWeight: i === 0 ? 500 : 400,
                            }}
                          >
                            {e.t}
                          </div>
                          <Meta>{e.e}</Meta>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </BlurFade>
        )}
      </MagicCard>
    </li>
  );
}

function AlertField({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div>
      <Meta>{k}</Meta>
      <div
        style={{
          marginTop: 3,
          fontFamily: "var(--sr-display)",
          fontSize: 13,
          color: "var(--sr-fg)",
        }}
      >
        {v}
      </div>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────
   ISSUES FOOTER
   ────────────────────────────────────────────────────────────── */

function IssuesFooter({ issues }: { issues: IssueItem[] }) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed || issues.length === 0) return null;
  const head = issues[0];
  const footerStyle: CSSProperties = {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    borderTop: "1px solid rgba(248,113,113,0.20)",
    background: "rgba(248,113,113,0.07)",
    padding: "10px 14px 10px 16px",
    display: "flex",
    alignItems: "center",
    gap: 10,
    backdropFilter: "blur(6px)",
  };
  return (
    <footer style={footerStyle}>
      <Dot color="var(--sr-coral)" size={6} pulse />
      <span
        style={{
          fontFamily: "var(--sr-display)",
          fontSize: 13,
          fontWeight: 500,
          color: "var(--sr-fg)",
        }}
      >
        {head.title}
      </span>
      <Meta tone="coral" style={{ fontSize: 9.5 }}>
        {head.detail}
      </Meta>
      <span style={{ flex: 1 }} />
      <button
        style={{
          background: "transparent",
          border: 0,
          cursor: "pointer",
          color: "var(--sr-fg-2)",
          fontFamily: "var(--sr-mono)",
          fontSize: 9.5,
          letterSpacing: "0.16em",
          textTransform: "uppercase",
        }}
      >
        View
      </button>
      <button
        onClick={() => setDismissed(true)}
        aria-label="Dismiss issues"
        style={{
          background: "transparent",
          border: 0,
          cursor: "pointer",
          color: "var(--sr-fg-3)",
          fontFamily: "var(--sr-mono)",
          fontSize: 12,
          padding: "0 0 0 6px",
        }}
      >
        ✕
      </button>
    </footer>
  );
}
