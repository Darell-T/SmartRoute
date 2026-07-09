"use client";

import {
  type KeyboardEvent,
  type PointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

export type MobileRailSheetState = "small" | "medium" | "full";

export type MobileRailAppState =
  | "idle"
  | "search"
  | "loading"
  | "result"
  | "navigating"
  | "error";

export type MobileRailSheetController = {
  mobileRailSheet: MobileRailSheetState;
  mobileRailSheetHeight: string;
  mobileRailSheetPixels: number;
  isMobileRailDragging: boolean;
  syncMobileRailAppState: (state: MobileRailAppState) => void;
  expandMobileRailSheet: () => void;
  handleMobileRailPointerDown: (event: PointerEvent<HTMLButtonElement>) => void;
  handleMobileRailPointerMove: (event: PointerEvent<HTMLButtonElement>) => void;
  handleMobileRailPointerUp: (event: PointerEvent<HTMLButtonElement>) => void;
  handleMobileRailPointerCancel: (
    event: PointerEvent<HTMLButtonElement>,
  ) => void;
  handleMobileRailKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
};

const MOBILE_RAIL_SHEET_HEIGHTS: Record<MobileRailSheetState, string> = {
  small: "calc(7.75rem + env(safe-area-inset-bottom))",
  medium: "calc(var(--visual-viewport-height, 100dvh) * 0.5)",
  full: "min(calc(var(--visual-viewport-height, 100dvh) * 0.9), calc(100dvh - max(0.75rem, env(safe-area-inset-top))))",
};

const MOBILE_RAIL_MIN_HEIGHT_PX = 104;
const MOBILE_RAIL_COMPACT_HEIGHT_PX = 124;

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

export function useMobileRailSheet(): MobileRailSheetController {
  const [mobileRailSheet, setMobileRailSheet] =
    useState<MobileRailSheetState>("small");
  const [mobileRailDragHeight, setMobileRailDragHeight] = useState<
    number | null
  >(null);
  const [isMobileRailDragging, setIsMobileRailDragging] = useState(false);
  const mobileRailDragRef = useRef({
    startY: 0,
    startHeight: 0,
    moved: false,
  });
  const mobileRailAppStateRef = useRef<MobileRailAppState>("idle");

  useEffect(() => {
    if (typeof window === "undefined") return;

    const setVisualViewportHeight = () => {
      const height = window.visualViewport?.height ?? window.innerHeight;
      document.documentElement.style.setProperty(
        "--visual-viewport-height",
        `${Math.round(height)}px`,
      );
    };

    setVisualViewportHeight();
    window.visualViewport?.addEventListener("resize", setVisualViewportHeight);
    window.visualViewport?.addEventListener("scroll", setVisualViewportHeight);
    window.addEventListener("resize", setVisualViewportHeight);

    return () => {
      window.visualViewport?.removeEventListener(
        "resize",
        setVisualViewportHeight,
      );
      window.visualViewport?.removeEventListener(
        "scroll",
        setVisualViewportHeight,
      );
      window.removeEventListener("resize", setVisualViewportHeight);
    };
  }, []);

  const getMobileRailSnapHeights = useCallback(() => {
    if (typeof window === "undefined") {
      return {
        small: MOBILE_RAIL_COMPACT_HEIGHT_PX,
        medium: 380,
        full: 684,
      };
    }

    const viewportHeight =
      window.visualViewport?.height || window.innerHeight || 760;
    const safeAreaTop = 12;
    const full = Math.max(
      MOBILE_RAIL_MIN_HEIGHT_PX,
      Math.round((viewportHeight - safeAreaTop) * 0.9),
    );
    const small = clamp(
      MOBILE_RAIL_COMPACT_HEIGHT_PX,
      MOBILE_RAIL_MIN_HEIGHT_PX,
      full,
    );
    const medium = clamp(Math.round(viewportHeight * 0.5), small, full);

    return {
      small,
      medium,
      full,
    };
  }, []);

  const getMobileRailSheetHeight = useCallback(
    (state: MobileRailSheetState) => getMobileRailSnapHeights()[state],
    [getMobileRailSnapHeights],
  );

  const toggleMobileRailSheet = useCallback(() => {
    setMobileRailSheet((current) => {
      if (current === "small") return "medium";
      if (current === "medium") return "full";
      return "medium";
    });
  }, []);

  const expandMobileRailSheet = useCallback(() => {
    setMobileRailDragHeight(null);
    setIsMobileRailDragging(false);
    setMobileRailSheet((current) => (current === "small" ? "medium" : current));
  }, []);

  const settleMobileRailSheet = useCallback(
    (height: number) => {
      const snaps = getMobileRailSnapHeights();
      const next = (
        Object.entries(snaps) as Array<[MobileRailSheetState, number]>
      ).reduce<[MobileRailSheetState, number]>(
        (best, current) => {
          const distance = Math.abs(height - current[1]);
          return distance < best[1] ? [current[0], distance] : best;
        },
        ["medium", Number.POSITIVE_INFINITY],
      )[0];

      setMobileRailSheet(next);
      setMobileRailDragHeight(null);
      setIsMobileRailDragging(false);
    },
    [getMobileRailSnapHeights],
  );

  const handleMobileRailPointerDown = useCallback(
    (event: PointerEvent<HTMLButtonElement>) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;

      const startHeight =
        mobileRailDragHeight ?? getMobileRailSheetHeight(mobileRailSheet);
      mobileRailDragRef.current = {
        startY: event.clientY,
        startHeight,
        moved: false,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
      setIsMobileRailDragging(true);
      setMobileRailDragHeight(startHeight);
    },
    [getMobileRailSheetHeight, mobileRailDragHeight, mobileRailSheet],
  );

  const handleMobileRailPointerMove = useCallback(
    (event: PointerEvent<HTMLButtonElement>) => {
      if (!isMobileRailDragging) return;

      const drag = mobileRailDragRef.current;
      const deltaY = drag.startY - event.clientY;
      if (Math.abs(deltaY) > 4) {
        drag.moved = true;
      }

      const snaps = getMobileRailSnapHeights();
      setMobileRailDragHeight(
        clamp(drag.startHeight + deltaY, snaps.small, snaps.full),
      );
    },
    [getMobileRailSnapHeights, isMobileRailDragging],
  );

  const handleMobileRailPointerUp = useCallback(
    (event: PointerEvent<HTMLButtonElement>) => {
      if (!isMobileRailDragging) return;

      try {
        event.currentTarget.releasePointerCapture(event.pointerId);
      } catch {
        // Pointer capture may already be gone after a cancelled gesture.
      }

      if (!mobileRailDragRef.current.moved) {
        toggleMobileRailSheet();
        setMobileRailDragHeight(null);
        setIsMobileRailDragging(false);
        return;
      }

      settleMobileRailSheet(
        mobileRailDragHeight ?? getMobileRailSheetHeight(mobileRailSheet),
      );
    },
    [
      getMobileRailSheetHeight,
      isMobileRailDragging,
      mobileRailDragHeight,
      mobileRailSheet,
      settleMobileRailSheet,
      toggleMobileRailSheet,
    ],
  );

  const handleMobileRailPointerCancel = useCallback(
    (event: PointerEvent<HTMLButtonElement>) => {
      if (!isMobileRailDragging) return;

      try {
        event.currentTarget.releasePointerCapture(event.pointerId);
      } catch {
        // Pointer capture may already be gone after a cancelled gesture.
      }

      setMobileRailDragHeight(null);
      setIsMobileRailDragging(false);
    },
    [isMobileRailDragging],
  );

  const handleMobileRailKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>) => {
      const order: MobileRailSheetState[] = ["small", "medium", "full"];
      const currentIndex = order.indexOf(mobileRailSheet);

      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMobileRailSheet(order[Math.min(currentIndex + 1, order.length - 1)]);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setMobileRailSheet(order[Math.max(currentIndex - 1, 0)]);
      } else if (event.key === "Home") {
        event.preventDefault();
        setMobileRailSheet("small");
      } else if (event.key === "End") {
        event.preventDefault();
        setMobileRailSheet("full");
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleMobileRailSheet();
      }
    },
    [mobileRailSheet, toggleMobileRailSheet],
  );

  const syncMobileRailAppState = useCallback((state: MobileRailAppState) => {
    if (mobileRailAppStateRef.current === state) return;
    mobileRailAppStateRef.current = state;
    setMobileRailDragHeight(null);
    setIsMobileRailDragging(false);

    if (state === "idle") return;
    if (state === "result") {
      setMobileRailSheet("medium");
      return;
    }
    if (state === "navigating") {
      setMobileRailSheet("small");
      return;
    }
    setMobileRailSheet("full");
  }, []);

  const mobileRailSheetHeight =
    mobileRailDragHeight === null
      ? MOBILE_RAIL_SHEET_HEIGHTS[mobileRailSheet]
      : `${Math.round(mobileRailDragHeight)}px`;
  const mobileRailSheetPixels = Math.round(
    mobileRailDragHeight ?? getMobileRailSheetHeight(mobileRailSheet),
  );

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.documentElement.style.setProperty(
      "--sr-mobile-sheet-px",
      `${mobileRailSheetPixels}px`,
    );
  }, [mobileRailSheetPixels]);

  return {
    mobileRailSheet,
    mobileRailSheetHeight,
    mobileRailSheetPixels,
    isMobileRailDragging,
    syncMobileRailAppState,
    expandMobileRailSheet,
    handleMobileRailPointerDown,
    handleMobileRailPointerMove,
    handleMobileRailPointerUp,
    handleMobileRailPointerCancel,
    handleMobileRailKeyDown,
  };
}
