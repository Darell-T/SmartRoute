"use client";

import { motion, useReducedMotion } from "motion/react";
import type {
  ForwardRefExoticComponent,
  HTMLAttributes,
  KeyboardEvent,
  RefAttributes,
} from "react";
import { useRef, useState } from "react";
import { PromptSuggestion } from "@/components/prompt-kit/prompt-suggestion";
import { AccessibilityIcon } from "@/components/ui/accessibility";
import { AirplaneIcon } from "@/components/ui/airplane";
import { WaypointsIcon } from "@/components/ui/waypoints";
import { HomeNearYou } from "./home-near-you";
import type { HomeNearbyModel } from "./near-you";

export type ChatSuggestion = {
  label: string;
  query: string;
  icon: "airplane" | "waypoints" | "accessibility";
};

interface AnimatedSuggestionIconHandle {
  startAnimation: () => void;
  stopAnimation: () => void;
}

type AnimatedSuggestionIcon = ForwardRefExoticComponent<
  HTMLAttributes<HTMLDivElement> &
    { size?: number } &
    RefAttributes<AnimatedSuggestionIconHandle>
>;

const SUGGESTION_ICONS: Record<
  ChatSuggestion["icon"],
  AnimatedSuggestionIcon
> = {
  airplane: AirplaneIcon,
  waypoints: WaypointsIcon,
  accessibility: AccessibilityIcon,
};

export function ChatWelcome({
  nearby,
  onOpenLiveMap,
}: {
  nearby: HomeNearbyModel;
  onOpenLiveMap: () => void;
}) {
  return (
    <div className="sr-chat-empty">
      <h2 className="sr-chat-welcome-line sr-chat-welcome-line--title">
        Where to?
      </h2>

      <div className="sr-chat-empty__nearby">
        <HomeNearYou model={nearby} onOpenLiveMap={onOpenLiveMap} />
      </div>
    </div>
  );
}

export function ChatSuggestions({
  suggestions,
  hidden = false,
  onSelectSuggestion,
}: {
  suggestions: readonly ChatSuggestion[];
  hidden?: boolean;
  onSelectSuggestion: (query: string) => void;
}) {
  const reduceMotion = useReducedMotion();
  const railRef = useRef<HTMLDivElement>(null);
  const [scrolledFromStart, setScrolledFromStart] = useState(false);

  function handleRailKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const current = (event.target as HTMLElement).closest("button");
    const buttons = Array.from(
      railRef.current?.querySelectorAll<HTMLButtonElement>("button") ?? [],
    );
    const currentIndex = current ? buttons.indexOf(current as HTMLButtonElement) : -1;
    if (currentIndex < 0) return;
    const offset = event.key === "ArrowRight" ? 1 : -1;
    const next = buttons[currentIndex + offset];
    if (!next) return;
    event.preventDefault();
    next.focus();
    next.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "nearest",
      inline: "nearest",
    });
  }

  return (
    <div
      ref={railRef}
      className="sr-chat-empty__suggestions"
      role="group"
      aria-label="Trip suggestions"
      aria-hidden={hidden || undefined}
      inert={hidden || undefined}
      data-hidden={hidden ? "true" : "false"}
      data-scrolled={scrolledFromStart ? "true" : "false"}
      onKeyDown={handleRailKeyDown}
      onScroll={(event) => {
        const isScrolled = event.currentTarget.scrollLeft > 4;
        setScrolledFromStart((current) =>
          current === isScrolled ? current : isScrolled,
        );
      }}
    >
      {suggestions.map((suggestion) => (
        <AnimatedSuggestion
          key={suggestion.query}
          suggestion={suggestion}
          reduceMotion={Boolean(reduceMotion)}
          onSelect={onSelectSuggestion}
        />
      ))}
    </div>
  );
}

function AnimatedSuggestion({
  suggestion,
  reduceMotion,
  onSelect,
}: {
  suggestion: ChatSuggestion;
  reduceMotion: boolean;
  onSelect: (query: string) => void;
}) {
  const iconRef = useRef<AnimatedSuggestionIconHandle>(null);
  const Icon = SUGGESTION_ICONS[suggestion.icon];

  return (
    <motion.div
      className="sr-chat-suggestion-motion"
      tabIndex={-1}
      whileHover={reduceMotion ? undefined : { y: -1 }}
      whileTap={reduceMotion ? undefined : { scale: 0.985 }}
      onHoverStart={() => {
        if (!reduceMotion) iconRef.current?.startAnimation();
      }}
      onHoverEnd={() => iconRef.current?.stopAnimation()}
      transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
    >
      <PromptSuggestion
        variant="outline"
        size="lg"
        className="sr-chat-suggestion-pill"
        aria-label={suggestion.query}
        onClick={() => onSelect(suggestion.query)}
      >
        <Icon
          ref={iconRef}
          className={`sr-chat-suggestion-icon sr-chat-suggestion-icon--${suggestion.icon}`}
          size={18}
          aria-hidden="true"
        />
        <span>{suggestion.label}</span>
      </PromptSuggestion>
    </motion.div>
  );
}
