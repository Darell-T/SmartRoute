"use client";

/**
 * Voice dictation for the chat composer, via the browser's Web Speech API.
 * This is a straight port of the recognition setup in
 * `components/smart-route/left-rail/route-view.tsx`'s `DestinationInput`
 * (the app's one other voice-input surface) into a reusable hook, so the
 * chat composer's mic behaves identically: single-shot (`continuous:
 * false`), final-results-only, English locale, and a graceful no-op when
 * the browser has no `SpeechRecognition`/`webkitSpeechRecognition`.
 *
 * Kept intentionally free of any UI — `isListening`/`isSupported` are the
 * whole public surface a mic button needs.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

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
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null;
}

// Capability detection never changes after mount, so this is modeled as a
// (non-subscribing) external store rather than `useEffect` + `setState` —
// `useSyncExternalStore` reads `getServerSnapshot` (null) for the first
// client render to match SSR output, then reconciles against the real
// browser value before paint, with no manual effect/hydration-mismatch
// dance needed.
function subscribeNever(): () => void {
  return () => {};
}

function getServerSnapshot(): SpeechRecognitionConstructor | null {
  return null;
}

export interface UseVoiceInputResult {
  /** False until the constructor lookup runs on mount (avoids an SSR/CSR
   *  mismatch); false forever on a browser with no speech API. */
  isSupported: boolean;
  isListening: boolean;
  /** Starts a single-shot recognition; a second call while already
   *  listening stops it instead (mirrors the rail's mic button). */
  start: () => void;
  stop: () => void;
}

export function useVoiceInput(onTranscript: (text: string) => void): UseVoiceInputResult {
  const recognitionCtor = useSyncExternalStore(subscribeNever, getSpeechRecognitionConstructor, getServerSnapshot);
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const onTranscriptRef = useRef(onTranscript);

  // Ref writes belong in an effect, not the render body — this keeps the
  // callback ref current for the next `start()` without touching it during
  // render.
  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  });

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort();
      recognitionRef.current = null;
    };
  }, []);

  function start(): void {
    if (!recognitionCtor || isListening) {
      recognitionRef.current?.stop();
      return;
    }

    const recognition = new recognitionCtor();
    recognition.lang = "en-US";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      const transcriptParts: string[] = [];
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index] ?? event.results.item(index);
        const alternative = result[0] ?? result.item(0);
        if (alternative?.transcript) transcriptParts.push(alternative.transcript);
      }
      const transcript = transcriptParts.join(" ").trim();
      if (transcript) onTranscriptRef.current(transcript);
    };
    recognition.onerror = () => {
      setIsListening(false);
      recognitionRef.current = null;
    };
    recognition.onend = () => {
      setIsListening(false);
      recognitionRef.current = null;
    };

    recognitionRef.current = recognition;
    setIsListening(true);
    try {
      recognition.start();
    } catch {
      setIsListening(false);
      recognitionRef.current = null;
    }
  }

  function stop(): void {
    recognitionRef.current?.stop();
  }

  return { isSupported: recognitionCtor !== null, isListening, start, stop };
}
