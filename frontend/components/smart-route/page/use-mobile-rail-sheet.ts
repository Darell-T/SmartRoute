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

// Detent order (collapsed → expanded), shared by keyboard stepping and the
// velocity-aware flick logic below.
const MOBILE_RAIL_DETENT_ORDER: MobileRailSheetState[] = [
  "small",
  "medium",
  "full",
];

// A flick advances one detent in the flick direction regardless of which
// detent is nearest — this is what makes the sheet feel like an iOS sheet
// rather than a web slider. Below this speed, release settles to nearest.
const MOBILE_RAIL_FLICK_VELOCITY_THRESHOLD_PX_MS = 0.5;
// Velocity is measured over a short trailing window of recent pointer
// samples so a fast flick reads correctly even if the pointer decelerated
// earlier in a longer drag.
const MOBILE_RAIL_FLICK_WINDOW_MS = 90;
// Samples older than this are dropped on each move so the buffer can't
// grow unbounded over a long, slow drag.
const MOBILE_RAIL_SAMPLE_RETENTION_MS = 250;

type MobileRailPointerSample = { time: number; y: number };

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

// Signed px/ms velocity over the trailing window, using the same sign
// convention as the drag delta (startY - currentY): positive means the
// pointer moved up (toward expanding the sheet), negative means down.
function computeMobileRailFlickVelocity(
  samples: MobileRailPointerSample[],
  finalY: number,
  finalTime: number,
): number {
  if (samples.length === 0) return 0;
  const cutoff = finalTime - MOBILE_RAIL_FLICK_WINDOW_MS;
  const reference =
    samples.find((sample) => sample.time >= cutoff) ??
    samples[samples.length - 1];
  const elapsed = finalTime - reference.time;
  if (elapsed <= 0) return 0;
  return (reference.y - finalY) / elapsed;
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
  // Recent (timestamp, y) pointer samples for the current drag — used to
  // compute release velocity for flick-to-advance settling.
  const mobileRailPointerSamplesRef = useRef<MobileRailPointerSample[]>([]);

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
      mobileRailPointerSamplesRef.current = [
        { time: Date.now(), y: event.clientY },
      ];
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

      const now = Date.now();
      const samples = mobileRailPointerSamplesRef.current;
      samples.push({ time: now, y: event.clientY });
      const retainAfter = now - MOBILE_RAIL_SAMPLE_RETENTION_MS;
      while (samples.length > 1 && samples[0].time < retainAfter) {
        samples.shift();
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
        mobileRailPointerSamplesRef.current = [];
        toggleMobileRailSheet();
        setMobileRailDragHeight(null);
        setIsMobileRailDragging(false);
        return;
      }

      const velocity = computeMobileRailFlickVelocity(
        mobileRailPointerSamplesRef.current,
        event.clientY,
        Date.now(),
      );
      mobileRailPointerSamplesRef.current = [];

      // A fast flick advances one detent from wherever the sheet currently
      // is, in the flick direction — regardless of which detent the drag
      // ended up nearest to. Anything slower falls back to nearest-detent
      // settling, same as before.
      if (Math.abs(velocity) > MOBILE_RAIL_FLICK_VELOCITY_THRESHOLD_PX_MS) {
        const direction = velocity > 0 ? 1 : -1;
        const currentIndex = MOBILE_RAIL_DETENT_ORDER.indexOf(mobileRailSheet);
        const nextIndex = clamp(
          currentIndex + direction,
          0,
          MOBILE_RAIL_DETENT_ORDER.length - 1,
        );
        setMobileRailSheet(MOBILE_RAIL_DETENT_ORDER[nextIndex]);
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

      mobileRailPointerSamplesRef.current = [];
      setMobileRailDragHeight(null);
      setIsMobileRailDragging(false);
    },
    [isMobileRailDragging],
  );

  const handleMobileRailKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>) => {
      const order = MOBILE_RAIL_DETENT_ORDER;
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
