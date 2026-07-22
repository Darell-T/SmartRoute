"use client";

import { useState, useSyncExternalStore } from "react";
import { NavArrowRight } from "iconoir-react";
import { motion, useReducedMotion } from "motion/react";
import Typewriter from "typewriter-effect";

const WELCOME_VARIANTS = [
  {
    title: "Where to?",
    subtitle: "Ask about any trip in New York.",
  },
  {
    title: "What’s the plan?",
    subtitle: "Tell SmartRoute where you’re going and what matters most.",
  },
  {
    title: "Ready to go?",
    subtitle: "Plan around time, crowds, walking, or accessibility.",
  },
  {
    title: "Where next?",
    subtitle: "Find a route shaped around your timing and preferences.",
  },
  {
    title: "Need a route?",
    subtitle: "Plan with live service conditions across New York.",
  },
  {
    title: "Let’s get moving.",
    subtitle: "Start with a destination, deadline, or travel preference.",
  },
] as const;

const LAST_WELCOME_KEY = "sr-last-welcome";
let cachedWelcomeIndex: number | null = null;

const suggestionGroupVariants = {
  hidden: {},
  visible: {
    transition: {
      delayChildren: 0.08,
      staggerChildren: 0.085,
    },
  },
};

const suggestionVariants = {
  hidden: { opacity: 0, y: 12, scale: 0.985 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: {
      duration: 0.42,
      ease: [0.16, 1, 0.3, 1] as const,
    },
  },
};

function randomInteger(max: number): number {
  try {
    const value = new Uint32Array(1);
    window.crypto.getRandomValues(value);
    return value[0] % max;
  } catch {
    return Math.floor(Math.random() * max);
  }
}

function selectWelcomeIndex(): number {
  let previous = -1;
  try {
    const stored = Number.parseInt(window.sessionStorage.getItem(LAST_WELCOME_KEY) ?? "", 10);
    if (Number.isInteger(stored) && stored >= 0 && stored < WELCOME_VARIANTS.length) {
      previous = stored;
    }
  } catch {
    // Storage can be unavailable in private or embedded browser contexts.
  }

  const next =
    previous < 0
      ? randomInteger(WELCOME_VARIANTS.length)
      : (previous + 1 + randomInteger(WELCOME_VARIANTS.length - 1)) % WELCOME_VARIANTS.length;

  try {
    window.sessionStorage.setItem(LAST_WELCOME_KEY, String(next));
  } catch {
    // The randomized welcome still works when session storage is unavailable.
  }
  return next;
}

function subscribeToWelcome(): () => void {
  return () => undefined;
}

function getWelcomeSnapshot(): number {
  cachedWelcomeIndex ??= selectWelcomeIndex();
  return cachedWelcomeIndex;
}

function getWelcomeServerSnapshot(): null {
  return null;
}

export function ChatWelcome({
  suggestions,
  onSelectSuggestion,
}: {
  suggestions: readonly string[];
  onSelectSuggestion: (query: string) => void;
}) {
  const reduceMotion = useReducedMotion();
  const welcomeIndex = useSyncExternalStore(
    subscribeToWelcome,
    getWelcomeSnapshot,
    getWelcomeServerSnapshot,
  );
  const [titleComplete, setTitleComplete] = useState(false);
  const [copyComplete, setCopyComplete] = useState(false);

  const welcome = welcomeIndex === null ? null : WELCOME_VARIANTS[welcomeIndex];
  const animationsDisabled = reduceMotion === true;
  const titleVisible = animationsDisabled || titleComplete;
  const cardsVisible = animationsDisabled || copyComplete;

  if (!welcome) {
    return <div className="sr-chat-empty sr-chat-empty--loading" aria-hidden="true" />;
  }

  return (
    <div className="sr-chat-empty">
      <span className="sr-only">
        {welcome.title} {welcome.subtitle}
      </span>

      <div
        className="sr-chat-welcome-line sr-chat-welcome-line--title"
        data-complete={titleVisible ? "true" : "false"}
        aria-hidden="true"
      >
        {animationsDisabled ? (
          welcome.title
        ) : (
          <Typewriter
            key={`title-${welcomeIndex}`}
            options={{ delay: 38, cursor: "|", skipAddStyles: true }}
            onInit={(typewriter) => {
              typewriter
                .pauseFor(140)
                .typeString(welcome.title)
                .callFunction(() => setTitleComplete(true))
                .start();
            }}
          />
        )}
      </div>

      <div
        className="sr-chat-welcome-line sr-chat-welcome-line--subtitle"
        data-complete={cardsVisible ? "true" : "false"}
        aria-hidden="true"
      >
        {animationsDisabled ? (
          welcome.subtitle
        ) : titleComplete ? (
          <Typewriter
            key={`subtitle-${welcomeIndex}`}
            options={{ delay: 13, cursor: "|", skipAddStyles: true }}
            onInit={(typewriter) => {
              typewriter
                .pauseFor(90)
                .typeString(welcome.subtitle)
                .pauseFor(90)
                .callFunction(() => setCopyComplete(true))
                .start();
            }}
          />
        ) : null}
      </div>

      <motion.div
        className="sr-chat-empty__suggestions"
        data-visible={cardsVisible ? "true" : "false"}
        variants={suggestionGroupVariants}
        initial={animationsDisabled ? false : "hidden"}
        animate={cardsVisible ? "visible" : "hidden"}
      >
        {suggestions.map((query) => (
          <motion.div key={query} className="sr-chat-suggestion-motion" variants={suggestionVariants}>
            <button
              type="button"
              className="sr-chat-suggestion-pill"
              onClick={() => onSelectSuggestion(query)}
            >
              <span>{query}</span>
              <NavArrowRight
                className="sr-chat-suggestion-pill__arrow"
                width={17}
                height={17}
                strokeWidth={1.7}
                aria-hidden="true"
              />
            </button>
          </motion.div>
        ))}
      </motion.div>
    </div>
  );
}
