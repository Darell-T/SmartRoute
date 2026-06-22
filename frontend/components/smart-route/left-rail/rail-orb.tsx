"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute — Left Rail orb wrapper

   Adapts the project's existing three.js `AgentOrb` to the rail's tone +
   live-state vocabulary. The orb is the only brand presence in the UI; it
   must NEVER appear outside ATLAS conversation contexts.

   Tone → accent color mapping mirrors the design tokens:
   - "clear"      → gold   (#F0B94B)  — default, route picked
   - "minor"      → amber  (#D89B2B)  — warnings, thinking
   - "disrupted"  → coral  (#F87171)  — errors, major incidents
   - "sage"       → sage   (#86C29A)  — quiet/healthy network status

   ATLAS state → AgentOrb phase mapping:
     standby  → idle       (calm, low-energy ambient)
     thinking → thinking   (visible particles + connecting lines while
                              the ATLAS pipeline is running)
     result   → speaking   (same compact breathing read while narration
                              auto-plays the rationale)
     error    → idle       (held still; accent goes coral via tone)
   ════════════════════════════════════════════════════════════════════════ */

import { AgentOrb } from "@/components/smart-route/agent-orb";
import type { JarvisState } from "./types";

export type RailOrbTone = "clear" | "minor" | "disrupted" | "sage";

const TONE_ACCENT: Record<RailOrbTone, string> = {
  clear: "#F0B94B",
  minor: "#D89B2B",
  disrupted: "#F87171",
  sage: "#86C29A",
};

// Resting (idle-phase) particle color per tone — a dimmer, warmer cast of
// the accent so the standby orb stays gold on the glass rail instead of
// the AgentOrb's legacy blue.
const TONE_IDLE_ACCENT: Record<RailOrbTone, string> = {
  clear: "#C8902F",
  minor: "#B07D28",
  disrupted: "#C8625E",
  sage: "#6f9d80",
};

export function jarvisStateToOrbTone(state: JarvisState): RailOrbTone {
  if (state === "error") return "disrupted";
  if (state === "thinking") return "minor";
  return "clear";
}

interface RailOrbProps {
  size?: number;
  tone?: RailOrbTone;
  /**
   * Phase override. If omitted, the orb defaults to "idle". ATLAS callers
   * should pass "thinking" while the pipeline runs and "speaking" while TTS
   * narrates the rationale.
   */
  phase?: "idle" | "thinking" | "speaking";
}

export function RailOrb({ size = 64, tone = "clear", phase = "idle" }: RailOrbProps) {
  return (
    <div
      style={{
        width: size,
        height: size,
        position: "relative",
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        filter: "drop-shadow(0 0 14px rgba(216, 155, 43, 0.34))",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: size * 0.1,
          borderRadius: "50%",
          pointerEvents: "none",
          background: `radial-gradient(circle, ${TONE_ACCENT[tone]}30 0%, ${TONE_ACCENT[tone]}10 45%, transparent 72%)`,
          filter: "blur(2px)",
        }}
      />
      <AgentOrb
        phase={phase}
        size={size}
        accent={TONE_ACCENT[tone]}
        idleAccent={TONE_IDLE_ACCENT[tone]}
      />
      {/* Subtle radial halo — gives the orb a contained "lantern" presence on
          the dark rail surface without competing with the particle motion. */}
      <span
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          borderRadius: "50%",
          pointerEvents: "none",
          boxShadow: `inset 0 0 ${size * 0.18}px ${TONE_ACCENT[tone]}22, 0 0 ${
            size * 0.34
          }px ${TONE_ACCENT[tone]}1c`,
        }}
      />
    </div>
  );
}
