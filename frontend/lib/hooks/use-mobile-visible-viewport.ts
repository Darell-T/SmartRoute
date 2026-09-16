"use client";

import { useEffect } from "react";

const VISIBLE_VIEWPORT_HEIGHT = "--visible-viewport-height";
const MOBILE_VISIBLE_HEIGHT = "--mobile-visible-height";
const MOBILE_VIEWPORT_OFFSET_TOP = "--mobile-viewport-offset-top";
const LEGACY_VISIBLE_HEIGHT = "--visual-viewport-height";

type ViewportWindow = Pick<
  Window,
  | "innerHeight"
  | "visualViewport"
  | "requestAnimationFrame"
  | "cancelAnimationFrame"
  | "addEventListener"
  | "removeEventListener"
>;

type ViewportRoot = Pick<HTMLElement, "style">;

/**
 * Keeps the mobile shell aligned to the viewport iOS is actually showing.
 * Layout consumes only the visible height; the grid naturally keeps the
 * composer above the keyboard without a second keyboard-offset translation.
 */
export function installMobileViewportVariables(
  targetWindow: ViewportWindow,
  root: ViewportRoot,
): () => void {
  const viewport = targetWindow.visualViewport;
  let animationFrame = 0;

  const commit = () => {
    animationFrame = 0;
    const visibleHeight = Math.max(
      1,
      Math.round(viewport?.height ?? targetWindow.innerHeight),
    );
    const offsetTop = Math.max(0, Math.round(viewport?.offsetTop ?? 0));

    root.style.setProperty(VISIBLE_VIEWPORT_HEIGHT, `${visibleHeight}px`);
    root.style.setProperty(MOBILE_VISIBLE_HEIGHT, `${visibleHeight}px`);
    root.style.setProperty(MOBILE_VIEWPORT_OFFSET_TOP, `${offsetTop}px`);
    // The map sheet already consumes this name. Keep one measurement owner
    // while its styles migrate to the explicit mobile viewport variable.
    root.style.setProperty(LEGACY_VISIBLE_HEIGHT, `${visibleHeight}px`);
  };

  const scheduleCommit = () => {
    if (animationFrame) return;
    animationFrame = targetWindow.requestAnimationFrame(commit);
  };

  commit();
  viewport?.addEventListener("resize", scheduleCommit, { passive: true });
  viewport?.addEventListener("scroll", scheduleCommit, { passive: true });
  targetWindow.addEventListener("resize", scheduleCommit, { passive: true });

  return () => {
    if (animationFrame) targetWindow.cancelAnimationFrame(animationFrame);
    viewport?.removeEventListener("resize", scheduleCommit);
    viewport?.removeEventListener("scroll", scheduleCommit);
    targetWindow.removeEventListener("resize", scheduleCommit);
  };
}

export function useMobileVisibleViewport(): void {
  useEffect(() => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return;
    }
    return installMobileViewportVariables(window, document.documentElement);
  }, []);
}
