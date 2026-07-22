"use client";

/**
 * Shared SmartRoute workspace theme.
 *
 * One provider owns the state for navigation, chat, the Route/Alerts panel,
 * and the map. The first render is dark on both server and client; the stored
 * or OS preference is restored on the first client frame, then explicit
 * choices persist under `sr-theme` in localStorage.
 */

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

const STORAGE_KEY = "sr-theme";

export type SmartRouteTheme = "dark" | "light";
/** @deprecated Use SmartRouteTheme for new shared-workspace consumers. */
export type ChatTheme = SmartRouteTheme;

function isSmartRouteTheme(value: unknown): value is SmartRouteTheme {
  return value === "dark" || value === "light";
}

function readInitialTheme(): SmartRouteTheme {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isSmartRouteTheme(stored)) return stored;
  } catch {
    // Storage may be unavailable in embedded/private browser contexts.
  }

  try {
    if (window.matchMedia?.("(prefers-color-scheme: light)").matches) return "light";
  } catch {
    // matchMedia may be unavailable in embedded webviews.
  }

  return "dark";
}

function persistTheme(next: SmartRouteTheme): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // The in-memory choice still works for this session.
  }
}

export interface UseChatThemeResult {
  theme: SmartRouteTheme;
  toggleTheme: () => void;
}

const SmartRouteThemeContext = createContext<UseChatThemeResult | null>(null);

export function SmartRouteThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<SmartRouteTheme>("dark");

  useEffect(() => {
    const initialTheme = readInitialTheme();
    if (initialTheme === "dark") return;
    const frame = window.requestAnimationFrame(() => setTheme(initialTheme));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((currentTheme) => {
      const nextTheme = currentTheme === "dark" ? "light" : "dark";
      persistTheme(nextTheme);
      return nextTheme;
    });
  }, []);

  const value = useMemo(() => ({ theme, toggleTheme }), [theme, toggleTheme]);

  return createElement(SmartRouteThemeContext.Provider, { value }, children);
}

export function useSmartRouteTheme(): UseChatThemeResult {
  const context = useContext(SmartRouteThemeContext);
  if (!context) {
    throw new Error("useSmartRouteTheme must be used within SmartRouteThemeProvider");
  }
  return context;
}

/** @deprecated Kept for compatibility with chat-specific imports. */
export const useChatTheme = useSmartRouteTheme;
