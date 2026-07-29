"use client";

// Mapbox destination autocomplete, extracted from LiveMapSearchCommand so
// the left rail's SearchBlock (the single search entry point now) can reuse
// the debounce / session-token / suggest / retrieve flow.

import {
  startTransition,
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";
import type { DestinationSelection } from "@/types";
import {
  createMapboxSearchSessionToken,
  retrieveMapboxSuggestion,
  suggestMapboxPlaces,
  type MapboxSearchSuggestion,
} from "@/lib/mapbox-search";

export interface UseDestinationSearchOptions {
  inputValue: string;
  /** Suggestions only fetch while true (e.g. the input is focused). */
  enabled: boolean;
  /** Suppresses fetching while a trip request is in flight. */
  isLoading?: boolean;
}

export interface DestinationSuggestionState {
  query: string;
  suggestions: MapboxSearchSuggestion[];
}

export class DestinationRequestGate {
  private generation = 0;

  begin(): number {
    this.generation += 1;
    return this.generation;
  }

  isCurrent(generation: number): boolean {
    return this.generation === generation;
  }
}

export function publishDestinationSearch(
  state: DestinationSuggestionState,
  gate: DestinationRequestGate,
  generation: number,
  query: string,
  suggestions: MapboxSearchSuggestion[],
): DestinationSuggestionState {
  return gate.isCurrent(generation)
    ? { ...state, query, suggestions }
    : state;
}

export function visibleDestinationSuggestions(
  state: DestinationSuggestionState,
  query: string,
  active: boolean,
): MapboxSearchSuggestion[] {
  return active && state.query === query ? state.suggestions : [];
}

export function useDestinationSearch({
  inputValue,
  enabled,
  isLoading = false,
}: UseDestinationSearchOptions) {
  const [suggestionState, setSuggestionState] = useState<DestinationSuggestionState>({
    query: "",
    suggestions: [],
  });
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [isResolving, setIsResolving] = useState(false);
  const deferredInput = useDeferredValue(inputValue);
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const sessionRef = useRef(createMapboxSearchSessionToken());
  const requestGateRef = useRef(new DestinationRequestGate());

  useEffect(() => {
    const requestId = requestGateRef.current.begin();
    const query = deferredInput.trim();
    const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    if (selectedLabel === query) {
      return;
    }
    if (!enabled || !token || query.length < 3 || isLoading) {
      return;
    }

    const controller = new AbortController();
    const id = setTimeout(async () => {
      try {
        const nextSuggestions = await suggestMapboxPlaces({
          query,
          accessToken: token,
          sessionToken: sessionRef.current,
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        startTransition(() => {
          setSuggestionState((state) => publishDestinationSearch(
            state,
            requestGateRef.current,
            requestId,
            query,
            nextSuggestions,
          ));
          setHighlightedIndex(0);
        });
      } catch {
        if (!controller.signal.aborted) {
          setSuggestionState((state) => publishDestinationSearch(
            state,
            requestGateRef.current,
            requestId,
            query,
            [],
          ));
        }
      }
    }, 180);

    return () => {
      clearTimeout(id);
      controller.abort();
    };
  }, [deferredInput, enabled, isLoading, selectedLabel]);

  /** Resolve a suggestion to coordinates; resets the billing session token
   *  and suppresses re-suggesting the chosen label. Null on failure. */
  async function choose(
    suggestion: MapboxSearchSuggestion,
  ): Promise<DestinationSelection | null> {
    const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    if (!token || isResolving) return null;
    let selection: DestinationSelection | null = null;
    try {
      setIsResolving(true);
      selection = await retrieveMapboxSuggestion({
        suggestion,
        accessToken: token,
        sessionToken: sessionRef.current,
      });
    } catch {
      selection = null;
    } finally {
      setIsResolving(false);
    }
    if (!selection) return null;
    setSelectedLabel(selection.label);
    setSuggestionState((state) => ({ ...state, suggestions: [] }));
    sessionRef.current = createMapboxSearchSessionToken();
    return selection;
  }

  function clearSuggestions() {
    setSuggestionState((state) => ({ ...state, suggestions: [] }));
  }

  /** Call when the user edits the input so the chosen label can re-suggest. */
  function markInputEdited() {
    setSelectedLabel(null);
  }

  /** Suppress suggestions for a query the user already acted on (free-text
   *  submit) -- otherwise the debounced fetch reopens the dropdown over the
   *  rail after the trip request is already in flight. */
  function markSelectedLabel(label: string) {
    setSelectedLabel(label);
    setSuggestionState((state) => ({ ...state, suggestions: [] }));
  }

  function resetSession() {
    sessionRef.current = createMapboxSearchSessionToken();
  }

  return {
    suggestions: visibleDestinationSuggestions(
      suggestionState,
      deferredInput.trim(),
      enabled && !isLoading && selectedLabel !== deferredInput.trim(),
    ),
    highlightedIndex,
    setHighlightedIndex,
    choose,
    isResolving,
    clearSuggestions,
    markInputEdited,
    markSelectedLabel,
    resetSession,
  };
}
