"use client";

import {
  type KeyboardEvent,
  type PointerEvent,
  useCallback,
  useRef,
  useState,
} from "react";

export type MobileRailSheetState = "hidden" | "peek" | "half" | "full";

export type MobileRailSheetController = {
  mobileRailSheet: MobileRailSheetState;
  mobileRailSheetHeight: string;
  isMobileRailDragging: boolean;
  handleMobileRailPointerDown: (event: PointerEvent<HTMLButtonElement>) => void;
  handleMobileRailPointerMove: (event: PointerEvent<HTMLButtonElement>) => void;
  handleMobileRailPointerUp: (event: PointerEvent<HTMLButtonElement>) => void;
  handleMobileRailPointerCancel: (
    event: PointerEvent<HTMLButtonElement>,
  ) => void;
  handleMobileRailKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
};

const MOBILE_RAIL_SHEET_HEIGHTS: Record<MobileRailSheetState, string> = {
  hidden: "3.25rem",
  peek: "min(42dvh, 23rem)",
  half: "62dvh",
  full: "calc(100dvh - max(0.75rem, env(safe-area-inset-top)))",
};

const MOBILE_RAIL_MIN_HEIGHT_PX = 52;
const MOBILE_RAIL_FULL_MARGIN_PX = 10;

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

export function useMobileRailSheet(): MobileRailSheetController {
  const [mobileRailSheet, setMobileRailSheet] =
    useState<MobileRailSheetState>("peek");
  const [mobileRailDragHeight, setMobileRailDragHeight] = useState<
    number | null
  >(null);
  const [isMobileRailDragging, setIsMobileRailDragging] = useState(false);
  const mobileRailDragRef = useRef({
    startY: 0,
    startHeight: 0,
    moved: false,
  });

  const getMobileRailSnapHeights = useCallback(() => {
    if (typeof window === "undefined") {
      return {
        hidden: MOBILE_RAIL_MIN_HEIGHT_PX,
        peek: 320,
        half: 480,
        full: 680,
      };
    }

    const viewportHeight = window.innerHeight || 760;
    const full = Math.max(
      MOBILE_RAIL_MIN_HEIGHT_PX,
      viewportHeight - MOBILE_RAIL_FULL_MARGIN_PX,
    );
    const peek = clamp(Math.round(viewportHeight * 0.42), 256, full);
    const half = clamp(Math.round(viewportHeight * 0.62), peek, full);

    return {
      hidden: MOBILE_RAIL_MIN_HEIGHT_PX,
      peek,
      half,
      full,
    };
  }, []);

  const getMobileRailSheetHeight = useCallback(
    (state: MobileRailSheetState) => getMobileRailSnapHeights()[state],
    [getMobileRailSnapHeights],
  );

  const toggleMobileRailSheet = useCallback(() => {
    setMobileRailSheet((current) => {
      if (current === "hidden") return "peek";
      if (current === "full") return "peek";
      return "full";
    });
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
        ["peek", Number.POSITIVE_INFINITY],
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
        clamp(drag.startHeight + deltaY, snaps.hidden, snaps.full),
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
      const order: MobileRailSheetState[] = ["hidden", "peek", "half", "full"];
      const currentIndex = order.indexOf(mobileRailSheet);

      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMobileRailSheet(order[Math.min(currentIndex + 1, order.length - 1)]);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setMobileRailSheet(order[Math.max(currentIndex - 1, 0)]);
      } else if (event.key === "Home") {
        event.preventDefault();
        setMobileRailSheet("hidden");
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

  const mobileRailSheetHeight =
    mobileRailDragHeight === null
      ? MOBILE_RAIL_SHEET_HEIGHTS[mobileRailSheet]
      : `${Math.round(mobileRailDragHeight)}px`;

  return {
    mobileRailSheet,
    mobileRailSheetHeight,
    isMobileRailDragging,
    handleMobileRailPointerDown,
    handleMobileRailPointerMove,
    handleMobileRailPointerUp,
    handleMobileRailPointerCancel,
    handleMobileRailKeyDown,
  };
}
