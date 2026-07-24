"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";
import { PromptInputAction } from "@/components/prompt-kit/prompt-input";
import { Button } from "@/components/ui/button";
import type { ResponsePresentationMode } from "@/lib/response-presentation";
import type { ChatTheme } from "@/lib/use-chat-theme";

const RESPONSE_MODES: Array<{
  value: ResponsePresentationMode;
  label: string;
  title: string;
  description: string;
  tooltip: string;
}> = [
  {
    value: "auto",
    label: "Auto",
    title: "Best for most trips",
    description:
      "SmartRoute adjusts the amount of explanation to your trip, transfers, and current conditions.",
    tooltip:
      "SmartRoute adjusts its explanation based on your trip, transfers, and current conditions.",
  },
  {
    value: "quick",
    label: "Quick",
    title: "Shorter response",
    description:
      "Uses the same route analysis and returns the essential directions in a shorter answer.",
    tooltip:
      "Uses the same route analysis and returns a shorter answer with the essential directions.",
  },
];

const MENU_WIDTH = 256;
const VIEWPORT_GUTTER = 8;

export function ResponseModeMenu({
  value,
  theme,
  onValueChange,
}: {
  value: ResponsePresentationMode;
  theme: ChatTheme;
  onValueChange: (mode: ResponsePresentationMode) => void;
}) {
  const [open, setOpen] = useState(false);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const currentMode =
    RESPONSE_MODES.find((mode) => mode.value === value) ?? RESPONSE_MODES[0];

  const positionMenu = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const maxLeft = Math.max(
      VIEWPORT_GUTTER,
      window.innerWidth - MENU_WIDTH - VIEWPORT_GUTTER,
    );
    setMenuStyle({
      left: Math.min(Math.max(VIEWPORT_GUTTER, rect.left), maxLeft),
      bottom: window.innerHeight - rect.top + 8,
      width: MENU_WIDTH,
    });
  }, []);

  const focusOption = useCallback((index: number) => {
    const normalized =
      (index + RESPONSE_MODES.length) % RESPONSE_MODES.length;
    optionRefs.current[normalized]?.focus();
  }, []);

  const closeMenu = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) {
      window.requestAnimationFrame(() => triggerRef.current?.focus());
    }
  }, []);

  function openMenu(direction: "selected" | "first" | "last" = "selected") {
    positionMenu();
    setOpen(true);
    window.requestAnimationFrame(() => {
      if (direction === "first") focusOption(0);
      else if (direction === "last") focusOption(RESPONSE_MODES.length - 1);
      else {
        focusOption(
          Math.max(
            0,
            RESPONSE_MODES.findIndex((mode) => mode.value === value),
          ),
        );
      }
    });
  }

  useEffect(() => {
    if (!open) return;
    positionMenu();
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (
        !menuRef.current?.contains(target) &&
        !triggerRef.current?.contains(target)
      ) {
        closeMenu(false);
      }
    };
    const handleViewportChange = () => positionMenu();
    document.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("scroll", handleViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("scroll", handleViewportChange, true);
    };
  }, [closeMenu, open, positionMenu]);

  function handleTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(event.key === "ArrowDown" ? "first" : "last");
    }
  }

  function handleOptionKeyDown(
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusOption(index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusOption(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusOption(0);
    } else if (event.key === "End") {
      event.preventDefault();
      focusOption(RESPONSE_MODES.length - 1);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeMenu();
    }
  }

  const menu = open
    ? createPortal(
        <div
          ref={menuRef}
          className="sr-response-mode-menu"
          data-theme={theme}
          role="menu"
          aria-label="Route analysis"
          style={menuStyle}
        >
          <p className="sr-response-mode-menu__eyebrow">Response style</p>
          <div className="sr-response-mode-menu__options">
            {RESPONSE_MODES.map((mode, index) => {
              const selected = mode.value === value;
              return (
                <button
                  key={mode.value}
                  ref={(node) => {
                    optionRefs.current[index] = node;
                  }}
                  type="button"
                  role="menuitemradio"
                  aria-checked={selected}
                  className="sr-response-mode-menu__option"
                  data-selected={selected ? "true" : "false"}
                  onKeyDown={(event) => handleOptionKeyDown(event, index)}
                  onClick={() => {
                    onValueChange(mode.value);
                    closeMenu();
                  }}
                >
                  <span className="sr-response-mode-menu__check" aria-hidden="true">
                    {selected ? <Check size={14} strokeWidth={2.2} /> : null}
                  </span>
                  <span className="sr-response-mode-menu__copy">
                    <span className="sr-response-mode-menu__label">{mode.label}</span>
                    <span className="sr-response-mode-menu__title">{mode.title}</span>
                    <span className="sr-response-mode-menu__description">
                      {mode.description}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
          <p className="sr-response-mode-menu__note">
            Quick changes response length, not route choice or travel time.
          </p>
        </div>,
        document.body,
      )
    : null;

  return (
    <>
      <PromptInputAction
        tooltip={
          <span className="sr-response-mode-tooltip">
            <strong>{currentMode.label} mode</strong>
            <span>{currentMode.tooltip}</span>
          </span>
        }
      >
        <Button
          ref={triggerRef}
          type="button"
          variant="ghost"
          className="sr-chat-composer__mode-trigger"
          aria-label={`Response style: ${currentMode.label}`}
          aria-haspopup="menu"
          aria-expanded={open}
          data-state={open ? "open" : "closed"}
          onKeyDown={handleTriggerKeyDown}
          onClick={() => {
            if (open) closeMenu();
            else openMenu();
          }}
        >
          <span>{currentMode.label}</span>
          <ChevronDown size={12} strokeWidth={2} aria-hidden="true" />
        </Button>
      </PromptInputAction>
      {menu}
    </>
  );
}
