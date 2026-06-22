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

export function useDestinationSearch({
  inputValue,
  enabled,
  isLoading = false,
}: UseDestinationSearchOptions) {
  const [suggestions, setSuggestions] = useState<MapboxSearchSuggestion[]>([]);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [isResolving, setIsResolving] = useState(false);
  const deferredInput = useDeferredValue(inputValue);
  const selectedLabelRef = useRef<string | null>(null);
  const sessionRef = useRef(createMapboxSearchSessionToken());

  useEffect(() => {
    const query = deferredInput.trim();
    const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    if (selectedLabelRef.current === query) {
      setSuggestions([]);
      return;
    }
    if (!enabled || !token || query.length < 3 || isLoading) {
      setSuggestions([]);
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
        startTransition(() => {
          setSuggestions(nextSuggestions);
          setHighlightedIndex(0);
        });
      } catch {
        if (!controller.signal.aborted) {
          setSuggestions([]);
        }
      }
    }, 180);

    return () => {
      clearTimeout(id);
      controller.abort();
    };
  }, [deferredInput, enabled, isLoading]);

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
    selectedLabelRef.current = selection.label;
    setSuggestions([]);
    sessionRef.current = createMapboxSearchSessionToken();
    return selection;
  }

  function clearSuggestions() {
    setSuggestions([]);
  }

  /** Call when the user edits the input so the chosen label can re-suggest. */
  function markInputEdited() {
    selectedLabelRef.current = null;
  }

  /** Suppress suggestions for a query the user already acted on (free-text
   *  submit) -- otherwise the debounced fetch reopens the dropdown over the
   *  rail after the trip request is already in flight. */
  function markSelectedLabel(label: string) {
    selectedLabelRef.current = label;
    setSuggestions([]);
  }

  function resetSession() {
    sessionRef.current = createMapboxSearchSessionToken();
  }

  return {
    suggestions,
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
