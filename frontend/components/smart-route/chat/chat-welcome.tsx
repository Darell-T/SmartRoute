"use client";

import { useState, useSyncExternalStore } from "react";
import { motion, useReducedMotion } from "motion/react";
import Typewriter from "typewriter-effect";
import { PromptSuggestion } from "@/components/prompt-kit/prompt-suggestion";

const WELCOME = {
  title: "Where to?",
  subtitle: "Ask about any trip in New York.",
} as const;

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

const subscribeToHydration = () => () => undefined;

function useHydrated(): boolean {
  return useSyncExternalStore(subscribeToHydration, () => true, () => false);
}

export function ChatWelcome({
  suggestions,
  onSelectSuggestion,
}: {
  suggestions: readonly string[];
  onSelectSuggestion: (query: string) => void;
}) {
  const reduceMotion = useReducedMotion();
  const hydrated = useHydrated();
  const [titleComplete, setTitleComplete] = useState(false);
  const [copyComplete, setCopyComplete] = useState(false);

  // `useReducedMotion` can resolve differently during SSR and the first
  // browser render. Gate its visual branch until hydration completes so the
  // welcome screen preserves React's event handlers for the composer.
  const animationsDisabled = hydrated && reduceMotion === true;
  const titleVisible = animationsDisabled || titleComplete;
  const cardsVisible = animationsDisabled || copyComplete;

  return (
    <div className="sr-chat-empty">
      <span className="sr-only">
        {WELCOME.title} {WELCOME.subtitle}
      </span>

      <h2
        className="sr-chat-welcome-line sr-chat-welcome-line--title"
        data-complete={titleVisible ? "true" : "false"}
        aria-hidden="true"
      >
        {animationsDisabled ? (
          WELCOME.title
        ) : (
          <Typewriter
            options={{ delay: 38, cursor: "|", skipAddStyles: true }}
            onInit={(typewriter) => {
              typewriter
                .pauseFor(140)
                .typeString(WELCOME.title)
                .callFunction(() => setTitleComplete(true))
                .start();
            }}
          />
        )}
      </h2>

      <div
        className="sr-chat-welcome-line sr-chat-welcome-line--subtitle"
        data-complete={cardsVisible ? "true" : "false"}
        aria-hidden="true"
      >
        {animationsDisabled ? (
          WELCOME.subtitle
        ) : titleComplete ? (
          <Typewriter
            options={{ delay: 13, cursor: "|", skipAddStyles: true }}
            onInit={(typewriter) => {
              typewriter
                .pauseFor(90)
                .typeString(WELCOME.subtitle)
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
            <PromptSuggestion
              variant="outline"
              size="lg"
              className="sr-chat-suggestion-pill"
              onClick={() => onSelectSuggestion(query)}
            >
              {query}
            </PromptSuggestion>
          </motion.div>
        ))}
      </motion.div>
    </div>
  );
}
