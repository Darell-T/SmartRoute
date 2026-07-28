"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ArrowUp, Mic, X } from "lucide-react";
import { motion } from "motion/react";
import type { MapboxSearchSuggestion } from "@/lib/mapbox-search";
import { useDestinationSearch } from "@/lib/use-destination-search";
import {
  DestinationSuggestions,
  destinationSuggestionOptionId,
} from "./destination-suggestions";
import type { RailSearchProps } from "./left-rail";

function cleanDestinationDraft(value: string) {
  return value
    .replace(/\s+/g, " ")
    .replace(/\s+,/g, ",")
    .replace(/,{2,}/g, ",")
    .trimStart();
}

export function cleanDestinationSubmit(value: string) {
  return cleanDestinationDraft(value).trim();
}

type DestinationInputActionState =
  | "empty"
  | "submit"
  | "stop"
  | "finalizing"
  | "clear";

type SpeechRecognitionAlternativeLike = {
  transcript: string;
};

type SpeechRecognitionResultLike = {
  readonly length: number;
  readonly isFinal: boolean;
  item(index: number): SpeechRecognitionAlternativeLike;
  [index: number]: SpeechRecognitionAlternativeLike;
};

type SpeechRecognitionResultListLike = {
  readonly length: number;
  item(index: number): SpeechRecognitionResultLike;
  [index: number]: SpeechRecognitionResultLike;
};

type SpeechRecognitionEventLike = Event & {
  results: SpeechRecognitionResultListLike;
};

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
};

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

type SpeechRecognitionWindow = Window &
  typeof globalThis & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };

function getSpeechRecognitionConstructor(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const speechWindow = window as SpeechRecognitionWindow;
  return (
    speechWindow.SpeechRecognition ??
    speechWindow.webkitSpeechRecognition ??
    null
  );
}

export function DestinationInput({
  search,
  onDemoSubmit,
  onFocusChange,
}: {
  search?: RailSearchProps;
  onDemoSubmit: (query: string) => void;
  onFocusChange?: (focused: boolean) => void;
}) {
  const [localValue, setLocalValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [speechRecognitionCtor, setSpeechRecognitionCtor] =
    useState<SpeechRecognitionConstructor | null>(null);
  const [isListening, setIsListening] = useState(false);
  const speechRecognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const suggestionsId = useId();
  const controlledSearch = search ?? null;
  const wired = controlledSearch !== null;
  const value = controlledSearch ? controlledSearch.inputValue : localValue;
  const displayValue = cleanDestinationDraft(value);

  const destinationSearch = useDestinationSearch({
    inputValue: controlledSearch ? controlledSearch.inputValue : "",
    enabled: wired && focused,
    isLoading: search?.isLoading ?? false,
  });
  const {
    suggestions,
    highlightedIndex,
    setHighlightedIndex,
    choose,
    isResolving,
    clearSuggestions,
    markInputEdited,
    markSelectedLabel,
    resetSession,
  } = destinationSearch;

  useEffect(() => {
    const supportCheck = window.setTimeout(() => {
      const recognitionCtor = getSpeechRecognitionConstructor();
      setSpeechRecognitionCtor(() => recognitionCtor);
    }, 0);
    return () => {
      window.clearTimeout(supportCheck);
      speechRecognitionRef.current?.abort();
      speechRecognitionRef.current = null;
    };
  }, []);

  function setValue(next: string) {
    const cleaned = cleanDestinationDraft(next);
    if (controlledSearch) {
      markInputEdited();
      controlledSearch.onInputChange(cleaned);
      return;
    }
    setLocalValue(cleaned);
  }

  async function chooseSuggestion(suggestion: MapboxSearchSuggestion) {
    const selection = await choose(suggestion);
    const label = cleanDestinationSubmit(selection?.label ?? suggestion.label);
    search?.onInputChange(label);
    clearSuggestions();
    resetSession();
    setFocused(false);
    onFocusChange?.(false);
    search?.onSubmit(label, selection ?? null);
  }

  function submitSearch() {
    const query = cleanDestinationSubmit(value);
    if (!query) return;
    clearSuggestions();
    resetSession();
    setFocused(false);
    onFocusChange?.(false);
    markSelectedLabel(query);
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    if (controlledSearch) controlledSearch.onSubmit(query, null);
    else onDemoSubmit(query);
  }

  function clearSearch() {
    clearSuggestions();
    resetSession();
    if (controlledSearch) {
      controlledSearch.onClear();
      return;
    }
    setLocalValue("");
  }

  function stopRoutePlanning() {
    clearSuggestions();
    resetSession();
    setFocused(false);
    onFocusChange?.(false);
    controlledSearch?.onCancelPlanning();
  }

  function startVoiceInput() {
    if (!speechRecognitionCtor || isListening) {
      speechRecognitionRef.current?.stop();
      return;
    }

    const recognition = new speechRecognitionCtor();
    recognition.lang = "en-US";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      const transcriptParts: string[] = [];
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index] ?? event.results.item(index);
        const alternative = result[0] ?? result.item(0);
        if (alternative?.transcript) {
          transcriptParts.push(alternative.transcript);
        }
      }
      const transcript = cleanDestinationDraft(transcriptParts.join(" "));
      if (!transcript) return;
      setValue(transcript);
      setFocused(true);
      onFocusChange?.(true);
    };
    recognition.onerror = () => {
      setIsListening(false);
      speechRecognitionRef.current = null;
    };
    recognition.onend = () => {
      setIsListening(false);
      speechRecognitionRef.current = null;
    };

    speechRecognitionRef.current = recognition;
    setIsListening(true);
    try {
      recognition.start();
    } catch {
      setIsListening(false);
      speechRecognitionRef.current = null;
    }
  }

  const planningPhase = search?.planningPhase ?? "idle";
  const isPlanning = planningPhase !== "idle" || Boolean(search?.isLoading);
  const busy = Boolean(isPlanning || isResolving);
  const hasSearchContent = cleanDestinationSubmit(value).length > 0;
  const showClearAction = Boolean(controlledSearch?.hasActiveRoute && !busy);
  const actionState: DestinationInputActionState = showClearAction
    ? "clear"
    : planningPhase === "cancellable"
      ? "stop"
      : planningPhase === "finalizing" || isResolving || search?.isLoading
        ? "finalizing"
        : hasSearchContent
          ? "submit"
          : "empty";
  const canUseVoice =
    speechRecognitionCtor !== null &&
    !busy &&
    !showClearAction &&
    actionState !== "clear";
  const actionDisabled =
    actionState === "empty" || actionState === "finalizing";
  const actionLabel =
    actionState === "clear"
      ? "Clear route"
      : actionState === "stop"
        ? "Stop route planning"
        : actionState === "finalizing"
          ? "Finalizing route"
          : "Search route";
  const actionFilled =
    actionState === "submit" ||
    actionState === "stop" ||
    actionState === "clear";
  const suggestionsOpen = wired && focused && suggestions.length > 0;

  return (
    <section className="sr-rail-section sr-route-search">
      <form
        className="sr-input-group"
        onSubmit={(event) => {
          event.preventDefault();
          if (actionState === "submit") submitSearch();
        }}
      >
        <input
          aria-label="Search destination or address"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={suggestionsOpen}
          aria-controls={suggestionsOpen ? suggestionsId : undefined}
          aria-activedescendant={
            suggestionsOpen
              ? destinationSuggestionOptionId(suggestionsId, highlightedIndex)
              : undefined
          }
          value={displayValue}
          onChange={(event) => setValue(event.target.value)}
          onFocus={() => {
            setFocused(true);
            onFocusChange?.(true);
          }}
          onBlur={() =>
            window.setTimeout(() => {
              setFocused(false);
              onFocusChange?.(false);
            }, 140)
          }
          onKeyDown={(event) => {
            if (!wired || suggestions.length === 0) return;
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setHighlightedIndex((highlightedIndex + 1) % suggestions.length);
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setHighlightedIndex(
                highlightedIndex === 0
                  ? suggestions.length - 1
                  : highlightedIndex - 1,
              );
            } else if (event.key === "Enter" && suggestions[highlightedIndex]) {
              event.preventDefault();
              void chooseSuggestion(suggestions[highlightedIndex]);
            } else if (event.key === "Escape") {
              clearSuggestions();
            }
          }}
          placeholder="Where are we headed?"
          autoComplete="off"
          disabled={busy}
          title={displayValue || undefined}
        />
        {canUseVoice && (
          <button
            type="button"
            className="sr-input-voice"
            aria-label={
              isListening ? "Listening for destination" : "Use voice input"
            }
            data-listening={isListening ? "true" : "false"}
            onClick={startVoiceInput}
          >
            <Mic size={20} strokeWidth={1.9} aria-hidden="true" />
          </button>
        )}
        <motion.button
          type={actionState === "submit" ? "submit" : "button"}
          className="sr-input-submit"
          aria-label={actionLabel}
          disabled={actionDisabled}
          data-filled={actionFilled ? "true" : "false"}
          data-action-state={actionState}
          onClick={() => {
            if (actionState === "clear") {
              clearSearch();
            } else if (actionState === "stop") {
              stopRoutePlanning();
            }
          }}
          animate={{
            backgroundColor: actionFilled
              ? "rgba(255,255,255,0.96)"
              : "rgba(255,255,255,0.12)",
            color: actionFilled
              ? "rgba(8,12,18,0.96)"
              : "rgba(255,255,255,0.72)",
          }}
          transition={{ duration: 0.2, ease: "easeOut" }}
          whileTap={!actionDisabled ? { scale: 0.96 } : undefined}
        >
          {actionState === "clear" ? (
            <X size={20} strokeWidth={2.1} aria-hidden="true" />
          ) : actionState === "stop" || actionState === "finalizing" ? (
            <span className="sr-input-stop-icon" aria-hidden="true" />
          ) : (
            <ArrowUp size={21} strokeWidth={2.25} aria-hidden="true" />
          )}
        </motion.button>
      </form>

      <DestinationSuggestions
        id={suggestionsId}
        open={suggestionsOpen}
        suggestions={suggestions}
        highlightedIndex={highlightedIndex}
        onHighlight={setHighlightedIndex}
        onSelect={(suggestion) => void chooseSuggestion(suggestion)}
      />
    </section>
  );
}
