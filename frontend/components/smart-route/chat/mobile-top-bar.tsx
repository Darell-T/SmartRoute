"use client";

import Image from "next/image";
import { motion } from "motion/react";
import { Menu } from "lucide-react";

export function MobileTopBar({
  navigationOpen,
  onOpenNavigation,
  onNewTrip,
}: {
  navigationOpen: boolean;
  onOpenNavigation: () => void;
  onNewTrip: () => void;
}) {
  return (
    <header className="sr-mobile-top-bar">
      <motion.button
        id="sr-mobile-menu-trigger"
        type="button"
        className="sr-mobile-top-bar__control"
        aria-label="Open navigation menu"
        aria-controls="sr-mobile-navigation"
        aria-expanded={navigationOpen}
        onClick={onOpenNavigation}
        whileTap={{ scale: 0.94 }}
      >
        <Menu size={22} strokeWidth={1.8} aria-hidden="true" />
      </motion.button>

      <button
        type="button"
        className="sr-mobile-top-bar__brand"
        aria-label="Start a new SmartRoute trip"
        onClick={onNewTrip}
      >
        <Image
          src="/smart-route-mark-512.png"
          alt=""
          width={30}
          height={30}
          priority
          aria-hidden="true"
        />
        <span>SmartRoute</span>
      </button>
    </header>
  );
}
