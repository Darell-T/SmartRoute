"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useReducedMotion } from "motion/react";
import { BorderBeam } from "@/components/ui/border-beam";
import {
  NETWORK_SIGNAL_THEME,
  normalizeNetworkStatus,
  type NetworkHealthStatus,
} from "./network-orb-color";

export function IntelligenceHub({
  children,
  status,
  activityKey,
}: {
  children: ReactNode;
  status?: NetworkHealthStatus | null;
  activityKey?: string | number | null;
}) {
  const reduceMotion = useReducedMotion();
  const [isFeedUpdating, setIsFeedUpdating] = useState(false);
  const didMountRef = useRef(false);
  const signal = normalizeNetworkStatus(status);
  const theme = NETWORK_SIGNAL_THEME[signal];

  useEffect(() => {
    if (activityKey == null) return;
    if (!didMountRef.current) {
      didMountRef.current = true;
      return;
    }

    if (reduceMotion) {
      setIsFeedUpdating(true);
      const id = window.setTimeout(() => setIsFeedUpdating(false), 260);
      return () => window.clearTimeout(id);
    }

    setIsFeedUpdating(true);
    const id = window.setTimeout(() => setIsFeedUpdating(false), 1200);
    return () => window.clearTimeout(id);
  }, [activityKey, reduceMotion]);

  const style = {
    "--sr-beam-from": theme.beamFrom,
    "--sr-beam-to": theme.beamTo,
    "--sr-beam-glow": theme.beamGlow,
    "--sr-beam-opacity": theme.beamOpacity,
    "--sr-beam-active-opacity": theme.beamActiveOpacity,
  } as CSSProperties;

  return (
    <aside
      className="sr-ihub"
      aria-label="Intelligence Hub"
      data-signal={signal}
      data-feed-updating={isFeedUpdating ? "true" : "false"}
      style={style}
    >
      {reduceMotion ? null : (
        <BorderBeam
          duration={theme.beamDuration}
          size={theme.beamSize}
          colorFrom={theme.beamFrom}
          colorTo={theme.beamTo}
          borderWidth={1.2}
          // CRITICAL: continuous infinite motion must use a linear curve.
          // Any ease-in / ease-out / cubic-bezier curve makes the beam
          // decelerate near 100% offset and then snap back to 0% with full
          // velocity, which reads as a visible pause/stutter at the loop
          // seam. With `linear` + `repeat: Infinity` + `repeatType: "loop"`,
          // the offset wraps 100% → 0% at constant velocity and the rounded
          // rect perimeter joins seamlessly.
          transition={{
            repeat: Infinity,
            repeatType: "loop",
            duration: theme.beamDuration,
            ease: "linear",
          }}
          className="sr-ihub__border-beam"
        />
      )}
      <div className="sr-ihub__core">{children}</div>
    </aside>
  );
}

export function IntelligenceDivider() {
  return <div className="sr-ihub__divider" aria-hidden="true" />;
}
