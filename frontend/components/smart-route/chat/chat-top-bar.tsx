"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute chat — top bar

   One line, ≤64px: brand mark + wordmark left (wordmark hides below 400px
   via CSS, mark alone still reads as the brand), a spacer reserved for the
   shell-level `TabToggle` (which floats above both panels, see
   tab-toggle.tsx), and the Near You row + theme toggle on the right. The
   brand tagline deliberately does not live here (copy rules: it reads as
   filler in a chrome bar; kept, if at all, to the empty-state subtitle).
   ════════════════════════════════════════════════════════════════════════ */

import { Moon, Sun } from "lucide-react";
import type { ChatTheme } from "@/lib/use-chat-theme";
import { NearYouRow } from "./near-you-row";

export function ChatTopBar({
  nearbyRouteIds,
  onSelectNearbyRoute,
  onOpenLiveMap,
  theme,
  onToggleTheme,
}: {
  nearbyRouteIds: string[];
  onSelectNearbyRoute: (routeId: string) => void;
  onOpenLiveMap: () => void;
  theme: ChatTheme;
  onToggleTheme: () => void;
}) {
  const nearYou = (
    <NearYouRow
      routeIds={nearbyRouteIds}
      onSelectRoute={onSelectNearbyRoute}
      onOpenLiveMap={onOpenLiveMap}
    />
  );

  return (
    <header className="sr-chat-top-bar">
      <div className="sr-chat-top-bar__brand">
        <img src="/smart-route-mark-512.png" width={28} height={28} alt="" />
        <span className="sr-chat-top-bar__wordmark">SmartRoute</span>
      </div>

      <div className="sr-chat-top-bar__toggle-slot" aria-hidden="true" />

      <div className="sr-chat-top-bar__right">
        {/* Inline Near You: wide screens only. On phones the floating
            toggle's center reservation leaves this column too narrow for a
            bullet row, so the bar grows a second row instead (below) —
            same container, same border, just taller. */}
        <div className="sr-chat-near-you-slot--bar">{nearYou}</div>
        <button
          type="button"
          className="sr-chat-theme-toggle"
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          onClick={onToggleTheme}
        >
          {theme === "dark" ? (
            <Sun size={16} strokeWidth={1.8} aria-hidden="true" />
          ) : (
            <Moon size={16} strokeWidth={1.8} aria-hidden="true" />
          )}
        </button>
      </div>

      <div className="sr-chat-top-bar__row2">{nearYou}</div>
    </header>
  );
}
