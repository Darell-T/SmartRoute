"use client";

/**
 * Chat tab theme (dark/light), independent of the rest of the app (which is
 * dark-only). Dark is the hard default; on first visit (no stored
 * preference) the OS `prefers-color-scheme: light` signal is honored once.
 * After that the rider's explicit choice always wins, persisted under
 * `sr-theme` in localStorage.
 *
 * Modeled as a tiny external store (`useSyncExternalStore`) rather than
 * `useState` + a mount `useEffect`: `getServerSnapshot` always returns
 * "dark", so server and first client render agree (no hydration
 * mismatch), and `getSnapshot` reads the real stored/OS preference — React
 * reconciles the two before paint with no manual effect needed. `toggleTheme`
 * writes through to localStorage and notifies subscribers directly.
 */

import { useSyncExternalStore } from "react";

const STORAGE_KEY = "sr-theme";

export type ChatTheme = "dark" | "light";

function isChatTheme(value: unknown): value is ChatTheme {
  return value === "dark" || value === "light";
}

function readStoredTheme(): ChatTheme | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isChatTheme(stored) ? stored : null;
  } catch {
    return null;
  }
}

function readInitialTheme(): ChatTheme {
  const stored = readStoredTheme();
  if (stored) return stored;
  try {
    if (window.matchMedia?.("(prefers-color-scheme: light)").matches) return "light";
  } catch {
    // matchMedia unavailable in some embedded webviews — fall through.
  }
  return "dark";
}

type Listener = () => void;
const listeners = new Set<Listener>();
// Lazily computed on first client read (readInitialTheme touches
// window/localStorage, so it must never run during SSR); cached after that
// so every hook instance and re-render agree without re-reading storage.
let cachedTheme: ChatTheme | null = null;

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): ChatTheme {
  if (cachedTheme === null) cachedTheme = readInitialTheme();
  return cachedTheme;
}

function getServerSnapshot(): ChatTheme {
  return "dark";
}

function setTheme(next: ChatTheme): void {
  cachedTheme = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Storage blocked/full: the toggle still works for this session, it
    // just won't survive a refresh. Not worth surfacing to the rider.
  }
  for (const listener of listeners) listener();
}

export interface UseChatThemeResult {
  theme: ChatTheme;
  toggleTheme: () => void;
}

export function useChatTheme(): UseChatThemeResult {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function toggleTheme(): void {
    setTheme(theme === "dark" ? "light" : "dark");
  }

  return { theme, toggleTheme };
}
