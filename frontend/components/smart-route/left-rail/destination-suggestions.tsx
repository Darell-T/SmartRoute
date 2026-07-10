"use client";

import { ChevronRight, MapPin } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { MapboxSearchSuggestion } from "@/lib/mapbox-search";

type DestinationSuggestionsProps = {
  id: string;
  open: boolean;
  suggestions: MapboxSearchSuggestion[];
  highlightedIndex: number;
  onHighlight: (index: number) => void;
  onSelect: (suggestion: MapboxSearchSuggestion) => void;
};

export function DestinationSuggestions({
  id,
  open,
  suggestions,
  highlightedIndex,
  onHighlight,
  onSelect,
}: DestinationSuggestionsProps) {
  const reduceMotion = useReducedMotion();

  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          id={id}
          className="sr-search-popover"
          role="listbox"
          aria-label="Suggested destinations"
          initial={reduceMotion ? false : { opacity: 0, y: -4, scale: 0.99 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -3, scale: 0.99 }}
          transition={{ duration: reduceMotion ? 0.01 : 0.18, ease: [0.22, 1, 0.36, 1] }}
        >
          {suggestions.map((suggestion, index) => {
            const optionId = `${id}-option-${index}`;
            const primary = primaryLabel(suggestion);
            const secondary = secondaryLabel(suggestion, primary);

            return (
              <button
                id={optionId}
                key={suggestion.id}
                className="sr-search-option"
                type="button"
                role="option"
                aria-selected={index === highlightedIndex}
                tabIndex={-1}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => onHighlight(index)}
                onClick={() => onSelect(suggestion)}
              >
                <span className="sr-search-option__icon" aria-hidden="true">
                  <MapPin size={16} strokeWidth={1.9} />
                </span>
                <span className="sr-search-option__copy">
                  <strong>{primary}</strong>
                  {secondary && <small>{secondary}</small>}
                </span>
                <ChevronRight
                  className="sr-search-option__chevron"
                  size={15}
                  strokeWidth={1.8}
                  aria-hidden="true"
                />
              </button>
            );
          })}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function destinationSuggestionOptionId(
  listboxId: string,
  index: number,
): string {
  return `${listboxId}-option-${index}`;
}

function primaryLabel(suggestion: MapboxSearchSuggestion): string {
  return suggestion.label.split(",")[0]?.trim() || suggestion.label;
}

function secondaryLabel(
  suggestion: MapboxSearchSuggestion,
  primary: string,
): string | undefined {
  const source = suggestion.address?.trim() || suggestion.label.trim();
  const primaryLower = primary.toLowerCase();
  const sourceLower = source.toLowerCase();
  const withoutRepeatedName = sourceLower.startsWith(`${primaryLower},`)
    ? source.slice(primary.length + 1).trim()
    : source;

  return withoutRepeatedName.toLowerCase() === primaryLower
    ? undefined
    : withoutRepeatedName;
}
