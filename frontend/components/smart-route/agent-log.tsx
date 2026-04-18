"use client";

import { useEffect, useRef } from "react";
import type { AgentLogEntry } from "@/lib/smart-route";

interface Props {
  accent: string;
  entries: AgentLogEntry[];
  live: boolean;
}

export function AgentLog({ accent, entries, live }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [entries.length]);

  return (
    <div
      className="flex flex-col"
      style={{
        background: "rgba(0,0,0,0.35)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 14,
        overflow: "hidden",
        minHeight: 0,
        flex: 1,
      }}
    >
      <div
        className="flex items-center gap-2"
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid rgba(255,255,255,0.05)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-geist), sans-serif",
            fontSize: 10,
            letterSpacing: "0.18em",
            color: "rgba(255,255,255,0.65)",
            fontWeight: 500,
          }}
        >
          AGENT STREAM
        </span>
        <span
          style={{
            fontSize: 9,
            fontFamily: "var(--font-jetbrains-mono), monospace",
            color: "rgba(255,255,255,0.35)",
            letterSpacing: "0.08em",
          }}
        >
          grok · claude
        </span>
        {live && (
          <span
            className="ml-auto flex items-center gap-1.5"
            style={{
              fontFamily: "var(--font-jetbrains-mono), monospace",
              fontSize: 9,
              color: "#9ccfbf",
              letterSpacing: "0.1em",
            }}
          >
            <span
              style={{
                width: 5,
                height: 5,
                borderRadius: 3,
                background: "#9ccfbf",
                animation: "srPulse 1.2s infinite",
              }}
            />
            STREAMING
          </span>
        )}
      </div>
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "10px 14px",
          fontFamily: "var(--font-jetbrains-mono), monospace",
          fontSize: 11,
          lineHeight: 1.55,
          minHeight: 0,
        }}
      >
        {entries.map((e, i) => {
          const levelColor =
            e.level === "decision"
              ? accent
              : e.level === "detect"
                ? "#f0b04a"
                : e.level === "reason"
                  ? "#9ccfbf"
                  : "rgba(255,255,255,0.55)";
          return (
            <div
              key={i}
              style={{
                marginBottom: 6,
                opacity: i === entries.length - 1 ? 1 : 0.78,
              }}
            >
              <span style={{ color: "rgba(255,255,255,0.35)", marginRight: 8 }}>
                [{e.t}]
              </span>
              <span
                style={{
                  color: levelColor,
                  marginRight: 6,
                  letterSpacing: "0.04em",
                }}
              >
                {e.level.toUpperCase().padEnd(8, " ")}
              </span>
              <span style={{ color: "rgba(255,255,255,0.82)" }}>{e.text}</span>
            </div>
          );
        })}
        {live && (
          <div
            className="flex items-center gap-1"
            style={{ marginTop: 4, color: accent }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 3,
                background: accent,
                animation: "srPulse 0.9s infinite",
              }}
            />
            <span
              style={{
                width: 4,
                height: 4,
                borderRadius: 2,
                background: accent,
                opacity: 0.6,
                animation: "srPulse 0.9s infinite 0.15s",
              }}
            />
            <span
              style={{
                width: 3,
                height: 3,
                borderRadius: 2,
                background: accent,
                opacity: 0.4,
                animation: "srPulse 0.9s infinite 0.3s",
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
