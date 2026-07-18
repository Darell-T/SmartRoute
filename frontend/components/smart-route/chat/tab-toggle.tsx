"use client";

/* ════════════════════════════════════════════════════════════════════════
   SmartRoute — Chat | Live Map tab toggle

   The one control that must work identically no matter which tab is
   showing, so it lives at the tab-shell level (z-index 60, above both
   keep-mounted panels — see app/styles/smart-route-tab-shell.css) rather
   than inside either panel. Visually it docks into the chat top bar's
   reserved center slot (chat-top-bar.tsx) and floats over the live map on
   the other tab, both by sitting at the same fixed vertical offset.

   Segmented-control pattern lifted from the left rail's own tab switch
   (RailHeader in left-rail.tsx): a `motion.span layoutId` thumb slides
   between the two options rather than a hand-rolled transform animation.
   ════════════════════════════════════════════════════════════════════════ */

import { motion } from "motion/react";
import type { AppTab } from "@/app/page-parts";

const TABS: { id: AppTab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "livemap", label: "Live Map" },
];

export function TabToggle({
  activeTab,
  onChange,
}: {
  activeTab: AppTab;
  onChange: (tab: AppTab) => void;
}) {
  return (
    <div className="sr-tab-toggle" role="tablist" aria-label="SmartRoute view">
      {TABS.map((tabOption) => {
        const active = tabOption.id === activeTab;
        return (
          <button
            key={tabOption.id}
            type="button"
            role="tab"
            aria-selected={active}
            className="sr-tab-toggle__option"
            data-active={active ? "true" : "false"}
            onClick={() => onChange(tabOption.id)}
          >
            {active && (
              <motion.span
                layoutId="sr-tab-toggle-thumb"
                className="sr-tab-toggle__thumb"
                transition={{ duration: 0.24, ease: [0.32, 0.72, 0, 1] }}
                aria-hidden="true"
              />
            )}
            <span className="sr-tab-toggle__label">{tabOption.label}</span>
          </button>
        );
      })}
    </div>
  );
}
