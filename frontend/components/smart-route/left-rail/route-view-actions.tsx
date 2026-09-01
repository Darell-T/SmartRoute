"use client";

import { useId, useState } from "react";
import { ArrowUp, Mic, X } from "lucide-react";
import { motion } from "motion/react";
import type { MapboxSearchSuggestion } from "@/lib/mapbox-search";
import { useDestinationSearch } from "@/lib/use-destination-search";
import { useVoiceInput } from "@/lib/use-voice-input";
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

const ACTION_LABELS: Record<DestinationInputActionState, string> = {
  empty: "Search route",
  submit: "Search route",
  stop: "Stop route planning",
  finalizing: "Finalizing route",
  clear: "Clear route",
};

export type DestinationComboboxCommand =
  | { type: "none" }
  | { type: "highlight"; index: number }
  | { type: "choose"; index: number }
  | { type: "escape" };

export function destinationComboboxCommand(
  key: string,
  suggestionCount: number,
  highlightedIndex: number,
): DestinationComboboxCommand {
  if (suggestionCount === 0) return { type: "none" };
  if (key === "ArrowDown") {
    return { type: "highlight", index: (highlightedIndex + 1) % suggestionCount };
  }
  if (key === "ArrowUp") {
    return {
      type: "highlight",
      index: highlightedIndex === 0 ? suggestionCount - 1 : highlightedIndex - 1,
    };
  }
  if (key === "Enter") return { type: "choose", index: highlightedIndex };
  if (key === "Escape") return { type: "escape" };
  return { type: "none" };
}

function destinationActionState(input: {
  showClearAction: boolean;
  planningPhase: string;
  isResolving: boolean;
  isLoading: boolean | undefined;
  hasSearchContent: boolean;
}): DestinationInputActionState {
  if (input.showClearAction) return "clear";
  if (input.planningPhase === "cancellable") return "stop";
  if (input.planningPhase === "finalizing" || input.isResolving || input.isLoading) {
    return "finalizing";
  }
  if (input.hasSearchContent) return "submit";
  return "empty";
}

function DestinationActionGlyph({
  actionState,
}: {
  actionState: DestinationInputActionState;
}) {
  if (actionState === "clear") {
    return <X size={20} strokeWidth={2.1} aria-hidden="true" />;
  }
  if (actionState === "stop" || actionState === "finalizing") {
    return <span className="sr-input-stop-icon" aria-hidden="true" />;
  }
  return <ArrowUp size={21} strokeWidth={2.25} aria-hidden="true" />;
}

function destinationSubmitColors(filled: boolean) {
  if (filled) {
    return {
      backgroundColor: "rgba(255,255,255,0.96)",
      color: "rgba(8,12,18,0.96)",
    };
  }
  return {
    backgroundColor: "rgba(255,255,255,0.12)",
    color: "rgba(255,255,255,0.72)",
  };
}

function DestinationVoiceButton({
  enabled,
  listening,
  onStart,
}: {
  enabled: boolean;
  listening: boolean;
  onStart: () => void;
}) {
  if (!enabled) return null;
  return (
    <button
      type="button"
      className="sr-input-voice"
      aria-label={listening ? "Listening for destination" : "Use voice input"}
      data-listening={listening ? "true" : "false"}
      onClick={onStart}
    >
      <Mic size={20} strokeWidth={1.9} aria-hidden="true" />
    </button>
  );
}

function DestinationSubmitControl({
  actionState,
  actionLabel,
  actionFilled,
  actionDisabled,
  onClear,
  onStop,
}: {
  actionState: DestinationInputActionState;
  actionLabel: string;
  actionFilled: boolean;
  actionDisabled: boolean;
  onClear: () => void;
  onStop: () => void;
}) {
  return (
    <motion.button
      type={actionState === "submit" ? "submit" : "button"}
      className="sr-input-submit"
      aria-label={actionLabel}
      disabled={actionDisabled}
      data-filled={actionFilled ? "true" : "false"}
      data-action-state={actionState}
      onClick={() => {
        if (actionState === "clear") onClear();
        else if (actionState === "stop") onStop();
      }}
      animate={destinationSubmitColors(actionFilled)}
      transition={{ duration: 0.2, ease: "easeOut" }}
      whileTap={actionDisabled ? undefined : { scale: 0.96 }}
    >
      <DestinationActionGlyph actionState={actionState} />
    </motion.button>
  );
}

function DestinationComboboxField({
  suggestionsId,
  suggestionsOpen,
  highlightedIndex,
  displayValue,
  busy,
  wired,
  suggestions,
  onChange,
  onFocus,
  onBlur,
  onHighlight,
  onChoose,
  onEscape,
}: {
  suggestionsId: string;
  suggestionsOpen: boolean;
  highlightedIndex: number;
  displayValue: string;
  busy: boolean;
  wired: boolean;
  suggestions: MapboxSearchSuggestion[];
  onChange: (value: string) => void;
  onFocus: () => void;
  onBlur: () => void;
  onHighlight: (index: number) => void;
  onChoose: (suggestion: MapboxSearchSuggestion) => void;
  onEscape: () => void;
}) {
  return (
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
      onChange={(event) => onChange(event.target.value)}
      onFocus={onFocus}
      onBlur={onBlur}
      onKeyDown={(event) => {
        if (!wired) return;
        const command = destinationComboboxCommand(
          event.key,
          suggestions.length,
          highlightedIndex,
        );
        if (command.type === "highlight") {
          event.preventDefault();
          onHighlight(command.index);
          return;
        }
        if (command.type === "choose") {
          const suggestion = suggestions[command.index];
          if (!suggestion) return;
          event.preventDefault();
          onChoose(suggestion);
          return;
        }
        if (command.type === "escape") {
          event.preventDefault();
          onEscape();
        }
      }}
      placeholder="Where are we headed?"
      autoComplete="off"
      disabled={busy}
      title={displayValue || undefined}
    />
  );
}

function destinationPresentation(input: {
  actionState: DestinationInputActionState;
  busy: boolean;
  voiceSupported: boolean;
  showClearAction: boolean;
  wired: boolean;
  focused: boolean;
  suggestionCount: number;
}) {
  const idle = input.actionState === "empty" || input.actionState === "finalizing";
  return {
    canUseVoice: input.voiceSupported && !input.busy && !input.showClearAction,
    actionDisabled: idle,
    actionFilled: !idle,
    suggestionsOpen: input.wired && input.focused && input.suggestionCount > 0,
  };
}

function destinationFieldModel(input: {
  search?: RailSearchProps;
  controlledSearch: RailSearchProps | null;
  isResolving: boolean;
  focused: boolean;
  suggestionCount: number;
  value: string;
  voiceSupported: boolean;
}) {
  const planningPhase = input.search?.planningPhase ?? "idle";
  const loading = Boolean(input.search?.isLoading);
  const busy = planningPhase !== "idle" || loading || input.isResolving;
  const showClearAction = Boolean(input.controlledSearch?.hasActiveRoute) && !busy;
  const actionState = destinationActionState({
    showClearAction,
    planningPhase,
    isResolving: input.isResolving,
    isLoading: loading,
    hasSearchContent: cleanDestinationSubmit(input.value).length > 0,
  });
  return {
    busy,
    actionState,
    actionLabel: ACTION_LABELS[actionState],
    ...destinationPresentation({
      actionState,
      busy,
      voiceSupported: input.voiceSupported,
      showClearAction,
      wired: Boolean(input.controlledSearch),
      focused: input.focused,
      suggestionCount: input.suggestionCount,
    }),
  };
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

  const voice = useVoiceInput((transcript) => {
    const cleaned = cleanDestinationDraft(transcript);
    if (!cleaned) return;
    setValue(cleaned);
    setFocused(true);
    onFocusChange?.(true);
  });

  const field = destinationFieldModel({
    search,
    controlledSearch,
    isResolving,
    focused,
    suggestionCount: suggestions.length,
    value,
    voiceSupported: voice.isSupported,
  });

  return (
    <section className="sr-rail-section sr-route-search">
      <form
        className="sr-input-group"
        onSubmit={(event) => {
          event.preventDefault();
          if (field.actionState === "submit") submitSearch();
        }}
      >
        <DestinationComboboxField
          suggestionsId={suggestionsId}
          suggestionsOpen={field.suggestionsOpen}
          highlightedIndex={highlightedIndex}
          displayValue={displayValue}
          busy={field.busy}
          wired={wired}
          suggestions={suggestions}
          onChange={setValue}
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
          onHighlight={setHighlightedIndex}
          onChoose={(suggestion) => void chooseSuggestion(suggestion)}
          onEscape={clearSuggestions}
        />
        <DestinationVoiceButton
          enabled={field.canUseVoice}
          listening={voice.isListening}
          onStart={voice.start}
        />
        <DestinationSubmitControl
          actionState={field.actionState}
          actionLabel={field.actionLabel}
          actionFilled={field.actionFilled}
          actionDisabled={field.actionDisabled}
          onClear={clearSearch}
          onStop={stopRoutePlanning}
        />
      </form>
      <DestinationSuggestions
        id={suggestionsId}
        open={field.suggestionsOpen}
        suggestions={suggestions}
        highlightedIndex={highlightedIndex}
        onHighlight={setHighlightedIndex}
        onSelect={(suggestion) => void chooseSuggestion(suggestion)}
      />
    </section>
  );
}
