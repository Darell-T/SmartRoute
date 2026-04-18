"use client";

import type { ServiceAlert } from "@/types";
import { getLineColor } from "@/components/map/route-layers";

interface Props {
  alerts: ServiceAlert[];
}

interface LineStatus {
  key: string;
  lines: string[];
  status: "delayed" | "caution" | "nominal";
  label: string;
}

const DEFAULT_STATUS: LineStatus[] = [
  { key: "c/e", lines: ["C", "E"], status: "nominal", label: "Running normally" },
  { key: "4/5/6", lines: ["4", "5", "6"], status: "nominal", label: "Running normally" },
  { key: "1/2/3", lines: ["1", "2", "3"], status: "nominal", label: "Running normally" },
  { key: "n/q/r/w", lines: ["N", "Q", "R", "W"], status: "nominal", label: "Running normally" },
  { key: "a", lines: ["A"], status: "nominal", label: "Running normally" },
];

function deriveStatus(alerts: ServiceAlert[]): LineStatus[] {
  const affected = new Map<string, string>();
  for (const a of alerts) {
    for (const r of a.routeIds || []) {
      if (!affected.has(r)) affected.set(r, a.header);
    }
  }

  return DEFAULT_STATUS.map((row) => {
    const hit = row.lines.find((l) => affected.has(l));
    if (hit) {
      const header = affected.get(hit) || "";
      const isDelay = /delay|suspend|diversion|reroute|service change/i.test(header);
      return {
        ...row,
        status: isDelay ? "delayed" : "caution",
        label: header.length > 36 ? header.slice(0, 34) + "…" : header,
      };
    }
    return row;
  });
}

function StatusRow({ line, label, color }: { line: string; label: string; color: string }) {
  return (
    <div className="flex items-center gap-2.5" style={{ fontSize: 12 }}>
      <div
        style={{
          width: 36,
          fontFamily: "var(--font-geist), sans-serif",
          fontWeight: 600,
          fontSize: 11,
          color: "rgba(255,255,255,0.8)",
        }}
      >
        {line}
      </div>
      <div
        style={{
          width: 5,
          height: 5,
          borderRadius: 3,
          background: color,
          boxShadow: `0 0 5px ${color}88`,
          flexShrink: 0,
        }}
      />
      <div
        className="truncate"
        style={{
          fontFamily: "var(--font-geist), sans-serif",
          fontSize: 11.5,
          color: "rgba(255,255,255,0.65)",
        }}
      >
        {label}
      </div>
    </div>
  );
}

export function NetworkStatus({ alerts }: Props) {
  const rows = deriveStatus(alerts);
  return (
    <div
      style={{
        padding: 12,
        background: "rgba(255,255,255,0.025)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 12,
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-geist), sans-serif",
          fontSize: 10,
          letterSpacing: "0.14em",
          color: "rgba(255,255,255,0.5)",
          marginBottom: 10,
        }}
      >
        NETWORK STATUS
      </div>
      <div className="flex flex-col gap-1.5">
        {rows.map((r) => {
          const color =
            r.status === "delayed"
              ? "#ff6868"
              : r.status === "caution"
                ? "#f0b04a"
                : "#9ccfbf";
          const lineLabel = r.lines.join("/");
          return (
            <StatusRow
              key={r.key}
              line={lineLabel}
              label={r.label}
              color={color}
            />
          );
        })}
      </div>
      <div
        className="flex items-center gap-2"
        style={{
          marginTop: 10,
          paddingTop: 10,
          borderTop: "1px solid rgba(255,255,255,0.05)",
          fontFamily: "var(--font-jetbrains-mono), monospace",
          fontSize: 9,
          color: "rgba(255,255,255,0.35)",
          letterSpacing: "0.1em",
        }}
      >
        <span
          style={{
            width: 4,
            height: 4,
            borderRadius: 2,
            background: getLineColor("4"),
          }}
        />
        MTA GTFS-RT
      </div>
    </div>
  );
}
