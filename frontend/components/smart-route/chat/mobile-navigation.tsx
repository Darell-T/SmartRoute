"use client";

import Image from "next/image";
import { AnimatePresence, motion } from "motion/react";
import {
  MessageCircle,
  Moon,
  SquarePen,
  Sun,
  type LucideIcon,
} from "lucide-react";
import { Map as MapIcon } from "iconoir-react";
import { useEffect, useRef, type ComponentType, type SVGProps } from "react";
import type { AppTab } from "@/app/page-parts";
import type { ChatTheme } from "@/lib/use-chat-theme";

type NavigationIcon = LucideIcon | ComponentType<SVGProps<SVGSVGElement>>;

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function getFocusableElements(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => !element.hidden,
  );
}

function NavigationItem({
  label,
  description,
  icon: Icon,
  active = false,
  onClick,
}: {
  label: string;
  description?: string;
  icon: NavigationIcon;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <motion.button
      type="button"
      className="sr-mobile-navigation__item"
      data-active={active ? "true" : "false"}
      aria-current={active ? "page" : undefined}
      onClick={onClick}
      whileTap={{ scale: 0.985 }}
    >
      <span className="sr-mobile-navigation__item-icon" aria-hidden="true">
        <Icon width={22} height={22} strokeWidth={1.85} />
      </span>
      <span className="sr-mobile-navigation__item-copy">
        <span className="sr-mobile-navigation__item-label">{label}</span>
        {description ? (
          <span className="sr-mobile-navigation__item-description">
            {description}
          </span>
        ) : null}
      </span>
    </motion.button>
  );
}

export function MobileNavigation({
  open,
  activeTab,
  theme,
  onClose,
  onOpenChat,
  onOpenLiveMap,
  onNewTrip,
  onToggleTheme,
}: {
  open: boolean;
  activeTab: AppTab;
  theme: ChatTheme;
  onClose: () => void;
  onOpenChat: () => void;
  onOpenLiveMap: () => void;
  onNewTrip: () => void;
  onToggleTheme: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const newTripButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;

    const focusFrame = window.requestAnimationFrame(() => {
      newTripButtonRef.current?.focus();
    });
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;

      const focusableElements = getFocusableElements(dialogRef.current);
      const firstElement = focusableElements[0];
      const lastElement = focusableElements.at(-1);
      if (!firstElement || !lastElement) {
        event.preventDefault();
        return;
      }

      const activeElement = document.activeElement;
      const focusEscaped = !dialogRef.current.contains(activeElement);
      if (event.shiftKey && (activeElement === firstElement || focusEscaped)) {
        event.preventDefault();
        lastElement.focus();
      } else if (!event.shiftKey && (activeElement === lastElement || focusEscaped)) {
        event.preventDefault();
        firstElement.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, open]);

  function choose(action: () => void) {
    action();
    onClose();
  }

  return (
    <AnimatePresence initial={false}>
      {open ? (
        <motion.aside
          ref={dialogRef}
          id="sr-mobile-navigation"
          className="sr-mobile-navigation"
          data-theme={theme}
          role="dialog"
          aria-modal="true"
          aria-label="SmartRoute navigation"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
        >
          <div className="sr-mobile-navigation__header">
            <button
              type="button"
              className="sr-mobile-navigation__brand"
              onClick={() => choose(onNewTrip)}
            >
              <Image
                src="/smart-route-mark-512.png"
                alt=""
                width={38}
                height={38}
                priority
                aria-hidden="true"
              />
              <span>SmartRoute</span>
            </button>
            <button
              type="button"
              className="sr-mobile-navigation__close"
              aria-label="Close navigation"
              onClick={onClose}
            >
              Close
            </button>
          </div>

          <nav className="sr-mobile-navigation__content" aria-label="Primary">
            <motion.button
              ref={newTripButtonRef}
              type="button"
              className="sr-mobile-navigation__new-trip"
              onClick={() => choose(onNewTrip)}
              whileTap={{ scale: 0.985 }}
            >
              <SquarePen size={22} strokeWidth={1.85} aria-hidden="true" />
              <span>New Trip</span>
            </motion.button>

            <div className="sr-mobile-navigation__rule" aria-hidden="true" />

            <NavigationItem
              label="Chat"
              description="Plan and explore trips"
              icon={MessageCircle}
              active={activeTab === "chat"}
              onClick={() => choose(onOpenChat)}
            />
            <NavigationItem
              label="Transit Map"
              description="Live feed · Alerts · Routes"
              icon={MapIcon}
              active={activeTab === "livemap"}
              onClick={() => choose(onOpenLiveMap)}
            />
            <div className="sr-mobile-navigation__rule" aria-hidden="true" />

            <motion.button
              type="button"
              className="sr-mobile-navigation__theme"
              aria-label={
                theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
              }
              aria-pressed={theme === "light"}
              onClick={onToggleTheme}
              whileTap={{ scale: 0.985 }}
            >
              {theme === "dark" ? (
                <Sun size={22} strokeWidth={1.85} aria-hidden="true" />
              ) : (
                <Moon size={22} strokeWidth={1.85} aria-hidden="true" />
              )}
              <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>
              <span className="sr-mobile-navigation__switch" aria-hidden="true">
                <span />
              </span>
            </motion.button>
          </nav>
        </motion.aside>
      ) : null}
    </AnimatePresence>
  );
}
