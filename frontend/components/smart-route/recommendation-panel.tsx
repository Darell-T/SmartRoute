"use client";

import dynamic from "next/dynamic";
import { Loader2, Play } from "lucide-react";
import type { RouteSummary, RouteLeg } from "@/lib/smart-route";
import { getLineColor } from "@/components/map/route-layers";

const AgentOrb = dynamic(() => import("./agent-orb").then((m) => m.AgentOrb), {
  ssr: false,
});

export type AgentState = "idle" | "thinking" | "speaking";

interface Props {
  accent: string;
  state: AgentState;
  summary: RouteSummary | null;
  recommendationText: string;
  displayedText: string;
  thinkingText: string;
  voicePlaying: boolean;
  onPlayVoice: () => void;
  showDetails: boolean;
  onToggleDetails: () => void;
  confidence: number;
  errorText: string | null;
  onRetry: () => void;
}

function LineBullet({
  letter,
  color,
  size = 34,
}: {
  letter: string;
  color: string;
  size?: number;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
        borderRadius: size / 2,
        background: color,
        color: "#0a0e15",
        fontFamily: "var(--font-geist), sans-serif",
        fontWeight: 700,
        fontSize: size * 0.5,
        letterSpacing: "-0.01em",
        lineHeight: 1,
        flexShrink: 0,
        boxShadow: `0 0 0 1px rgba(0,0,0,0.35), 0 2px 10px ${color}33`,
      }}
    >
      {letter}
    </span>
  );
}

function ArrowGlyph({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 14 14"
      fill="none"
      stroke="rgba(255,255,255,0.55)"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flexShrink: 0 }}
    >
      <path d="M3 7 L11 7 M8 4 L11 7 L8 10" />
    </svg>
  );
}

function IconCheck({ color, size = 12 }: { color: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke={color}
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="3 8.5 7 12.5 13 4.5" />
    </svg>
  );
}

function IconWalk({ size = 12 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="9" cy="3" r="1.2" />
      <path d="M7 7 L10 7 L11 10 L13 11" />
      <path d="M7 7 L5 10 L4 14" />
      <path d="M10 7 L10.5 13" />
    </svg>
  );
}

function IconTrain({ size = 12 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3" y="2" width="10" height="10" rx="2" />
      <circle cx="5.5" cy="9" r="0.7" />
      <circle cx="10.5" cy="9" r="0.7" />
      <path d="M3 6 L13 6" />
      <path d="M5 14 L4 15 M11 14 L12 15" />
    </svg>
  );
}

function IconSwap({ size = 12 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="3 6 6 3 9 6" />
      <path d="M6 3 L6 13" />
      <polyline points="13 10 10 13 7 10" />
      <path d="M10 13 L10 3" />
    </svg>
  );
}

function LegRow({ leg, accent }: { leg: RouteLeg; accent: string }) {
  const isRail = leg.mode === "rail" || leg.mode === "bus";
  const isTransfer = leg.mode === "transfer";
  const icon = isRail ? (
    <IconTrain />
  ) : isTransfer ? (
    <IconSwap />
  ) : (
    <IconWalk />
  );
  return (
    <div
      className="flex items-center gap-2.5"
      style={{
        padding: "6px 8px",
        background: "rgba(255,255,255,0.02)",
        borderRadius: 6,
        border: "1px solid rgba(255,255,255,0.04)",
      }}
    >
      <div
        style={{
          color: isRail && leg.color ? leg.color : "rgba(255,255,255,0.5)",
        }}
      >
        {icon}
      </div>
      {isRail && leg.line && (
        <div
          className="flex items-center justify-center flex-shrink-0"
          style={{
            width: 18,
            height: 18,
            borderRadius: 9,
            background: leg.color || accent,
            fontFamily: "var(--font-geist), sans-serif",
            fontWeight: 700,
            fontSize: 10,
            color: "#0b0e13",
          }}
        >
          {leg.line}
        </div>
      )}
      <div
        className="flex-1"
        style={{
          fontFamily: "var(--font-geist), sans-serif",
          fontSize: 11.5,
          color: "rgba(255,255,255,0.82)",
        }}
      >
        {leg.detail}
      </div>
      <div
        style={{
          fontFamily: "var(--font-jetbrains-mono), monospace",
          fontSize: 11,
          color: "rgba(255,255,255,0.55)",
        }}
      >
        {leg.min}m
      </div>
    </div>
  );
}

export function RecommendationPanel({
  accent,
  state,
  summary,
  recommendationText,
  displayedText,
  thinkingText,
  voicePlaying,
  onPlayVoice,
  showDetails,
  onToggleDetails,
  confidence,
  errorText,
  onRetry,
}: Props) {
  const isThinking = state === "thinking";
  const isIdle = state === "idle" && !summary && !recommendationText;

  const headline = summary?.primaryHeadline;
  const depart = summary?.departLabel ?? "—";
  const arrive = summary?.arriveLabel ?? "—";
  const total = summary?.totalMin ? `${summary.totalMin} MIN` : "—";

  const revealText =
    state === "speaking" && displayedText ? displayedText : recommendationText;
  const voiceBlurb = isThinking
    ? thinkingText || "Processing route options…"
    : isIdle
      ? "Awaiting destination. Ask where you'd like to go."
      : revealText || recommendationText;

  return (
    <div
      style={{
        background:
          "linear-gradient(180deg, rgba(212,167,255,0.08), rgba(212,167,255,0.02))",
        border: "1px solid rgba(212,167,255,0.22)",
        borderRadius: 14,
        padding: 18,
        position: "relative",
        overflow: "hidden",
        animation: "srCardIn 300ms ease-out",
      }}
    >
      {/* Corner glow */}
      <div
        style={{
          position: "absolute",
          top: -40,
          right: -40,
          width: 120,
          height: 120,
          borderRadius: "50%",
          background: `radial-gradient(circle, ${accent}22, transparent 70%)`,
          pointerEvents: "none",
        }}
      />

      {/* Header — orb + label + confidence */}
      <div
        className="flex items-center justify-between"
        style={{ marginBottom: 12 }}
      >
        <div className="flex items-center gap-2.5">
          <div
            style={{
              width: 28,
              height: 28,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: 14,
              background: "rgba(0,0,0,0.35)",
              border: `1px solid ${accent}33`,
              overflow: "hidden",
            }}
          >
            <AgentOrb phase={state} size={28} accent={accent} />
          </div>
          <span
            style={{
              fontFamily: "var(--font-geist), sans-serif",
              fontSize: 10.5,
              letterSpacing: "0.18em",
              color: "rgba(255,255,255,0.72)",
              fontWeight: 500,
            }}
          >
            ATLAS RECOMMENDATION
          </span>
          {isThinking && (
            <Loader2
              size={10}
              style={{
                color: accent,
                animation: "spin 1s linear infinite",
              }}
            />
          )}
        </div>
        {summary && (
          <span
            style={{
              fontFamily: "var(--font-jetbrains-mono), monospace",
              fontSize: 10.5,
              color: `${accent}cc`,
              letterSpacing: "0.04em",
            }}
          >
            CONF · 0.{confidence}
          </span>
        )}
      </div>

      {/* Error state */}
      {errorText && (
        <>
          <div
            style={{
              fontFamily: "var(--font-instrument-serif), serif",
              fontSize: 22,
              lineHeight: 1.15,
              color: "rgba(255,200,180,0.95)",
            }}
          >
            {errorText}
          </div>
          <button
            onClick={onRetry}
            className="cursor-pointer"
            style={{
              marginTop: 10,
              background: `${accent}22`,
              border: `1px solid ${accent}66`,
              borderRadius: 999,
              padding: "6px 12px",
              color: accent,
              fontFamily: "var(--font-geist), sans-serif",
              fontSize: 11,
              letterSpacing: "0.04em",
            }}
          >
            Retry
          </button>
        </>
      )}

      {/* Route hero — line pills + transfer station lead; serif tagline demoted to subtext */}
      {!errorText && summary && summary.transitLines.length > 0 && (
        <div style={{ marginBottom: 2 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              flexWrap: "wrap",
              rowGap: 8,
              columnGap: 10,
              fontFamily: "var(--font-instrument-serif), serif",
              fontSize: 40,
              lineHeight: 1.04,
              color: "#fff",
              letterSpacing: "-0.02em",
            }}
          >
            <span>Take</span>
            {summary.transitLines.map((line, i) => (
              <span
                key={`${line}-${i}`}
                style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
              >
                {i > 0 && <ArrowGlyph size={16} />}
                <LineBullet
                  letter={line}
                  color={getLineColor(line)}
                  size={36}
                />
              </span>
            ))}
            {summary.transferStation && (
              <span
                style={{
                  color: "rgba(255,255,255,0.72)",
                  fontStyle: "italic",
                  fontSize: 32,
                  letterSpacing: "-0.01em",
                }}
              >
                via {summary.transferStation}
              </span>
            )}
          </div>
          <div
            style={{
              marginTop: 8,
              fontFamily: "var(--font-geist), sans-serif",
              fontSize: 13,
              lineHeight: 1.45,
              color: "rgba(255,255,255,0.58)",
              letterSpacing: "0.01em",
            }}
          >
            Fastest reliable option right now.
          </div>
        </div>
      )}

      {/* Walk-only fallback headline (no transit lines) */}
      {!errorText &&
        summary &&
        summary.transitLines.length === 0 &&
        headline && (
          <div
            style={{
              fontFamily: "var(--font-instrument-serif), serif",
              fontSize: 32,
              lineHeight: 1.08,
              color: "#fff",
              letterSpacing: "-0.015em",
            }}
          >
            {headline.prefix}{" "}
            <span style={{ color: accent, fontStyle: "italic" }}>
              {headline.emphasis}
            </span>
            {headline.suffix}
          </div>
        )}

      {/* Idle placeholder headline */}
      {!errorText && !summary && (
        <div
          style={{
            fontFamily: "var(--font-instrument-serif), serif",
            fontSize: 26,
            lineHeight: 1.1,
            color: "rgba(255,255,255,0.85)",
            letterSpacing: "-0.01em",
          }}
        >
          {isThinking ? (
            <>
              Thinking <span style={{ color: accent }}>through</span> live
              signal…
            </>
          ) : (
            <>
              Where are you <span style={{ color: accent }}>headed</span>, sir?
            </>
          )}
        </div>
      )}

      {/* Voice blurb */}
      {!errorText && (
        <div
          style={{
            marginTop: 12,
            paddingLeft: 10,
            borderLeft: `2px solid ${accent}55`,
            fontFamily: "var(--font-instrument-serif), serif",
            fontStyle: "italic",
            fontSize: 14,
            lineHeight: 1.45,
            color: "rgba(255,255,255,0.82)",
            minHeight: 32,
          }}
        >
          &ldquo;{voiceBlurb}&rdquo;
        </div>
      )}

      {/* Voice / Why this route buttons */}
      {!errorText && summary && (
        <div className="flex gap-2" style={{ marginTop: 12 }}>
          <button
            onClick={onPlayVoice}
            className="flex items-center gap-1.5 cursor-pointer"
            style={{
              background: voicePlaying
                ? `${accent}22`
                : "rgba(255,255,255,0.04)",
              border: `1px solid ${voicePlaying ? accent + "66" : "rgba(255,255,255,0.08)"}`,
              borderRadius: 999,
              padding: "6px 12px",
              color: voicePlaying ? accent : "rgba(255,255,255,0.85)",
              fontFamily: "var(--font-geist), sans-serif",
              fontSize: 11,
              letterSpacing: "0.04em",
            }}
          >
            <Play size={10} />
            {voicePlaying ? "Playing…" : "Hear from ATLAS"}
          </button>
          <button
            onClick={onToggleDetails}
            className="cursor-pointer"
            style={{
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 999,
              padding: "6px 12px",
              color: "rgba(255,255,255,0.8)",
              fontFamily: "var(--font-geist), sans-serif",
              fontSize: 11,
              letterSpacing: "0.04em",
            }}
          >
            {showDetails ? "Hide details" : "Why this route"}
          </button>
        </div>
      )}

      {/* Stats row */}
      {!errorText && summary && (
        <div
          className="grid"
          style={{
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 0,
            marginTop: 16,
            paddingTop: 14,
            borderTop: "1px solid rgba(255,255,255,0.06)",
          }}
        >
          {(
            [
              ["DEPART", depart],
              ["ARRIVE", arrive],
              ["TOTAL", total],
            ] as const
          ).map(([k, v], i) => (
            <div
              key={k}
              style={{
                padding: "0 12px",
                borderRight:
                  i < 2 ? "1px solid rgba(255,255,255,0.06)" : "none",
                paddingLeft: i === 0 ? 0 : 12,
              }}
            >
              <div
                style={{
                  fontFamily: "var(--font-geist), sans-serif",
                  fontSize: 9.5,
                  letterSpacing: "0.16em",
                  color: "rgba(255,255,255,0.52)",
                  marginBottom: 4,
                  fontWeight: 500,
                }}
              >
                {k}
              </div>
              <div
                style={{
                  fontFamily: "var(--font-jetbrains-mono), monospace",
                  fontSize: 14,
                  color: "#fff",
                  fontWeight: 500,
                }}
              >
                {v}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Why this route */}
      {!errorText && summary && showDetails && (
        <div
          style={{
            marginTop: 14,
            padding: "12px 14px",
            background: "rgba(0,0,0,0.25)",
            borderRadius: 10,
            border: "1px solid rgba(255,255,255,0.05)",
          }}
        >
          <div className="flex items-center gap-2" style={{ marginBottom: 8 }}>
            <IconCheck color={accent} />
            <span
              style={{
                fontFamily: "var(--font-geist), sans-serif",
                fontSize: 10.5,
                letterSpacing: "0.16em",
                color: "rgba(255,255,255,0.65)",
                fontWeight: 500,
              }}
            >
              REASONING
            </span>
            <span
              className="ml-auto flex items-center gap-1"
              style={{
                fontSize: 10,
                color: "#ff6868",
                fontFamily: "var(--font-jetbrains-mono), monospace",
                letterSpacing: "0.12em",
                fontWeight: 500,
              }}
            >
              <span
                style={{
                  width: 5,
                  height: 5,
                  borderRadius: 3,
                  background: "#ff6868",
                  animation: "srPulse 1.2s infinite",
                }}
              />
              LIVE
            </span>
          </div>
          <p
            style={{
              fontSize: 12.5,
              lineHeight: 1.55,
              color: "rgba(255,255,255,0.78)",
              margin: 0,
              fontFamily: "var(--font-geist), sans-serif",
            }}
          >
            {recommendationText}
          </p>
          <div className="flex flex-col gap-1.5" style={{ marginTop: 12 }}>
            {summary.legs.map((leg, i) => (
              <LegRow key={i} leg={leg} accent={accent} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
